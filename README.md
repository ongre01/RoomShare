# RoomShare — AI Agent Starter
개인 원룸 하나를 날짜 단위로 공유하는 모바일 웹 프로젝트. RS-001은 최소 FastAPI 앱과 로컬 개발 환경, RS-002는 SQLite 기반, RS-003은 예약 생성·달력 서비스, RS-004는 공개 HTTP API와 취소 서비스, RS-005는 사용자 모바일 예약 화면, RS-006은 보호된 관리자 API, RS-007은 관리자 모바일 화면을 제공한다. RS-008의 [Ubuntu 배포·백업·복원 실행서](docs/DEPLOYMENT.md)는 로컬 검증과 실제 OCI 배포를 구분한다. 공개 릴리스 전 단계다.

## 로컬 설치 및 실행 (RS-001)

검증 환경: Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1. Python 3.11 이상은 초기 목표이며 다른 Python 버전은 별도 검증이 필요하다. 시스템에 `python`이 없으면 가상환경 생성에 `python3`를 사용한다. 전체 개발 테스트에는 Chromium과 RS-008의 Nginx·OpenSSL CLI·systemd-analyze·flock이 필요하다. 테스트는 호스트 서비스를 설치하거나 reload하지 않는다. 저장소 루트에서 실행한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install -r requirements-dev.lock
python -m pip check
python -m playwright install chromium
python -m pytest --collect-only -q
python -m pytest -q
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

다른 터미널에서 `curl --fail http://127.0.0.1:8000/healthz`는 정확히 `{"status":"ok"}`를 반환한다. `/`에는 사용자 예약 화면이 있다. 달력·예약을 사용하기 전에 아래 RS-002 명령으로 DB를 명시적으로 준비한다. 서버는 Ctrl+C로 종료한다. 외부 인터페이스 바인딩 및 공개 배포는 이 절차에 포함되지 않는다. 런타임만 설치할 경우 새 venv에 `python -m pip install -r requirements.lock`을 사용한다. 운영 lock에는 테스트 및 브라우저 도구가 없다.

## 설정과 앱 계약

`app.main.create_app(settings=...)`에 `app.config.Settings` 객체를 주입할 수 있다. 생략하면 `load_settings()`를 호출한다. 주입된 설정은 `app.state.settings`에서 읽고, 변경할 수 없는 객체로 앱별 보관한다. `app.main:create_app --factory`가 ASGI 진입점이다. 모듈 import, 앱 생성 및 lifespan은 모두 DB/디렉터리 생성이나 외부 연결을 수행하지 않는다. DB 준비는 아래 RS-002 명령으로 별도 실행한다.

| Settings 필드 | 환경 변수 | 기본값 |
|---|---|---|
| `db_path` | `ROOMSHARE_DB_PATH` | `./data/roomshare.db` |
| `timezone` | `ROOMSHARE_TIMEZONE` | `Asia/Seoul` |
| `public_host` | `ROOMSHARE_PUBLIC_HOST` | `room.example.com` |
| `admin_host` | `ROOMSHARE_ADMIN_HOST` | `admin-room.example.com` |
| `booking_horizon_days` | `ROOMSHARE_BOOKING_HORIZON_DAYS` | `90` (임시 정책) |
| `admin_access` | `ROOMSHARE_ADMIN_ACCESS` | `deny` |
| `admin_proxy_secret` | `ROOMSHARE_ADMIN_PROXY_SECRET` | 미설정 (`proxy_mtls` 모드에서 필수) |
| `max_body_bytes` | `ROOMSHARE_MAX_BODY_BYTES` | `16384` (임시 제한) |
| `rate_limit_requests` | `ROOMSHARE_RATE_LIMIT_REQUESTS` | `120` (공개/관리자별, 임시 제한) |
| `rate_limit_window_seconds` | `ROOMSHARE_RATE_LIMIT_WINDOW_SECONDS` | `60` (임시 제한) |

