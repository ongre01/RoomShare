# Agent 설정 변경 기록

## 변경 목적
Claude Code와 Codex에서 공통 지침을 재사용하며 티켓별 필수 문서 누락을 줄인다.

## 변경 파일
- 신규 `CLAUDE.md`: `@AGENTS.md`, `@prompts/COMMON.md` import.
- `AGENTS.md`: 공통 지침 참조, 필수 문서 목록, 역할 및 BLOCKED 규칙.
- `prompts/COMMON.md`: 구현/QA/리뷰 역할 분리 및 로딩 확인.
- `prompts/REVIEW.md`: 공통 지침 참조 및 읽기 전용 리뷰 범위 명확화.
- `README.md`, `prompts/README.md`: 짧은 실행 프롬프트, 명시적 읽기 대체 방법과 실제 로딩 확인 절차.

## 유지 범위
RS-001~RS-010 티켓 및 티켓별 프롬프트, QA 테스트 명세/CSV/추적표, 기존 요구사항·API·설계·배포 문서와 소스 스캐폴드는 v2 그대로 유지한다. 모든 티켓과 테스트 상태도 유지한다.

## 제약 및 검증
자동 로딩은 Agent 버전과 실행 설정에 영향을 받으며 Markdown 지침은 강제 실행 장치가 아니다. 실제 Agent 실행 및 앱 테스트는 이 문서 변경에서 수행하지 않는다. 운영 배포나 CI 변경은 포함하지 않는다. 실제 세션 확인 방법은 `prompts/README.md`에 있다.
