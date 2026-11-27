"""TC-001-09: verify both lock files in disposable Python environments.

Run from any directory with Python 3.11+ on Ubuntu. This intentionally stays
outside pytest because it installs dependencies and runs the complete suite.
Only loopback HTTP and temporary configuration/data paths are used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env: dict[str, str], timeout: int = 180) -> None:
    """Keep subprocess failures visible, including their original exit code."""
    print(f"$ {shlex.join(command)}", flush=True)
    result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, timeout=timeout)
    print(f"exit={result.returncode}", flush=True)
    result.check_returncode()


def fresh_environment(path: Path, lock: str, env: dict[str, str]) -> Path:
    run([sys.executable, "-m", "venv", str(path)], env=env)
    python = path / "bin" / "python"
    run(
        [
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "--quiet", "-r", str(PROJECT_ROOT / lock),
        ],
        env=env,
        timeout=300,
    )
    run([str(python), "-m", "pip", "check"], env=env)
    run(
        [
            str(python), "-c",
            "import platform, sqlite3; "
            "print('Python:', platform.python_version()); "
            "print('SQLite:', sqlite3.sqlite_version)",
        ],
        env=env,
    )
    return python


def verify_http(python: Path, temp_root: Path, env: dict[str, str]) -> None:
    """Start the real server on a reserved loopback socket, then reap it."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    process = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = [
            str(python), "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", "127.0.0.1", "--fd", str(listener.fileno()),
            "--no-access-log", "--log-level", "warning",
        ]
        print(f"$ {shlex.join(command)} (reserved loopback port {port})", flush=True)
        with (temp_root / "uvicorn.log").open("w+") as server_log:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=temp_root,
                    env=env,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                    pass_fds=(listener.fileno(),),
                )
                # Only the child now holds this listening socket.
                listener.close()
                base_url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"uvicorn exited before readiness: {process.returncode}"
                        )
                    try:
                        with opener.open(base_url + "/healthz", timeout=1) as response:
                            if response.status != 200:
                                raise AssertionError("healthz did not return 200")
                            if json.load(response) != {"status": "ok"}:
                                raise AssertionError("healthz was not minimal JSON")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("uvicorn readiness timed out") from None
                        time.sleep(0.1)

                with opener.open(base_url + "/", timeout=2) as response:
                    html = response.read().decode("utf-8")
                    if response.status != 200 or 'name="viewport"' not in html:
                        raise AssertionError("root template/viewport check failed")
                with opener.open(base_url + "/static/css/main.css", timeout=2) as response:
                    if response.status != 200 or not response.read():
                        raise AssertionError("static stylesheet check failed")
                for path in ("/admin", "/api/admin"):
                    try:
                        opener.open(base_url + path, timeout=2).close()
                    except urllib.error.HTTPError as error:
                        with error:
                            if (error.code != 403 or json.load(error).get("code") != "ADMIN_FORBIDDEN"
                                    or error.headers.get("Cache-Control") != "no-store"):
                                raise AssertionError(f"{path} did not enforce administrator denial") from None
                    else:
                        raise AssertionError(f"{path} was unexpectedly accessible")
                print("HTTP PASS: healthz=200, root/CSS=200, admin/API=403 no-store", flush=True)
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                            raise RuntimeError("uvicorn required forced termination")
                    if process.poll() is None:
                        raise AssertionError("uvicorn process was not reaped")
                    print(f"uvicorn cleanup: exit={process.returncode}", flush=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise AssertionError("loopback listener remained after uvicorn shutdown")
    if Path(env["ROOMSHARE_DB_PATH"]).exists():
        raise AssertionError("bootstrap unexpectedly created a DB")
    print("Cleanup PASS: no server process/listener or DB remains", flush=True)


def main() -> int:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    with tempfile.TemporaryDirectory(prefix="roomshare-bootstrap-") as temporary:
        temp_root = Path(temporary)
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ROOMSHARE_DB_PATH": str(temp_root / "data" / "unused.db"),
            "ROOMSHARE_TIMEZONE": "Asia/Seoul",
            "ROOMSHARE_PUBLIC_HOST": "public.test",
            "ROOMSHARE_ADMIN_HOST": "admin.test",
            "ROOMSHARE_BOOKING_HORIZON_DAYS": "90",
            "ROOMSHARE_ADMIN_ACCESS": "deny",
        })
        # A caller's virtualenv/Python bootstrap settings must not affect these venvs.
        env.pop("PYTHONHOME", None)
        env.pop("VIRTUAL_ENV", None)
        runtime = fresh_environment(temp_root / "runtime", "requirements.lock", env)
        verify_http(runtime, temp_root, env)
        development = fresh_environment(
            temp_root / "development", "requirements-dev.lock", env
        )
        # RS-005 includes real browser tests in the full suite. Browser binaries
        # are a development-only prerequisite (never part of the runtime venv).
        run([str(development), "-m", "playwright", "install", "chromium"],
            env=env, timeout=300)
        run(
            [str(development), "-m", "pytest", "-q", "tests/test_health.py"],
            env=env,
        )
        run([str(development), "-m", "pytest", "-q"], env=env, timeout=1800)
    print("TC-001-09 PASS: runtime/development venvs verified and removed", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