환경 변수 이름은 기존 `.env.example`의 `ROOMSHARE_` 접두사를 유지한다. 프로세스 환경 변수 > 소스 저장소 루트의 선택적 `.env` > 기본값 순서다. `.env`는 자동 생성하지 않으며 필요하면 `.env.example`을 참고해 로컬에서 작성한다. 빈 값은 기본값으로 대체하지 않고 오류로 거부한다. 닫히지 않은 따옴표 등 dotenv 문법 오류는 시작 오류로 거부한다. 고정된 python-dotenv 파서로 변수 보간은 끄며, 로더가 `os.environ`을 변경하거나 값을 캐시하지 않는다. `load_settings(env_file=경로, environ=매핑)`으로 테스트 소스를 지정할 수 있고, `env_file=None`은 파일 로딩을 끈다. 지정한 매핑은 프로세스 환경 대신 사용한다. 없는 dotenv 파일은 기본값/환경 변수로 진행한다.

상대 DB 경로는 cwd가 아닌 소스 저장소 루트를 기준으로 절대 경로로 정규화한다. 빈 경로, NUL/제어 문자 및 기존 디렉터리는 거부한다. 호스트는 스킴·포트·경로 없는 ASCII 호스트명이고 공백 제거, 소문자화, 마지막 점 하나 제거 후 검증한다. 정규화 후 두 호스트가 같으면 오류다. 기간은 양의 정수 또는 숫자 문자열만 허용하며 bool/실수/빈 값은 거부한다. timezone은 `Asia/Seoul`, 관리자 접근 모드는 `deny` 또는 `proxy_mtls`다. 잘못된 설정은 값 자체를 노출하지 않는 `SettingsError`로 서버 시작을 실패시킨다.

관리자 `/admin`, `/api/admin` 및 하위 경로는 기본 403이다. RS-006의 명시적 `proxy_mtls` 모드는 정확한 관리자 Host, loopback 실제 연결, 프록시 mTLS 검증 신호, 내부 공유 비밀을 모두 요구한다. 변경 요청은 HTTPS Origin과 CSRF도 검증한다. 테스트용 인증 주입은 Python 테스트 코드에만 있으며 환경 설정 우회는 없다. RS-008은 실제 Nginx/mTLS를 격리 검증하며 외부 접근 여부는 별도 확인한다. 자동 API 문서 경로도 비활성화한다. 템플릿/정적 경로는 소스 위치 기준이며 Jinja HTML 자동 이스케이프와 모바일 viewport를 사용한다.

## 테스트와 lock 갱신

`tests/conftest.py`의 임시 설정·앱·client fixture는 실제 서비스 DB를 사용하지 않는다. RS-001은 DB 파일이 생기지 않는 것을 검증한다. RS-002의 선택적 `migrated_db` fixture만 실제 임시 파일 DB를 생성·마이그레이션하며 기존 bootstrap fixture는 그대로 유지한다. 대상 테스트와 전체 회귀 명령은 다음과 같다.

```bash
python -m pytest -q tests/test_config.py tests/test_health.py
python -m pytest -q tests/test_db.py tests/test_migrations.py
python -m pytest -q tests/test_reservation_service.py tests/test_concurrency.py
python -m pytest -q tests/test_public_api.py tests/test_cancellation.py tests/test_public_limits.py
python -m pytest -q tests/test_public_page.py tests/e2e/test_public_ui.py
python -m pytest -q tests/test_admin_access.py tests/test_admin_api.py tests/test_admin_service.py
python -m pytest -q tests/test_admin_page.py tests/e2e/test_admin_ui.py
python -m pytest -q tests/test_deployment.py tests/deployment/test_proxy.py tests/test_backup_restore.py
python -m pytest -q
python3 scripts/verify_bootstrap.py
git diff --check
```

`verify_bootstrap.py`는 TC-001-09용 별도 검증이다. 새 임시 런타임/개발 venv에 lock을 설치하고, pip check, loopback 실제 HTTP, 테스트 실행과 자식 프로세스 종료를 확인한 뒤 임시 venv/DB를 제거한다. RS-005부터 개발 venv에서 Chromium 바이너리를 준비하고 전체 E2E도 실행한다(브라우저 다운로드 캐시는 재사용). 패키지/브라우저 저장소 접근 및 브라우저 실행에 필요한 시스템 라이브러리가 필요하며 일반 pytest에서 중첩 실행하지 않는다. 처음 검증한 증거는 [RS-001 보고서](qa/reports/RS-001.md), RS-005 변경 이후 결과는 [RS-005 보고서](qa/reports/RS-005.md)에 기록한다.

