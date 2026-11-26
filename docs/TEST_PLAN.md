# RoomShare 테스트 계획

이 문서는 기존 REQUIREMENTS/DESIGN/API/DECISIONS의 계약을 보존한 **개발·검증 계획**이다. 모든 테스트는 초기 NOT RUN이며 실행 가능한 구현이나 검증 결과가 아니다. 정책의 우선순위는 AGENTS.md를 따른다. 신규 수치와 도구는 제안이다.

## 1. 테스트 단계와 환경

| 단계 | 실행 장소 | 범위 | 증거 |
|---|---|---|---|
| local | 개인 Ubuntu 또는 격리된 Ubuntu 스테이징 | unit/db/api/integration/concurrency/e2e/security/deploy/recovery/performance | pytest/JUnit, 실제 브라우저·프록시·셸 명령 및 로그 |
| device | 실제 iOS/Android 기기 | Safari/Chrome, 터치, 인증서, PWA(선택) | 기기/OS/브라우저 버전 및 결과 |
| oci | 승인된 OCI 스테이징 | 외부 DNS/TLS/mTLS, 직접 접근 차단, 서비스 지속성, 백업 및 자원 측정 | 환경 식별 정보(비밀 제외), 명령·결과 |
| approval | 운영자 | 정책·접근 범위·배포 승인 | 승인 내역 및 GO/NO-GO |

local은 운영 서버를 의미하지 않는다. systemd/Nginx 실행 권한이 없는 환경은 해당 테스트 NOT RUN. 브라우저 에뮬레이션은 실제 장치 테스트의 대체 증거가 아니다. RS-008의 OCI 미검증은 별도 상태로 남기며, RS-009의 로컬 검증만 진행할 경우 인계 기록과 승인을 받는다. 모든 필수 릴리스 테스트 PASS 및 운영자 승인 전 공개 금지. RS-010은 승인 후 별도로 진행한다.

## 2. 테스트 디렉터리 (제안)

```text
tests/
├── conftest.py              # settings, app, client, tmp DB, clock, admin context
├── unit/                    # 설정/날짜/토큰
├── db/                      # migration, constraints, transactions
├── api/                     # 공개/관리자 계약
├── integration/             # 실제 서비스와 DB
├── concurrency/             # 독립 DB 연결 경합
├── e2e/                     # 브라우저 자동화, 실제 서버 연동
├── security/                # guard, CSRF, 노출, 입력
├── deployment/              # 프록시, 백업, 복구, 스크립트
├── manual/                  # 기기 및 실제 OCI 체크리스트
└── performance/             # 격리된 환경의 기준선 측정
```

기존 파일 경로를 우선한다. 이 구조는 구현 전 제안으로, 빈 테스트 파일을 미리 생성하여 테스트가 존재하는 것처럼 보이게 하지 않는다. `qa/specs/`는 테스트 명세이며 실행 코드는 Agent가 각 티켓에서 작성한다.

## 3. 공통 fixture 및 데이터

- 임시 디렉터리 내 실제 파일 SQLite DB를 케이스마다 새로 생성·마이그레이션한다. 운영 DB 경로 사용 금지. 환경 변수 변경은 monkeypatch 등으로 복원한다.
- 고정 clock 예시: UTC 2026-11-30T15:00:00Z = KST 2026-12-01 00:00:00. 오늘 포함 90일은 2026-12-01~2027-02-28. 이 날짜들은 테스트 데이터이며 실제 현재 날짜가 아니다. 윤년 사례는 별도 clock 사용.
- 기본 이름은 합성 값 `QA_USER_A`, `QA_USER_B`; 메모는 `QA_MEMO_SENTINEL`. 12/02, 12/03, 12/04 등은 모두 2026년 12월로 해석한다. 01/01은 2027년. 날짜/시간 의존 테스트는 필요한 clock을 명시적으로 설정한다.
- DB 상태 비교 helper: reservations, reserved_dates, reservation_logs, settings의 안정적으로 정렬한 논리 스냅샷을 비교한다. 시간/UUID 등 생성값은 별도로 검증한다. 상태 변경 전후를 확인하고 rollback 케이스에서는 기존 스냅샷과 정확히 일치해야 한다.
- 예약 토큰은 테스트 내 실제 생성한 값으로 검사하되 보고서에 평문을 출력하지 않는다. 실제 인증서/키 사용 금지. 테스트 CA/키는 임시 경로에 생성 후 제거한다.
- HTTPX/FastAPI 테스트 client는 실제 SQLite를 연결한다. 서비스 오류 주입용 mock은 예외 재현에만 사용하고 정상 통합 경로를 대체하지 않는다. 브라우저 정상 흐름은 실제 서버 연동, 응답 역전/네트워크 오류는 intercept 허용.
- 동시성: 작업별 독립 SQLite 연결, 시작 barrier 및 유한 join timeout, worker 예외 수집, 매 반복 초기 DB. 충돌 예상 테스트에서 충분한 busy_timeout과 의도적 timeout 테스트를 분리한다. 단순 순차 요청은 동시성 검증으로 인정하지 않는다.
- 전역 불변식: 날짜별 점유 최대 1, RESERVED는 CONFIRMED 예약 참조, BLOCKED는 FK NULL, CANCELLED의 활성 점유 없음, 실패 거래는 예약/점유/로그/설정 변경 없음. 과거 점유는 설계에 따라 유지한다.

