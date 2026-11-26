"""Real loopback HTTP and file SQLite for public browser tests.

No browser/DB fallback or skip: install the documented development dependencies.
Only the browser context is reused within a test; every test gets a fresh DB.
"""

from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright
import pytest

from app.db import connect
from app.migrations import migrate
from tests.db_helpers import assert_db_invariants


ROOT = Path(__file__).resolve().parents[2]
SERVER_CODE = """
from datetime import datetime, timezone
import sys
import uvicorn
from app.config import Settings
from app.main import create_app
settings = Settings(db_path=sys.argv[1], public_host='public.test',
                    admin_host='admin.test', rate_limit_requests=10000)
app = create_app(settings=settings,
                 clock=lambda: datetime(2026, 11, 30, 15, tzinfo=timezone.utc))
uvicorn.run(app, fd=int(sys.argv[2]), access_log=False, log_level='warning')
"""


def pytest_addoption(parser):
    parser.addoption(
        "--browser-engine", choices=("chromium", "webkit"), default="chromium",
        help="RS-005 local browser engine; neither option is a physical device.",
    )


@dataclass
class LiveServer:
    url: str
    db_path: Path
    process: subprocess.Popen
    sensitive_values: list[str] = field(default_factory=list, repr=False)


@pytest.fixture
def live_server(tmp_path):
    db_path = tmp_path / "browser.db"
    migrate(db_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    process = subprocess.Popen(
        [sys.executable, "-c", SERVER_CODE, str(db_path), str(listener.fileno())],
        cwd=ROOT, pass_fds=(listener.fileno(),),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    listener.close()
    server = LiveServer(url, db_path, process)
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(base_url=url, timeout=0.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Isolated HTTP server exited before readiness")
                try:
                    response = client.get("/healthz")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Isolated HTTP server readiness timed out")
        yield server
        with closing(connect(db_path)) as connection:
            assert_db_invariants(connection)
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise AssertionError("Isolated HTTP server failed to stop") from None
        assert process.returncode in (0, -15), "Isolated HTTP server failed"
        assert not any(value in output for value in server.sensitive_values), (
            "Sensitive synthetic test input appeared in server logs"
        )


@pytest.fixture(scope="module")
def browser(request):
    # sync_playwright owns an asyncio loop until its context exits. Close it at
    # the module boundary so unrelated API tests can own their own event loops.
    with sync_playwright() as playwright:
        engine = getattr(playwright, request.config.getoption("--browser-engine"))
        browser = engine.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True,
        locale="ko-KR", timezone_id="America/Los_Angeles",
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert not errors, "Uncaught browser JavaScript exception"