lock은 실제 설치된 전이 의존성까지 `pip freeze`로 고정했다. 기존 `.venv`를 동결하지 말고, 갱신할 때는 새로운 임시 venv에서 다음 순서로 실행한다. 개발 lock은 런타임 버전을 그대로 포함한 전체 개발 설치 목록이다.

```bash
python3 -m venv /tmp/roomshare-lock-venv
source /tmp/roomshare-lock-venv/bin/activate
python -m pip install -r requirements.in
python -m pip freeze > requirements.lock
python -m pip install -c requirements.lock -r requirements-dev.in
python -m pip freeze > requirements-dev.lock
python -m pip check
python -m playwright install chromium
python -m pytest -q
python3 scripts/verify_bootstrap.py
deactivate
```

해당 임시 경로가 이미 있으면 새 경로를 선택한다. 설치/호환성 테스트 및 깨끗한 venv 검증을 모두 통과한 lock만 커밋한다.

## DB 초기화·검증 (RS-002)

가상환경 활성화 후 저장소 루트에서 실행한다. 실제 서비스 DB 대신 아래처럼 별도 임시 디렉터리로 먼저 검증할 수 있다. SQLite는 Python 표준 라이브러리만 사용하며 추가 의존성은 없다.

```bash
rs002_tmp_dir=$(mktemp -d)
python scripts/migrate.py --db-path "$rs002_tmp_dir/roomshare.db"
python scripts/migrate.py --db-path "$rs002_tmp_dir/roomshare.db"
python scripts/migrate.py --db-path "$rs002_tmp_dir/roomshare.db" --check
```

첫 실행은 `applied: 1`, 재실행은 `up to date`, 검증은 `schema: 1; integrity: ok; foreign keys: ok`를 출력한다. 테스트 DB가 필요 없어지면 생성된 임시 디렉터리를 정리한다. `--check`는 기존 파일을 읽기 전용으로 검사하며 없는 DB를 생성하지 않는다. 실패 시 종료 코드 1이며 SQL/데이터/경로는 출력하지 않는다.

운영자가 대상 경로와 백업을 확인한 뒤 실행할 초기화 명령은 `python scripts/migrate.py`, 검증은 `python scripts/migrate.py --check`다. `--db-path` 생략 시 `load_settings()`의 `ROOMSHARE_DB_PATH`를 사용한다. 명시적 경로도 기존 Settings 규칙에 따라 상대 경로는 소스 루트 기준이다. CLI는 마이그레이션 때만 상위 디렉터리를 만들며 앱 기동은 DB를 초기화하지 않는다. 삭제·다운그레이드·자동 재시도는 제공하지 않는다.

초기 `booking_horizon_days`는 실행 당시 Settings 값(환경 변수 > 선택적 .env > 기본 90)을 DB에 한 번 저장한다. 이후 DB 값이 운영 설정의 기준이며 재실행 시 환경 값을 바꾸어도 DB 값을 덮어쓰지 않는다. DB 연결/트랜잭션 소유권, 오류 및 다음 티켓 계약은 [DESIGN](docs/DESIGN.md)의 RS-002 절, 실제 검증은 [RS-002 보고서](qa/reports/RS-002.md)를 따른다.

## 예약 엔진 (RS-003)

명시적으로 마이그레이션한 DB 경로에 `app.services.reservations.create_reservation(db_path, name=..., dates=[...], memo="")`와 `app.services.calendar.get_calendar(db_path, "YYYY-MM")`를 호출한다. 생성은 예약 UUID·정렬 날짜·CONFIRMED·취소 토큰을 반환한다. 토큰 평문은 생성 결과에만 있으며 결과 전체를 로그에 출력하지 않는다. 달력은 날짜별 상태와 KST 오늘/예약 범위만 반환한다.

각 호출은 `clock` 함수와 `busy_timeout_ms`를 주입할 수 있다. clock은 timezone-aware datetime을 반환하며 테스트에서는 고정 UTC 시각을 사용한다. 예약 가능 기간은 DB에서 매번 읽고, 생성에서는 BEGIN IMMEDIATE 이후 정책과 충돌을 검증한다. 일부 날짜 충돌이나 이력 쓰기 실패도 예약·점유·이력 전체를 롤백한다. 서비스 시그니처, 도메인 예외, 재사용 helper 및 후속 HTTP 매핑은 [DESIGN](docs/DESIGN.md)의 RS-003 계약과 [API](docs/API.md)를 따른다. 취소 동작과 HTTP 경로는 아래 RS-004 계약을 따른다.

