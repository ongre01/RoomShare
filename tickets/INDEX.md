# 개발 티켓 인덱스
상태: TODO / IN_PROGRESS / DONE / BLOCKED. 모든 티켓은 초기 TODO. P0은 MVP, P2는 선택 확장.
| ID | 제목 | 우선순위 | 선행 | 상태 |
|---|---|---|---|---|
| [RS-001](RS-001.md) | 프로젝트 초기화 | P0 | 없음 | DONE |
| [RS-002](RS-002.md) | SQLite 스키마 및 마이그레이션 | P0 | RS-001 | DONE |
| [RS-003](RS-003.md) | 예약 엔진 및 충돌 방지 | P0 | RS-002 | DONE |
| [RS-004](RS-004.md) | 사용자 예약 API 및 취소 | P0 | RS-003 | DONE |
| [RS-005](RS-005.md) | 사용자 모바일 웹 | P0 | RS-004 | DONE |
| [RS-006](RS-006.md) | 관리자 접근 제어 및 API | P0 | RS-003 | DONE |
| [RS-007](RS-007.md) | 관리자 모바일 웹 | P0 | RS-006 | DONE |
| [RS-008](RS-008.md) | OCI 배포 및 백업 | P0 | RS-005,RS-007 | BLOCKED |
| [RS-009](RS-009.md) | 통합 테스트 및 릴리스 | P0 | RS-008 | IN_PROGRESS (로컬 검증만) |
| [RS-010](RS-010.md) | PWA 홈 화면 지원 | P2 | RS-009 | TODO |

RS-004/005와 RS-006/007은 RS-003 이후 독립 진행 가능. RS-008에서 통합한다. CSV는 일반 백로그 형식이며 Jira 필드 매핑은 가져오기 시 설정한다.

## 상세 실행 프롬프트
공통 지침: [COMMON](../prompts/COMMON.md). 각 티켓 시작 전에 읽는다.

- [RS-001 프롬프트](../prompts/RS-001.md)
- [RS-002 프롬프트](../prompts/RS-002.md)
- [RS-003 프롬프트](../prompts/RS-003.md)
- [RS-004 프롬프트](../prompts/RS-004.md)
- [RS-005 프롬프트](../prompts/RS-005.md)
- [RS-006 프롬프트](../prompts/RS-006.md)
- [RS-007 프롬프트](../prompts/RS-007.md)
- [RS-008 프롬프트](../prompts/RS-008.md)
- [RS-009 프롬프트](../prompts/RS-009.md)
- [RS-010 프롬프트](../prompts/RS-010.md)

## QA 명세
- [RS-001 테스트 명세](../qa/specs/RS-001.md): 10개, 초기 NOT RUN
- [RS-002 테스트 명세](../qa/specs/RS-002.md): 11개, 초기 NOT RUN
- [RS-003 테스트 명세](../qa/specs/RS-003.md): 14개, 초기 NOT RUN
- [RS-004 테스트 명세](../qa/specs/RS-004.md): 14개, 초기 NOT RUN
- [RS-005 테스트 명세](../qa/specs/RS-005.md): 13개, 초기 NOT RUN
- [RS-006 테스트 명세](../qa/specs/RS-006.md): 15개, 초기 NOT RUN
- [RS-007 테스트 명세](../qa/specs/RS-007.md): 13개, 초기 NOT RUN
- [RS-008 테스트 명세](../qa/specs/RS-008.md): 16개, 초기 NOT RUN
- [RS-009 테스트 명세](../qa/specs/RS-009.md): 12개, 초기 NOT RUN
- [RS-010 테스트 명세](../qa/specs/RS-010.md): 8개, 초기 NOT RUN

## RS-001 검증 증거
2026-11-26: TC 10/10 PASS, 전체 pytest 66 PASS, 깨끗한 런타임/개발 venv 및 loopback HTTP/종료 검증 PASS. [완료 보고서](../qa/reports/RS-001.md). 후속 티켓 및 전체 릴리스는 미완료.

