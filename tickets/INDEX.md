# 개발 티켓 인덱스
상태: TODO / IN_PROGRESS / DONE / BLOCKED. 모든 티켓은 초기 TODO. P0은 MVP, P2는 선택 확장.
| ID | 제목 | 우선순위 | 선행 | 상태 |
|---|---|---|---|---|
| [RS-001](RS-001.md) | 프로젝트 초기화 | P0 | 없음 | DONE |
| [RS-002](RS-002.md) | SQLite 스키마 및 마이그레이션 | P0 | RS-001 | DONE |
| [RS-003](RS-003.md) | 예약 엔진 및 충돌 방지 | P0 | RS-002 | DONE |
| [RS-004](RS-004.md) | 사용자 예약 API 및 취소 | P0 | RS-003 | TODO |
| [RS-005](RS-005.md) | 사용자 모바일 웹 | P0 | RS-004 | TODO |
| [RS-006](RS-006.md) | 관리자 접근 제어 및 API | P0 | RS-003 | TODO |
| [RS-007](RS-007.md) | 관리자 모바일 웹 | P0 | RS-006 | TODO |
| [RS-008](RS-008.md) | OCI 배포 및 백업 | P0 | RS-005,RS-007 | TODO |
| [RS-009](RS-009.md) | 통합 테스트 및 릴리스 | P0 | RS-008 | TODO |
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