## 공개 API 및 취소 (RS-004)

명시적 마이그레이션 후 동일한 앱 실행 명령으로 `GET /api/calendar?month=YYYY-MM`, `POST /api/reservations`, `POST /api/reservations/cancel`을 사용한다. POST는 JSON 본문만 받으며 query를 거부한다. 상세 필드·오류·반복 취소 계약은 [API](docs/API.md)의 RS-004 절을 따른다. 생성 토큰을 로그나 URL에 넣지 않는다. 생성 응답이 손실되면 성공 여부를 알 수 없으므로 자동 재제출하지 않는다.

취소 서비스는 `cancel_reservation(db_path, *, id, cancel_token, clock=utc_now, busy_timeout_ms=5000)`다. 유효한 토큰과 전체 미래 점유를 같은 쓰기 트랜잭션에서 검사하고 점유 해제·상태·UTC 시각·이력을 원자적으로 기록한다. 유효한 반복 취소는 추가 변경 없이 성공한다. 앱 팩토리의 키워드 `clock`, `busy_timeout_ms`는 테스트 주입용이며 HTTP로 변경할 수 없다.

요청 제한 설정 3개는 1~2,147,483,647 정수(숫자 문자열 허용)이며 0/음수/bool/실수는 시작 시 거부한다. 본문 크기는 실제 수신 bytes로 검사한다. 빈도는 모든 공개 API 요청이 공유하는 프로세스별 고정 시간창으로 적용하며 전달 IP 헤더를 사용하지 않는다. 단일 worker를 유지하고 운영 수치는 별도 승인·조정한다. 공개 API의 성공/오류 모두 no-store이며 CORS 허용 헤더를 추가하지 않는다. [앱 검증](qa/reports/RS-004.md)과 [프록시 검증](qa/reports/RS-008.md)은 별도다.

## 사용자 모바일 화면 및 브라우저 검증 (RS-005)

명시적으로 DB를 마이그레이션한 뒤 기존 Uvicorn 명령으로 실행하고 `http://127.0.0.1:8000/`을 연다. 다른 달을 오가며 날짜를 선택한 뒤 이름/메모를 입력해 예약한다. 성공 화면의 ID·토큰은 한 번만 표시하므로 함께 보관한다. 보관 후 닫기·새로고침·페이지 이동으로 지워진 정보는 복구할 수 없다. 취소하려면 두 정보를 별도 취소 폼에 입력하고 전체 취소에 동의한다. 모든 날짜가 오늘 이후인 예약만 사용자 취소할 수 있다.

충돌 시 관련 월을 다시 조회하고 가능한 선택과 입력을 유지한다. 연결 중단·응답 손실은 결과 불명으로 표시하며 자동 재제출하지 않는다. 달력 갱신에 실패하면 달력 새로고침으로 선택 상태를 다시 확인한다. 서버 KST 날짜와 예약 범위를 사용하므로 브라우저의 시간대와 무관하다. JavaScript를 끄면 안내가 표시되며 예약·취소를 제출하지 않는다.

Playwright Python은 개발 lock에만 포함되며 앱 운영에는 Node나 브라우저가 필요하지 않다. E2E는 케이스별 실제 임시 SQLite·loopback HTTP·고정 clock·Chromium을 사용한다. 기본 전체 pytest에도 포함되며 브라우저가 없으면 skip으로 숨기지 않고 실패한다. 최소 Linux 이미지에서는 브라우저 실행 오류가 알려주는 시스템 라이브러리를 별도로 준비해야 한다. `python -m playwright install-deps chromium`은 시스템 패키지를 설치하는 개발 환경 준비 명령이며 권한이 필요하다. 운영 서버에서 실행하지 않는다.

선택적으로 WebKit을 준비한 환경에서는 `python -m playwright install webkit` 후 `python -m pytest -q tests/e2e/test_public_ui.py --browser-engine=webkit`로 별도 실행한다. WebKit/Chromium 자동화는 실제 iPhone Safari/Android Chrome을 대체하지 않는다. 실제 장치 절차와 현재 미실행 상태는 [수동 체크리스트](tests/manual/public-ui.md), 케이스별 결과는 [RS-005 보고서](qa/reports/RS-005.md)를 따른다.

