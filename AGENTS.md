# Agent 작업 규칙

## 필수 문서 로딩 및 작업 시작
- 이 파일은 저장소 루트의 공통 진입점이다. Claude Code는 `CLAUDE.md`를 통해 이 파일과 `prompts/COMMON.md`를 참조하도록 구성한다. Codex는 이 파일의 지시에 따라 `prompts/COMMON.md`를 직접 읽는다. 자동 로딩 여부와 무관하게 필수 문서의 실제 내용을 확인한다.
- 모든 티켓 작업(구현/QA/리뷰) 전에 저장소 루트 기준 `prompts/COMMON.md`를 반드시 읽고 적용한다. Markdown 링크만 확인하거나 파일명만 나열하는 것으로 대신하지 않는다.
- `README.md`, `docs/REQUIREMENTS.md`, `docs/DECISIONS.md`, `docs/DESIGN.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, `docs/TEST_PLAN.md`, `tickets/INDEX.md`, `qa/README.md`, `qa/ASSUMPTIONS.md`, `qa/TEST_CASES.csv`, `qa/TRACEABILITY.md`, `qa/REPORT_TEMPLATE.md`를 읽는다. CSV는 대상 티켓 행 및 관련 선행 조건을 확인한다.
- 요청된 티켓 번호를 RS-001~RS-010 중 하나로 확인하고 `tickets/RS-XXX.md`, `prompts/RS-XXX.md`, `qa/specs/RS-XXX.md`를 읽는다(`RS-XXX`는 실제 대상 번호로 치환). 기존 `qa/reports/RS-XXX.md`가 있으면 함께 확인한다. 관련 선행 티켓의 실제 코드와 검증 증거도 확인한다.
- 필수 파일을 읽을 수 없거나 대상 티켓/필수 선행 작업이 확정되지 않은 경우 임의로 구현하지 말고 BLOCKED와 원인을 보고한다. 모든 티켓 문서를 한 번에 로딩할 필요는 없다. 컨텍스트 초기화/압축 후에는 필수 규칙과 대상 티켓을 다시 확인한다.
- 구현 요청은 대상 티켓 하나만 실제 구현하고 테스트한다. QA 요청은 테스트 작성/실행 범위로 한정하고 제품 코드 변경은 별도 승인으로 처리한다. 리뷰 요청은 `prompts/REVIEW.md`도 읽고 읽기 전용 코드 검토와 격리 테스트만 수행한다. 리뷰라는 이유로 구현하거나 티켓 상태/CSV/소스/Git을 변경하지 않는다(별도 승인 예외).
- 작업 시작 시 대상 티켓, 역할, 읽은 필수 파일 및 선행 조건을 간단히 보고한다. 실제 확인하지 않은 파일은 읽었다고 주장하지 않는다. 요청이 단순 질문이나 문서 검토인 경우 구현 작업으로 확대하지 않는다.
- 세부 개발/테스트/보고 절차는 `prompts/COMMON.md`를 따른다. 현재 요청과 역할별 제한을 우선하며 기존 요구사항 우선순위와 보안 제한을 유지한다. 테스트 미실행을 PASS/DONE으로 처리하지 않는다.

## 핵심 프로젝트 규칙
- 요구사항의 우선순위: 사용자 명시 요구 > REQUIREMENTS > DECISIONS의 임시값 > DESIGN > 티켓. 충돌하면 기록하고 수정한다.
- 원룸 1개, 1일 단위, 사용자 및 관리자 URL 분리, 로그인/회원가입/사진/가격/결제 없음. 기능 확대 금지.
- 관리자 URL 분리는 인증이 아니다. 운영 환경 관리자 UI/API는 mTLS 또는 사설망으로 보호하고 기본 차단한다. 사용자에게 로그인 화면을 추가하지 않는다.
- 예약/차단의 모든 쓰기는 DB 트랜잭션에서 처리. 날짜 고유키를 최종 충돌 방어로 사용. 전체 성공 또는 전체 롤백.
- 날짜는 Asia/Seoul의 달력 날짜, YYYY-MM-DD. UTC 타임스탬프와 구분한다.
- 입력 검증, SQL 파라미터 바인딩, 템플릿 자동 이스케이프, 비밀값 로그 금지. 관리자 변경 API는 Origin/CSRF 방어 적용.
- 티켓 단위 브랜치 및 커밋: `feat(reservation): prevent duplicate booking (RS-003)` 형식. 테스트와 문서 함께 변경. 실제 테스트 결과만 보고한다.
- DB, 백업, 인증서, 키, .env는 Git에 올리지 않는다. 의존성 버전은 호환 검증 후 고정한다.
- 각 티켓 완료 후 INDEX 상태 및 검증 증거(명령/결과/남은 위험)를 갱신한다. 실제 OCI 접근 권한 없으면 배포는 미검증으로 남긴다.

## 테스트 검증 확장
- 모든 티켓은 `docs/TEST_PLAN.md`, `qa/specs/RS-XXX.md`, `qa/TEST_CASES.csv`를 추가 적용한다. 케이스별 실제 테스트 구현 및 증거를 `qa/reports/RS-XXX.md`에 남긴다.
- 기존 요구사항과 임시 정책을 유지한다. 제안된 도구/수치/인터페이스는 확정 요구사항이 아니며 차이는 문서화한다.
- 전체 pytest 및 대상 케이스, 수동·장치·프록시·OCI 검증을 구분한다. 미실행/skip을 PASS로 기록하지 않는다. 필수 항목 미검증 시 DONE 및 공개 릴리스 금지.