## RS-002 검증 증거
2026-11-26: TC 11/11 PASS, 대상 pytest 82 PASS, 전체 pytest 148 PASS. 실제 임시 SQLite 제약·migration rollback·독립 writer 경합·연결 정리 및 CLI 검증 PASS. [완료 보고서](../qa/reports/RS-002.md). 후속 티켓 미착수, 전체 공개 릴리스는 미완료.

## RS-003 검증 증거
2026-11-26: TC 14/14 PASS, 대상 pytest 134 PASS, 전체 pytest 282 PASS. 동일 날짜·부분 중복 독립 연결 경쟁 각각 20회, 부분 쓰기/로그/COMMIT 실패 rollback, KST 날짜/DB 정책 및 토큰·개인정보 검증 PASS. [완료 보고서](../qa/reports/RS-003.md). 후속 티켓 미착수, 전체 공개 릴리스는 미완료.

## RS-004 검증 증거
2026-11-26: TC 14/14 PASS, 대상 pytest 171 PASS, 전체 pytest 453 PASS. 공개 HTTP 계약·미래일 원자적 취소·독립 연결 취소 경쟁 20회·rollback·no-store·민감정보 비노출·본문/빈도 제한 및 실제 loopback HTTP 검증 PASS. [완료 보고서](../qa/reports/RS-004.md). 후속 티켓 미착수, 실제 프록시/OCI와 전체 공개 릴리스는 미완료.

## RS-005 검증 증거
2026-11-26: 로컬 TC 12 PASS, 실기기 TC-005-13 NOT RUN. 대상 pytest 29 PASS, fixture 격리 수정 및 키보드 보강 후 E2E+기존 제한 76 PASS, 깨끗한 런타임/개발 venv 및 전체 pytest 482 PASS(실패·skip 0). 실제 Chromium 360/390/1280px, 복수 월 예약·충돌·취소·응답 역전·토큰 비저장 검증 PASS. [완료 보고서](../qa/reports/RS-005.md). 필수 로컬 완료 조건에 따라 DONE이며 실기기/추가 WebKit/OCI 검증과 전체 공개 릴리스는 별도 미완료. 후속 티켓 미착수.

## RS-006 검증 증거
2026-11-26: 필수 로컬 TC15/15 PASS, 대상506 PASS(보안271/API139/서비스96), 전체 pytest988 PASS(실패·skip0). 관리자 기본 차단·proxy/host/Origin/CSRF, 전체 관리 API, 수정/차단/설정 rollback·날짜 PK·독립 예약/차단 경쟁20회·개인정보 보호 검증 PASS. [완료 보고서](../qa/reports/RS-006.md). 실제 Nginx/mTLS/외부 헤더 위조/OCI는 NOT RUN으로 RS-008/009에 인계하며 공개 릴리스는 미완료. 후속 티켓 미착수.

## RS-007 검증 증거
2026-11-26: 로컬 TC12 PASS, 실제 장치 TC-007-13 NOT RUN. 대상50 PASS(페이지8/E2E42), 전체 pytest1038 PASS(실패·skip0). 실제 Chromium360/390/1280px, 관리 기능 정상·오류, 월 경계/충돌 입력 보존, 정확한 Origin/CSRF, 확인/중복/응답 손실·pagehide·안전 렌더링 검증 PASS. [완료 보고서](../qa/reports/RS-007.md). TEST_PLAN의 필수 로컬 게이트에 따라 DONE이며 실기기·실제 Nginx/mTLS·OCI·공개 릴리스는 별도 미완료. 후속 티켓 미착수.

## RS-008 검증 증거
2026-11-27: 대상 자동 17 PASS, 전체 회귀 1,055 PASS(실패·skip 0). 로컬 TLS/mTLS·헤더 경계·설치/재실행·WAL 백업/격리 복원 검증. 명세 16개: PASS 10, BLOCKED 3(전용 systemd 권한·재시작·timer), NOT RUN 3(외부 클라이언트·OCI 2개). [실행 명령·전체 회귀·인계](../qa/reports/RS-008.md). 필수 게이트 미검증으로 BLOCKED, 공개 NO-GO. RS-009 미착수.