## 관리자 접근 제어 및 API (RS-006)

기본 `ROOMSHARE_ADMIN_ACCESS=deny`를 유지한다. 보호된 프록시 운영 준비 시에만 `proxy_mtls`와 별도 `ROOMSHARE_ADMIN_PROXY_SECRET`을 설정한다. 비밀은 CSPRNG 32바이트 이상의 URL-safe 문자열(43~128자)이며 Git·로그·브라우저에 넣지 않는다. 실제 서버에서는 [프록시 신뢰 계약](docs/DEPLOYMENT.md)에 따라 Uvicorn의 `--no-proxy-headers`, loopback bind, Nginx 헤더 덮어쓰기 및 mTLS를 함께 검증해야 한다. 설정만으로 배포 검증이 완료되지 않는다.

인증된 관리자는 `GET /api/admin/csrf`의 토큰을 메모리에 보관하고 모든 POST/PATCH/DELETE에 `Origin: https://<admin_host>`와 `X-CSRF-Token`으로 보낸다. 30분 만료·앱 재시작 후 재발급한다. 예약 조회/생성/부분 수정/취소, 일괄 차단/해제, 로그 페이지와 예약 기간 설정 API는 [API 계약](docs/API.md)을 따른다. 관리자 생성에서는 취소 토큰을 반환하지 않는다. `/admin`은 RS-007 관리자 화면이며 같은 guard를 통과한 컨텍스트에만 HTML을 제공한다.

과거 날짜 포함 예약의 모든 수정·취소와 과거 차단 해제는 임시로 금지한다. 오늘은 관리자 변경을 허용한다. 기간 축소는 기존 점유를 보존하고, PATCH 생략 날짜도 유지한다. 관리자 기간 변경 상한 366일·로그 기본 50/최대 100건은 승인 전 임시값이다. 추가 의존성·마이그레이션 없이 기존 SQLite 트랜잭션과 공개 생성 서비스를 재사용한다. 케이스별 검증은 [RS-006 보고서](qa/reports/RS-006.md)를 따른다.

## 시작 순서
1. 저장소 루트에서 Agent를 시작한다. `AGENTS.md`와 `prompts/COMMON.md`의 필수 문서 로딩 절차를 따른다. Claude Code용 진입점은 `CLAUDE.md`다.
2. `tickets/INDEX.md`의 선행 조건에 따라 명시적으로 요청된 티켓 하나씩 구현한다. 미확정 사항은 `docs/DECISIONS.md`를 참조한다.
3. 티켓별 테스트와 완료 조건을 충족한 뒤 커밋한다.
4. `docs/DEPLOYMENT.md` 기준으로 OCI 배포를 검증한다.

## 기술 구성 (제안)
Python FastAPI / Jinja2 / HTML CSS JavaScript / SQLite / Nginx / systemd / Ubuntu. Docker 및 Node 런타임 불필요. Python 3.11 이상을 초기 목표로 하되 실제 서버 버전 확인 후 확정한다.

## Agent 시작 프롬프트
```text
RS-001 티켓을 구현해줘. AGENTS.md와 prompts/COMMON.md를 읽고,
대상 티켓/프롬프트/QA 명세에 따라 실제 구현과 테스트를 수행해줘.
필수 완료 조건을 충족한 경우에만 DONE 처리하고 후속 티켓은 시작하지 마.
```
번호를 바꾸어 다음 티켓을 요청한다. 자세한 구현/QA/리뷰 실행 예시는 `prompts/README.md`를 참조한다.

## 상세 티켓 실행
`prompts/README.md`에 실행 순서와 복사용 시작 프롬프트가 있다. `prompts/COMMON.md`와 대상 `prompts/RS-XXX.md`를 읽고 티켓 하나씩 수행한다. 기존 일괄 구현 프롬프트보다 이 티켓 단위 실행 방식을 우선한다.

## v2 테스트 명세
`docs/TEST_PLAN.md`와 `qa/README.md`부터 읽는다. 전체 126개 테스트 명세가 `qa/specs/` 및 `qa/TEST_CASES.csv`에 있다. 각 티켓의 Agent는 실제 테스트 코드와 실행 보고서를 작성한다. 기존 개발 티켓/프롬프트에 v2 심화 절차를 추가했다. RS-001의 실제 소스·테스트 및 검증 결과는 해당 보고서에 기록하며 후속 티켓은 별도로 진행한다.