## 4. 자동화와 실행 명령

RS-001에서 실제 실행 환경과 설치 명령을 확정한다. 아래는 목표 명령이며 이 문서 작성 시 실행한 것이 아니다.

```bash
python --version
python -m pip check
python -m pytest --collect-only -q
python -m pytest -q
python -m pytest -q --junitxml=/tmp/roomshare-junit.xml
git diff --check
```

개별 케이스는 `@pytest.mark.parametrize`와 의미 있는 테스트명으로 구현하고, `TC-XXX-YY`를 파라미터 ID 또는 매핑표에 연결한다. 실제 경로·marker를 먼저 확인한 후 대상 티켓 테스트 명령을 실행한다. marker를 쓴다면 pytest 설정에 등록한다. 전체 테스트에는 기존 회귀 테스트를 반드시 포함한다. pytest의 종료 코드 5(수집된 테스트 없음)는 PASS가 아니다. 명령의 exit code를 파이프 등으로 잃어버리지 않는다.

Playwright Python 및 브라우저 설치, pytest-cov는 테스트 전용 추가 의존성 제안이다. 실제 호환 버전을 검증하여 lock에 반영하고 운영 의존성에서는 제외한다. E2E 도구 설치가 불가하면 NOT RUN 또는 실제 수동 검증을 별도 기록하며 자동화 PASS를 꾸미지 않는다. 웹 엔진 에뮬레이션과 실제 Safari/Chrome 실기기 검증은 별개다.

## 5. 결과 및 품질 게이트

- 상태: PASS(실행 및 모든 기대 결과 일치), FAIL(불일치), NOT RUN(미실행), SKIP(실행 도구에서 건너뜀), BLOCKED(환경/권한/선행 조건 부족). SKIP/NOT RUN/BLOCKED는 PASS가 아니다.
- 하나의 TC가 여러 입력을 포함하면 모든 하위 입력이 통과해야 PASS. 실패/미실행 하위 케이스를 보고서에 남긴다. 필요 시 세부 ID를 추가하고 원 ID 추적성을 보존한다.
- 티켓 종료: 대상 로컬 필수 케이스 모두 PASS, 기존 회귀 PASS, 완료 조건별 증거, 코드/문서 일치, diff 검사 및 보안 검토. 외부 환경 검증은 실제 상태 그대로 유지하고 기존 완료 조건의 필수 항목은 임의 면제하지 않는다.
- 릴리스 종료: RS-001~009의 필수 케이스와 장치/OCI 검증, 정책 승인, 백업 복원 및 관리자 접근 경계 모두 확인. 운영 노출에 영향 있는 미검증/실패/미승인 항목이 있으면 NO-GO.
- 커버리지 도구 도입 시 line/branch 및 핵심 경로 누락을 보고한다. 수치 제안: 전체 line 85%, 핵심 예약/권한/트랜잭션 branch 90% 목표. 현재 승인된 필수 기준은 아니며 높은 커버리지가 실패/동시성/보안 케이스를 대체하지 않는다.
- 성능 목표는 운영 환경 확인 후 승인한다. 제안 시나리오: 조회 동시성 1/5/10, 경쟁 예약 2/10, 각 실행 시간과 샘플 수 고정, p50/p95/p99/CPU/RSS/오류율 기록. 409 등 계약상 예상 오류는 예상외 5xx와 분리. 1GB RAM은 기존 목표일 뿐 확인된 환경이 아니다.
- 보안 우회, 중복 예약, 데이터 유실, 비밀 노출은 릴리스 차단 결함. 재현 케이스 추가 → 수정 → 관련 테스트 및 전체 회귀 → 증거 갱신. 테스트를 삭제하거나 기대값을 바꿔 통과시키지 않는다.

## 6. 산출물

`qa/TEST_CASES.csv`는 전체 테스트 레지스트리, `qa/TRACEABILITY.md`는 요구사항 추적표, `qa/specs/RS-XXX.md`는 상세 실행 절차다. Agent는 `qa/reports/RS-XXX.md`에 실제 결과를 작성하고 CSV의 상태를 갱신한다. 민감정보 없는 증거만 상대 경로로 연결한다. 실제 명령/종료 코드/실행 환경/commit/시간/실패 이유를 기록한다. 병렬 작업은 각자 담당 결과만 변경하고 병합 시 CSV/INDEX 충돌을 수동 조정한다.
