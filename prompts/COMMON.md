# AI Agent 공통 작업 지침

## 적용 범위 및 역할
- `AGENTS.md`의 필수 문서 로딩 규칙에 따라 실제 파일 내용을 읽는다. Claude Code에서는 루트 `CLAUDE.md`의 import로 이 문서가 제공될 수 있으며, Codex는 `AGENTS.md`의 지시에 따라 직접 읽는다. 중복 로딩은 필요 없지만 현재 세션에서 내용을 확인할 수 없으면 파일을 다시 읽는다.
- 아래 구현/상태 갱신/Git 절차는 구현 역할에 적용한다. QA 역할은 테스트 코드와 허용된 테스트 증거 기록만 변경한다. 리뷰 역할은 `prompts/REVIEW.md`에 따라 코드와 저장소 상태를 변경하지 않고 결과를 보고한다. 별도 요청 없이는 리뷰 보고서를 저장소에 쓰지 않는다. 제품 코드 수정이 필요한 QA/리뷰 발견 사항은 별도로 보고한다.
- 필수 문서 누락, 선행 작업 미완료 또는 읽기 권한 오류가 있으면 해당 작업을 BLOCKED로 보고한다. 테스트 실행 환경이 부족하면 해당 케이스를 NOT RUN/BLOCKED로 구분하며 성공으로 간주하지 않는다.

## 작업 시작
- 저장소 루트의 `AGENTS.md`, `README.md`, `docs/REQUIREMENTS.md`, `docs/DECISIONS.md`, `docs/DESIGN.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, `tickets/INDEX.md`, 대상 티켓과 `prompts/COMMON.md`를 읽는다.
- `git status --short`, `git branch --show-current`, `git log -5 --oneline` 및 관련 코드와 테스트를 확인한다. 기존 사용자 변경을 덮어쓰거나 삭제하지 않는다. Git 저장소가 아니라면 임의로 원격을 만들지 말고 초기화 필요성을 보고한다.
- 선행 티켓의 상태뿐 아니라 실제 산출물과 테스트 증거를 확인한다. 미완료 선행 작업을 임의로 구현하거나 건너뛰지 말고 BLOCKED로 보고한다.
- 확정 요구사항 > REQUIREMENTS > DECISIONS의 임시값 > DESIGN > 티켓 순으로 적용한다. 충돌은 문서에 기록하고 결정이 필요한 부분은 구분한다. 임시값은 승인된 정책이 아니다.
- 구현 역할에서 대상 티켓을 IN_PROGRESS로 변경하고 변경 파일/테스트 계획을 간단히 제시한 뒤 실제 코드 작업을 수행한다. 읽기 전용 분석에 그치지 않는다.

## 구현 규칙
- 원룸 1개, 1일 단위, 사용자/관리자 주소 분리, 로그인/회원가입/사진/가격/결제 없음. 다중 원룸, 리뷰, 승인 흐름, 외부 서비스 등 범위 확장 금지.
- FastAPI/Jinja2/SQLite/HTML/CSS/Vanilla JS, Nginx/systemd 구성 유지. Docker/Node 런타임 추가 금지. 기존 구조 우선, 파일명은 제안이며 필요 시 합리적으로 조정하고 이유를 보고한다.
- 모든 날짜는 Asia/Seoul 달력 날짜 YYYY-MM-DD; 타임스탬프는 UTC. 모든 예약/차단 쓰기와 이력은 같은 DB 트랜잭션에서 처리한다. 날짜 PK가 최종 방어선이다.
- 공개 API는 예약자 개인정보를 노출하지 않는다. 생성 응답의 본인 취소 토큰은 명시적 예외이며 1회 표시한다. 토큰 평문/개인정보/비밀값을 로그에 남기지 않는다.
- 관리자 경로는 기본 차단. 주소 분리만으로 인증하지 않는다. 검증된 프록시 접근 신호와 관리자 호스트를 함께 검사한다. 관리자 변경 요청의 Origin/CSRF 검증을 우회하지 않는다.
- 실제 서비스 DB, 인증서, 키, .env, 백업은 수정/커밋하지 않는다. 테스트는 임시 DB와 비밀이 아닌 테스트 자격 증명으로 수행한다. 외부 공개, 방화벽 변경, 실제 데이터 복원/삭제, 원격 push는 별도 승인 없이는 하지 않는다.
- 개인 Ubuntu에서 개발한다. OCI 이전 가능하도록 절대 경로/호스트/환경별 비밀을 코드에 하드코딩하지 않는다.
- 의존성 추가는 필요한 경우에 한하며 호환성 검증과 lock 갱신을 동반한다. 문서/테스트와 구현을 동기화한다.

## Git 및 완료 절차
- 브랜치 이름 제안: `feature/RS-XXX-short-name`. 현재 브랜치/작업트리의 소유권 확인 후 생성 또는 재사용한다. 병렬 작업은 별도 worktree 사용을 권장한다.
- 대상 티켓의 테스트를 실행한 후 전체 `python -m pytest -q`를 실행한다(실행 환경은 RS-001에서 확정). 테스트 실패/미실행/skip을 성공으로 기록하지 않는다.
- `git diff --check`, 변경 파일 및 비밀값 포함 여부 확인. 완료 조건을 증거와 함께 갱신한다. 미검증 필수 조건은 체크하지 않는다.
- 티켓 및 INDEX 상태는 DONE(필수 조건 충족), BLOCKED(선행/환경/승인 필요), IN_PROGRESS(일부 완료) 중 실제 상태를 기록한다. RS-008~009의 실제 OCI 검증은 로컬 준비 완료와 분리한다.
- Git 커밋이 허용되고 저장소가 준비되어 있다면 자기 티켓 변경만 커밋한다. 형식: `<type>(<scope>): <description> (RS-XXX)`. 자동 push/merge 금지.

## 완료 보고 형식
```text
Ticket / Status:
Branch / Commit (실제 값, 미실행 시 이유):
Implemented:
Changed files:
Requirements / Acceptance criteria (항목별 PASS / FAIL / NOT RUN):
Verification (실행 명령, 종료 코드, 실제 결과):
Policy assumptions / Contract changes:
Remaining risks / Blockers:
Next ticket / Handoff:
```

## v2 테스트 실행 지침
- `docs/TEST_PLAN.md`, `qa/specs/RS-XXX.md`, `qa/TRACEABILITY.md`, `qa/REPORT_TEMPLATE.md`를 작업 전에 읽고 기존 요구사항에 함께 적용한다.
- 각 TC를 자동 테스트 또는 실제 수동/환경 검증과 연결한다. fixture 격리, 실제 SQLite, 고정 clock, 독립 연결 동시성 검증, 오류 주입 및 DB 전후 비교를 적용한다.
- 기존 테스트 수정 시 변경 이유를 보고한다. 실패를 숨기기 위한 assert 약화·skip·삭제 금지. 자동 수집이 0개면 테스트 완료가 아니다.
- 구현/QA 역할에서 허용된 범위의 `qa/reports/RS-XXX.md` 및 CSV 상태 갱신. 리뷰 역할은 결과를 응답으로만 보고한다(별도 저장 승인 예외). 실제 명령과 종료 코드, 실행 환경, 커밋, 실패 및 미실행 이유 기록. 민감 정보는 출력하지 않는다.
- 개발용 브라우저 도구/coverage 추가는 실제 호환성 검증 후 lock에 반영한다. 운영 의존성과 분리하고 운영 서버에서 부하/복구 테스트를 자동 수행하지 않는다.
