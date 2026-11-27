"""RS-009 recovery and bounded load on disposable loopback servers, never a deployment URL."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from math import ceil
import os
from pathlib import Path
import threading
import time

import httpx

from app.db import connect
from app.services.admin import block_dates, update_settings
from scripts.snapshot import backup, restore
from tests.db_helpers import snapshot


def calendar(client):
    response = client.get("/api/calendar", params={"month": "2026-12"})
    assert response.status_code == 200
    return response.json()


def test_tc009_07_restart_and_serve_the_restored_snapshot(migrated_db, tmp_path, server_for_db):
    def clock():
        return datetime(2026, 11, 30, 15, tzinfo=timezone.utc)

    with server_for_db(migrated_db) as server, httpx.Client(base_url=server.url, trust_env=False) as client:
        response = client.post("/api/reservations", json={"name": "QA_USER_A", "dates": ["2026-12-02"]})
        assert response.status_code == 201
        credentials = {key: response.json()[key] for key in ("id", "cancel_token")}
        server.sensitive_values.append(credentials["cancel_token"])
        block_dates(migrated_db, dates=["2026-12-03"], clock=clock)
        update_settings(migrated_db, booking_horizon_days=45, clock=clock)
        expected = calendar(client)
        states = {item["date"]: item["status"] for item in expected["dates"]}
        assert (states["2026-12-02"], states["2026-12-03"]) == ("RESERVED", "BLOCKED")
        assert expected["max_date"] == "2027-01-14"
        with closing(connect(migrated_db)) as db:
            original = snapshot(db)

    with server_for_db(migrated_db) as restarted, httpx.Client(base_url=restarted.url, trust_env=False) as client:
        assert calendar(client) == expected
        with closing(connect(migrated_db)) as db:
            assert bool(snapshot(db) == original)
        archive = backup(migrated_db, tmp_path / "archives", days=0)

    recovered = restore(archive, tmp_path / "rehearsal")
    with server_for_db(recovered) as server, httpx.Client(base_url=server.url, trust_env=False) as client:
        server.sensitive_values.append(credentials["cancel_token"])
        assert calendar(client) == expected
        with closing(connect(recovered)) as db:
            assert bool(snapshot(db) == original)
        assert client.post("/api/reservations/cancel", json=credentials).status_code == 200
        states = {item["date"]: item["status"] for item in calendar(client)["dates"]}
        assert (states["2026-12-02"], states["2026-12-03"]) == ("AVAILABLE", "BLOCKED")
    with closing(connect(migrated_db)) as db:
        assert bool(snapshot(db) == original)


def process_usage(pid):
    proc = Path("/proc") / str(pid)
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    cpu_seconds = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    rss_kib = next(int(line.split()[1]) for line in (proc / "status").read_text().splitlines()
                   if line.startswith("VmRSS:"))
    return cpu_seconds, rss_kib


def measure(server, label, concurrency, samples, request):
    barrier = threading.Barrier(concurrency, timeout=10)

    def worker(index):
        if index < concurrency:
            barrier.wait()
        start = time.perf_counter()
        response = request(index)
        return response.status_code, (time.perf_counter() - start) * 1000

    cpu_before, _ = process_usage(server.process.pid)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(worker, range(samples)))
    wall = time.perf_counter() - start
    cpu_after, rss = process_usage(server.process.pid)
    codes = Counter(code for code, _ in results)
    ordered = sorted(duration for _, duration in results)
    p50, p95, p99 = (ordered[ceil(samples * percentile) - 1] for percentile in (0.50, 0.95, 0.99))
    print(f"{label} c={concurrency} n={samples} wall={wall:.3f}s "
          f"p50/p95/p99_ms={p50:.2f}/{p95:.2f}/{p99:.2f} codes={dict(sorted(codes.items()))} "
          f"cpu={100 * (cpu_after - cpu_before) / wall:.1f}% rss={rss}KiB db={server.db_path.stat().st_size}B")
    return codes


def test_tc009_03_09_local_calendar_load_and_ten_booking_contenders(live_server):
    with httpx.Client(base_url=live_server.url, trust_env=False, timeout=10) as client:
        window = calendar(client)
        first = date.fromisoformat(window["min_date"])
        days = [(first + timedelta(days=n)).isoformat() for n in range(90)]
        assert days[-1] == window["max_date"]
        seed = client.post("/api/reservations", json={"name": "QA_SEED", "dates": days[:-1]})
        assert seed.status_code == 201
        cpu, rss = process_usage(live_server.process.pid)
        idle_start = time.perf_counter()
        time.sleep(0.25)
        idle = time.perf_counter() - idle_start
        idle_cpu, _ = process_usage(live_server.process.pid)
        print(f"idle wall={idle:.3f}s cpu={100 * (idle_cpu - cpu) / idle:.1f}% "
              f"rss={rss}KiB db={live_server.db_path.stat().st_size}B")

        months = sorted({day[:7] for day in days})
        for concurrency in (1, 5, 10):
            codes = measure(live_server, "calendar", concurrency, 30,
                            lambda i: client.get("/api/calendar", params={"month": months[i % len(months)]}))
            assert codes == {200: 30}
        def contend(index):
            result = client.post("/api/reservations", json={"name": f"QA_RACE_{index}", "dates": days[-1:]})
            if result.status_code == 409:
                assert result.json()["code"] == "DATE_CONFLICT" and result.json()["dates"] == days[-1:]
            return result

        codes = measure(live_server, "reservation", 10, 10, contend)
        assert codes == {201: 1, 409: 9}

    with closing(connect(live_server.db_path)) as db:
        assert db.execute("SELECT count(*) FROM reservations WHERE status='CONFIRMED'").fetchone()[0] == 2
        assert [row[0] for row in db.execute("SELECT date FROM reserved_dates ORDER BY date")] == days
        assert [row[0] for row in db.execute("SELECT action FROM reservation_logs ORDER BY id")] == ["CREATE"] * 2
