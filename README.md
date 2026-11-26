# RoomShare — AI Agent Starter
개인 원룸 하나를 날짜 단위로 공유하는 모바일 웹 프로젝트. 이 패키지는 구현 전 스캐폴드 및 개발 티켓이며 실행 가능한 완성 서비스가 아니다.

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
`docs/TEST_PLAN.md`와 `qa/README.md`부터 읽는다. 전체 126개 테스트 명세가 `qa/specs/` 및 `qa/TEST_CASES.csv`에 있다. 각 티켓의 Agent는 실제 테스트 코드와 실행 보고서를 작성한다. 기존 개발 티켓/프롬프트에 v2 심화 절차를 추가했다. 실제 소스와 테스트 구현은 아직 수행하지 않았다.

## v3 Agent 진입점
- `AGENTS.md`: 공통 진입점과 필수 파일 로딩 규칙.
- `CLAUDE.md`: Claude Code용 import 진입점.
- `prompts/COMMON.md`: 상세 작업·테스트·Git·보고 규칙.
- 실제 Agent에서 자동 로딩을 확인하는 절차는 `prompts/README.md` 참조. 파일 참조는 지침이며 강제 실행이나 테스트 통과를 보장하지 않는다.
- v2의 개발 티켓 10개와 테스트 명세 126개는 변경하지 않았다. 이번 변경은 Agent 실행 지침에 한정한다.
