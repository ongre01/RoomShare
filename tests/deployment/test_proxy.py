"""Real unprivileged Nginx + TLS + Uvicorn. No host config, DNS or auth overrides."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "room.example.com"
ADMIN = "admin-room.example.com"


@pytest.fixture(scope="module")
def pki():
    with tempfile.TemporaryDirectory(prefix="roomshare-test-ca-") as temp:
        root = Path(temp)

        def openssl(*args):
            subprocess.run(["openssl", *args], cwd=root, capture_output=True, check=True, timeout=10)

        key_options = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-noenc"]
        for ca in ("server-ca", "admin-ca"):
            openssl("req", "-x509", *key_options, "-days", "3", "-subj", f"/CN={ca}",
                    "-keyout", ca + ".key", "-out", ca + ".pem",
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        (root / "index").touch()
        (root / "issued").mkdir()
        (root / "serial").write_text("01\n")
        (root / "ca.cnf").write_text(
            "[ca]\ndefault_ca=admin\n[admin]\ncertificate=admin-ca.pem\nprivate_key=admin-ca.key\n"
            "database=index\nnew_certs_dir=issued\nserial=serial\ndefault_md=sha256\n"
            "default_days=2\ndefault_crl_days=2\npolicy=names\n[names]\ncommonName=supplied\n"
            "[client]\nbasicConstraints=CA:FALSE\nextendedKeyUsage=clientAuth\n"
        )
        for index, name in enumerate(("server", "valid", "foreign", "expired", "revoked"), 1):
            openssl("req", "-new", *key_options, "-subj", f"/CN={name}", "-keyout", name + ".key",
                    "-out", name + ".csr")
            if name in ("server", "foreign"):
                ext = ("extendedKeyUsage=serverAuth\n" + f"subjectAltName=DNS:{PUBLIC},DNS:{ADMIN}\n"
                       if name == "server" else "extendedKeyUsage=clientAuth\n")
                (root / "ext").write_text("basicConstraints=CA:FALSE\n" + ext)
                openssl("x509", "-req", "-in", name + ".csr", "-CA", "server-ca.pem",
                        "-CAkey", "server-ca.key", "-set_serial", str(index), "-days", "2",
                        "-extfile", "ext", "-out", name + ".pem")
            else:
                validity = ["-startdate", "200101000000Z", "-enddate", "200102000000Z"] if name == "expired" else []
                openssl("ca", "-batch", "-notext", "-config", "ca.cnf", "-extensions", "client",
                        "-in", name + ".csr", "-out", name + ".pem", *validity)
        openssl("ca", "-config", "ca.cnf", "-revoke", "revoked.pem")
        openssl("ca", "-config", "ca.cnf", "-gencrl", "-out", "admin-ca.crl")
        yield root


@contextmanager
def backend(settings, seen):
    app = create_app(settings=settings, clock=lambda: datetime(2026, 11, 30, 15, tzinfo=timezone.utc))

    async def observed(scope, receive, send):
        if scope["type"] == "http":
            headers = scope["headers"]
            # Keep only booleans/counts, never record header values or bodies.
            seen[scope["path"]] = {
                "forwarded": any(k == b"forwarded" or k == b"x-real-ip" or k.startswith(b"x-forwarded-")
                                 for k, _ in headers),
                "signals": sum(k == b"x-roomshare-admin-verified" for k, _ in headers),
                "secrets": sum(k == b"x-roomshare-proxy-secret" for k, _ in headers),
            }
        await app(scope, receive, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(observed, proxy_headers=False, access_log=False, log_config=None))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(0.025)
            assert server.started
            yield listener.getsockname()[1]
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


def local_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def proxy(migrated_db, pki):
    secret = secrets.token_urlsafe(32)
    settings = Settings(db_path=migrated_db, public_host=PUBLIC, admin_host=ADMIN,
                        admin_access="proxy_mtls", admin_proxy_secret=secret)
    seen = {}
    with backend(settings, seen) as upstream, tls_proxy(upstream, pki, secret) as (request, http_port):
        yield request, seen, http_port


@contextmanager
def tls_proxy(upstream, pki, secret):
    with tempfile.TemporaryDirectory(prefix="roomshare-nginx-") as temp:
        work = Path(temp)
        http_port, tls_port = local_port(), local_port()
        while tls_port == http_port:
            tls_port = local_port()
        (work / "private-header.conf").write_text(f'proxy_set_header X-RoomShare-Proxy-Secret "{secret}";\n')
        template = ROOT / "deploy/nginx"
        site = (template / "roomshare.conf").read_text()
        site = re.sub(r"^\s*listen \[::\]:.*?;", "", site, flags=re.MULTILINE)
        site = (site.replace("listen 443 ", f"listen 127.0.0.1:{tls_port} ")
                    .replace("listen 80 ", f"listen 127.0.0.1:{http_port} ")
                    .replace(":443\"", f":{tls_port}\"")
                    .replace("/etc/roomshare/admin-proxy-header.conf", str(work / "private-header.conf"))
                    .replace("/etc/nginx/snippets/roomshare-proxy.conf", str(work / "common.conf"))
                    .replace("/etc/roomshare/tls/", str(pki) + "/")
                    .replace("127.0.0.1:8000", f"127.0.0.1:{upstream}"))
        common = (template / "roomshare-proxy.conf").read_text()
        (work / "common.conf").write_text(
            common.replace("/var/log/nginx/roomshare-access.log", str(work / "access.log"))
                  .replace("/etc/roomshare/tls/server-chain.pem", str(pki / "server.pem"))
                  .replace("/etc/roomshare/tls/server.key", str(pki / "server.key"))
        )
        (work / "nginx.conf").write_text(
            "worker_processes 1;\npid nginx.pid;\nerror_log error.log;\nevents {}\nhttp {\n"
            "access_log off;\n" + "".join(f"{name}_temp_path {name};\n" for name in
                                          ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")) + site + "\n}\n"
        )
        cmd = ["nginx", "-p", str(work), "-c", str(work / "nginx.conf"), "-e", str(work / "startup.log")]
        check = subprocess.run([*cmd, "-t"], capture_output=True, timeout=5)
        assert check.returncode == 0, check.stderr.decode()

        def request(host, path, *, cert=None, method="GET", headers=(), body=None, http_host=None):
            ctx = ssl.create_default_context(cafile=str(pki / "server-ca.pem"))
            if cert:
                ctx.load_cert_chain(pki / (cert + ".pem"), pki / (cert + ".key"))
            with socket.create_connection(("127.0.0.1", tls_port), timeout=3) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    with closing(http.client.HTTPConnection(host)) as client:
                        client.sock = tls
                        client.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                        client.putheader("Host", http_host if http_host is not None else host)
                        for key, value in headers:
                            client.putheader(key, value)
                        if body is not None:
                            client.putheader("Content-Type", "application/json")
                            client.putheader("Content-Length", str(len(body)))
                        client.endheaders(body)
                        response = client.getresponse()
                        return response.status, response.headers, response.read()

        with subprocess.Popen([*cmd, "-g", "daemon off; master_process off;"]) as nginx:
            try:
                for _ in range(100):
                    assert nginx.poll() is None, "Private nginx exited during startup"
                    try:
                        status, _, body = request(PUBLIC, "/healthz")
                        assert status == 200 and json.loads(body) == {"status": "ok"}
                        break
                    except ConnectionRefusedError:
                        time.sleep(0.03)
                else:
                    pytest.fail("Private nginx readiness timed out")
                yield request, http_port
                log = (work / "access.log").read_text().splitlines()
                assert log and all(re.fullmatch(r"[1-5]\d{2} \d+\.\d+", line) for line in log)
            finally:
                nginx.terminate()
                nginx.wait(timeout=5)


def test_tc008_04_public_https_assets_calendar_and_admin_denial(proxy):
    request, seen, _ = proxy
    status, _, page = request(PUBLIC, "/")
    assert status == 200 and b'http://' not in page
    for asset in ("/static/css/main.css", "/static/js/public.js"):
        assert asset.encode() in page and request(PUBLIC, asset)[0] == 200
    assert request(PUBLIC, "/api/calendar?month=2026-12")[0] == 200
    for path in ("/admin", "/api/admin", "/api/admin/csrf", "/api/admin/logs", "/api/admin/reservations",
                 "/api/admin/blocked-dates", "/api/admin/settings"):
        status, headers, body = request(PUBLIC, path)
        assert status == 403 and json.loads(body)["code"] == "ADMIN_FORBIDDEN"
        assert headers["Cache-Control"] == "no-store" and path not in seen


def test_tc008_05_only_current_unrevoked_client_from_admin_ca(proxy):
    request, _, _ = proxy
    for cert in (None, "foreign", "expired", "revoked"):
        status, _, body = request(ADMIN, "/api/admin/settings", cert=cert)
        assert status in (400, 403) and b"booking_horizon_days" not in body
    status, headers, body = request(ADMIN, "/api/admin/settings", cert="valid")
    assert status == 200 and json.loads(body) == {"booking_horizon_days": 90}
    assert headers["Cache-Control"] == "no-store"
    for path in ("/admin", "/static/js/admin.js", "/api/calendar?month=2026-12"):
        assert request(ADMIN, path, cert="valid")[0] == 200


def test_tc008_06_only_proxy_supplies_trust_headers(proxy):
    request, seen, _ = proxy
    untrusted = [("Forwarded", "for=client"), ("X-Forwarded-Host", ADMIN), ("X-Forwarded-For", "127.0.0.1"),
                 ("X-Forwarded-Proto", "https"), ("X-Real-IP", "127.0.0.1"),
                 ("X-RoomShare-Admin-Verified", "SUCCESS"), ("X-RoomShare-Proxy-Secret", "client-input")]
    untrusted += untrusted[-2:]
    assert request(PUBLIC, "/healthz", headers=untrusted)[0] == 200
    assert seen["/healthz"] == {"forwarded": False, "signals": 0, "secrets": 0}
    assert request(PUBLIC, "/admin", headers=untrusted)[0] == 403
    assert request(ADMIN, "/admin", headers=untrusted)[0] in (400, 403)
    assert "/admin" not in seen
    assert request(ADMIN, "/admin", cert="valid", headers=untrusted)[0] == 200
    assert seen["/admin"] == {"forwarded": False, "signals": 1, "secrets": 1}


def test_tc008_07_default_host_sni_and_admin_prefixes(proxy):
    request, seen, http_port = proxy
    with pytest.raises(ssl.SSLError, match="UNRECOGNIZED_NAME"):
        request("unknown.example.com", "/")
    for sni, host in ((PUBLIC, ADMIN), (ADMIN, PUBLIC), (PUBLIC, "unknown.example.com")):
        assert request(sni, "/admin", http_host=host, cert="valid")[0] in (400, 403, 421)
    for path in ("/admin/", "/admin/child", "/api/admin/", "/api/admin/settings/"):
        assert request(PUBLIC, path)[0] == 403 and path not in seen
    assert request(PUBLIC, "/", headers=[("Host", ADMIN)])[0] == 400
    with closing(http.client.HTTPConnection("127.0.0.1", http_port, timeout=3)) as client:
        client.request("GET", "/admin", headers={"Host": ADMIN})
        assert client.getresponse().status == 403


def test_tc008_09_body_rate_no_store_and_origin_csrf_through_proxy(proxy):
    request, _, _ = proxy
    status, headers, body = request(PUBLIC, "/api/reservations", method="POST",
                                    body=b'{"name":"QA_USER_A","dates":["2026-12-02"]}')
    assert status == 201 and json.loads(body)["cancel_token"] and headers["Cache-Control"] == "no-store"
    status, headers, body = request(PUBLIC, "/api/reservations", method="POST", body=b"x" * 16385)
    assert status == 413 and json.loads(body)["code"] == "REQUEST_TOO_LARGE"
    assert headers["Cache-Control"] == "no-store"
    token = json.loads(request(ADMIN, "/api/admin/csrf", cert="valid")[2])["csrf_token"]
    write = dict(cert="valid", method="PATCH", body=b'{"booking_horizon_days":45}')
    assert request(ADMIN, "/api/admin/settings", **write)[0] == 403
    status, headers, body = request(ADMIN, "/api/admin/settings", **write,
                                    headers=[("Origin", "https://" + ADMIN), ("X-CSRF-Token", token)])
    assert status == 200 and json.loads(body)["booking_horizon_days"] == 45 and headers["Cache-Control"] == "no-store"
    for _ in range(40):
        status, headers, body = request(PUBLIC, "/healthz")
        if status == 429:
            assert json.loads(body)["code"] == "RATE_LIMITED"
            assert int(headers["Retry-After"]) >= 1 and headers["Cache-Control"] == "no-store"
            break
        assert status == 200
    else:
        pytest.fail("Configured nginx burst did not produce 429")