## v3 Agent 진입점
- `AGENTS.md`: 공통 진입점과 필수 파일 로딩 규칙.
- `CLAUDE.md`: Claude Code용 import 진입점.
- `prompts/COMMON.md`: 상세 작업·테스트·Git·보고 규칙.
- 실제 Agent에서 자동 로딩을 확인하는 절차는 `prompts/README.md` 참조. 파일 참조는 지침이며 강제 실행이나 테스트 통과를 보장하지 않는다.
- v2의 개발 티켓 10개와 테스트 명세 126개는 변경하지 않았다. 이번 변경은 Agent 실행 지침에 한정한다.

## 관리자 모바일 화면 (RS-007)

보호된 관리자 호스트의 `/admin`을 연다. 기본 `deny` 설정은 계속 403이며 접속을 위해 코드나 환경에 인증 우회를 추가하지 않는다. 운영 접근 준비는 위 RS-006 및 DEPLOYMENT의 mTLS 계약을 따른다. 사용자 화면에는 관리자 링크를 제공하지 않는다.

월별 달력과 예약 목록에서 등록·수정·취소를 수행한다. 날짜는 달력 선택 또는 전체 날짜 입력으로 편집할 수 있으며 월을 넘는 기존 날짜도 모두 표시한다. 저장 전 원본과 변경 날짜를 확인한다. 차단일 등록·확인 후 해제, 변경 이력 페이지 이동, 예약 가능 기간 설정도 같은 화면에서 제공한다. 예약 취소는 예약 전체에 적용한다.

접근/CSRF 실패는 인증 환경을 확인한 뒤 `접근 다시 확인`으로 갱신한다. 실패한 변경은 자동 재제출하지 않으며 입력을 유지한다. 연결이 끊겨 결과를 알 수 없으면 `전체 새로고침`으로 처리 여부부터 확인한다. CSRF와 관리 정보는 브라우저 영구 저장소에 쓰지 않는다. 페이지를 떠나면 입력과 관리 데이터를 지운다.

테스트는 기존 개발 lock의 Playwright Python/Chromium과 임시 SQLite를 사용한다. 로컬 자동화는 실제 mTLS나 실기기 인증서 검증을 대신하지 않는다. [관리자 수동 검증 절차](tests/manual/admin-ui.md)와 [RS-007 검증 보고서](qa/reports/RS-007.md)에 로컬/실기기/배포 결과를 구분한다.

## Ubuntu 배포 및 복구 (RS-008)

승인된 Ubuntu 스테이징에서 디렉터리를 준비한 뒤 `./scripts/deploy.sh prepare "$APPROVED_REV" /opt/roomshare/releases /etc/roomshare`를 실행한다. 지정한 커밋으로 새 릴리스를 준비할 뿐, DB와 실행 중인 서비스는 변경하지 않는다. 최초 설치와 업데이트의 다음 단계는 [배포 실행서 §3](docs/DEPLOYMENT.md#3-db-준비와-서비스-전환)을 따른다.

[배포 패키지](deploy/README.md)는 비활성 릴리스의 venv 준비, 명시적 DB 마이그레이션, 두 TLS 호스트와 관리자 mTLS, 단일 loopback worker, 일일 WAL 온라인 백업을 제공한다. 복원은 항상 새 디렉터리에 수행하고 운영 전환은 별도 승인 절차다. [실행서](docs/DEPLOYMENT.md)와 [16개 케이스의 검증 보고서](qa/reports/RS-008.md)를 함께 확인한다. 개발용 테스트 CA·loopback 검증은 실제 OCI, 외부 방화벽 또는 운영 인증서 검증이 아니다.

RS-009의 [로컬 통합 검증 보고서](qa/reports/RS-009.md)와 [공개 전 체크리스트](docs/RELEASE_CHECKLIST.md)를 구분한다. 새 릴리스 통합 케이스만 실행하려면 개발 venv에서 `python -m pytest -q -s tests/e2e -k tc009`를 사용한다. 임시 SQLite·loopback 서버만 사용하며 외부 서버를 대상으로 받지 않는다.
