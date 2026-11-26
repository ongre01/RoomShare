# RoomShare — AI Agent Starter
개인 원룸 하나를 날짜 단위로 공유하는 모바일 웹 프로젝트. RS-001은 실행 가능한 최소 FastAPI 앱과 로컬 개발 환경을 제공한다. 예약 기능 및 운영 배포는 아직 구현하지 않았다.

## 로컬 설치 및 실행 (RS-001)

검증 환경: Ubuntu 24.04, Python 3.12.3, SQLite 3.45.1. Python 3.11 이상은 초기 목표이며 다른 Python 버전은 별도 검증이 필요하다. 시스템에 `python`이 없으면 가상환경 생성에 `python3`를 사용한다. 저장소 루트에서 실행한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install -r requirements-dev.lock
python -m pip check
python -m pytest --collect-only -q
python -m pytest -q
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

다른 터미널에서 `curl --fail http://127.0.0.1:8000/healthz`는 정확히 `{"status":"ok"}`를 반환한다. `/`에는 준비 화면만 있다. 서버는 Ctrl+C로 종료한다. 외부 인터페이스 바인딩 및 공개 배포는 이 절차에 포함되지 않는다. 런타임만 설치할 경우 새 venv에 `python -m pip install -r requirements.lock`을 사용한다. 운영 lock에는 pytest/HTTPX가 없다.

## 설정과 앱 계약

`app.main.create_app(settings=...)`에 `app.config.Settings` 객체를 주입할 수 있다. 생략하면 `load_settings()`를 호출한다. 주입된 설정은 `app.state.settings`에서 읽고, 변경할 수 없는 객체로 앱별 보관한다. `app.main:create_app --factory`가 ASGI 진입점이다. 모듈 import, 앱 생성 및 lifespan은 모두 DB/디렉터리 생성이나 외부 연결을 수행하지 않는다. 실제 DB 연결·마이그레이션은 RS-002 범위다.

| Settings 필드 | 환경 변수 | 기본값 |
|---|---|---|
| `db_path` | `ROOMSHARE_DB_PATH` | `./data/roomshare.db` |
| `timezone` | `ROOMSHARE_TIMEZONE` | `Asia/Seoul` |
| `public_host` | `ROOMSHARE_PUBLIC_HOST` | `room.example.com` |
| `admin_host` | `ROOMSHARE_ADMIN_HOST` | `admin-room.example.com` |
| `booking_horizon_days` | `ROOMSHARE_BOOKING_HORIZON_DAYS` | `90` (임시 정책) |
| `admin_access` | `ROOMSHARE_ADMIN_ACCESS` | `deny` |

환경 변수 이름은 기존 `.env.example`의 `ROOMSHARE_` 접두사를 유지한다. 프로세스 환경 변수 > 소스 저장소 루트의 선택적 `.env` > 기본값 순서다. `.env`는 자동 생성하지 않으며 필요하면 `.env.example`을 참고해 로컬에서 작성한다. 빈 값은 기본값으로 대체하지 않고 오류로 거부한다. 닫히지 않은 따옴표 등 dotenv 문법 오류는 시작 오류로 거부한다. 고정된 python-dotenv 파서로 변수 보간은 끄며, 로더가 `os.environ`을 변경하거나 값을 캐시하지 않는다. `load_settings(env_file=경로, environ=매핑)`으로 테스트 소스를 지정할 수 있고, `env_file=None`은 파일 로딩을 끈다. 지정한 매핑은 프로세스 환경 대신 사용한다. 없는 dotenv 파일은 기본값/환경 변수로 진행한다.

상대 DB 경로는 cwd가 아닌 소스 저장소 루트를 기준으로 절대 경로로 정규화한다. 빈 경로, NUL/제어 문자 및 기존 디렉터리는 거부한다. 호스트는 스킴·포트·경로 없는 ASCII 호스트명이고 공백 제거, 소문자화, 마지막 점 하나 제거 후 검증한다. 정규화 후 두 호스트가 같으면 오류다. 기간은 양의 정수 또는 숫자 문자열만 허용하며 bool/실수/빈 값은 거부한다. timezone은 `Asia/Seoul`, 접근 모드는 `deny`만 지원한다. 잘못된 설정은 값 자체를 노출하지 않는 `SettingsError`로 서버 시작을 실패시킨다.

관리자 `/admin`, `/api/admin` 및 하위 기능은 미구현 404다. 별도 호스트나 전달 헤더로 접근을 허용하는 개발용 우회는 없다. 실제 프록시 신뢰·mTLS·Origin/CSRF 검증은 RS-006/008에서 구현·검증해야 한다. 자동 API 문서 경로도 비활성화한다. 템플릿/정적 경로는 소스 위치 기준이며 Jinja HTML 자동 이스케이프와 모바일 viewport를 사용한다.

## 테스트와 lock 갱신

`tests/conftest.py`의 임시 설정·앱·client fixture는 실제 서비스 DB를 사용하지 않는다. RS-001은 DB 파일이 생기지 않는 것을 검증하며, 마이그레이션된 DB fixture는 RS-002에서 추가한다. 대상 테스트와 전체 회귀 명령은 다음과 같다.

```bash
python -m pytest -q tests/test_config.py tests/test_health.py
python -m pytest -q
python3 scripts/verify_bootstrap.py
git diff --check
```

`verify_bootstrap.py`는 TC-001-09용 별도 검증이다. 새 임시 런타임/개발 venv에 lock을 설치하고, pip check, loopback 실제 HTTP, 테스트 실행과 자식 프로세스 종료를 확인한 뒤 임시 파일을 제거한다. 패키지 저장소 접근이 필요하며 일반 pytest에서 중첩 실행하지 않는다. 증거와 TC 매핑은 [RS-001 보고서](qa/reports/RS-001.md)에 기록한다.

lock은 실제 설치된 전이 의존성까지 `pip freeze`로 고정했다. 기존 `.venv`를 동결하지 말고, 갱신할 때는 새로운 임시 venv에서 다음 순서로 실행한다. 개발 lock은 런타임 버전을 그대로 포함한 전체 개발 설치 목록이다.

```bash
python3 -m venv /tmp/roomshare-lock-venv
source /tmp/roomshare-lock-venv/bin/activate
python -m pip install -r requirements.in
python -m pip freeze > requirements.lock
python -m pip install -c requirements.lock -r requirements-dev.in
python -m pip freeze > requirements-dev.lock
python -m pip check
python -m pytest -q
python3 scripts/verify_bootstrap.py
deactivate
```

해당 임시 경로가 이미 있으면 새 경로를 선택한다. 설치/호환성 테스트 및 깨끗한 venv 검증을 모두 통과한 lock만 커밋한다.

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
