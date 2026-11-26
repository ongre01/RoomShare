# REST API 계약 (초안)
모든 날짜는 YYYY-MM-DD. JSON 응답. 오류 형식: `{"code":"DATE_CONFLICT","message":"예약할 수 없는 날짜입니다","dates":["2026-12-10"]}`. 400/422 입력 오류, 403 권한 거부, 404 미존재, 409 충돌, 429 요청 제한, 503 DB 일시 오류.

| Method | Path | Request / Response |
|---|---|---|
| GET | /api/calendar?month=2026-12 | dates: [{date,status}], today, min_date,max_date; 상태 AVAILABLE/RESERVED/BLOCKED/PAST |
| POST | /api/reservations | {name,memo,dates:[]} → 201 {id,dates,status,cancel_token} |
| POST | /api/reservations/cancel | {id,cancel_token} → {id,status}; 잘못된 토큰/ID는 동일 오류 |
| GET | /api/admin/reservations?month=2026-12 | 예약 목록, 날짜, 이름, 메모, 상태 (토큰 제외) |
| POST | /api/admin/reservations | {name,memo,dates:[]} → 201 |
| PATCH | /api/admin/reservations/{id} | {name,memo,dates:[]} → 변경 결과 |
| DELETE | /api/admin/reservations/{id} | 취소 결과 |
| POST | /api/admin/blocked-dates | {dates:[],memo} → 등록 결과 |
| DELETE | /api/admin/blocked-dates/{date} | 해제 결과 |
| GET | /api/admin/logs | 페이지네이션된 변경 이력 |
| GET | /api/admin/settings | 설정 |
| PATCH | /api/admin/settings | {booking_horizon_days} → 설정 |
| GET | /healthz | 최소 상태 응답, 내부 정보 제외 |

RS-001 구현 계약: `GET /healthz`는 200 및 정확히 `{"status":"ok"}`를 반환한다. DB 준비 상태를 검사하는 endpoint는 아니다. `/`는 준비 화면, `/static/`은 기본 CSS만 제공한다. 관리자 경로는 미구현 404이며 위 예약·관리자 API는 후속 티켓에서 구현한다. 자동 OpenAPI/Swagger/ReDoc 경로는 비활성화한다.

관리자 경로는 관리자 호스트와 검증된 내부 접근 신호 필요. 변경 요청에는 동일 Origin 검증과 CSRF 방어. 공개 API는 CORS 기본 비허용, 요청 크기/빈도 제한. 이름 1~40자, 메모 최대 200자, 날짜 중복 입력 거부, 서버 설정 범위 검증. 공개 예약 생성 응답 및 취소 응답은 Cache-Control: no-store.

## RS-003 서비스 인계 (HTTP 경로는 미구현)

`create_reservation(db_path, *, name, dates, memo="", clock=utc_now, busy_timeout_ms=5000)`와 `get_calendar(db_path, month, *, clock=utc_now, busy_timeout_ms=5000)`의 반환 dict는 위 생성/달력 payload와 일치한다. 이름은 앞뒤 Unicode 공백을 제거한 뒤 1~40 code point로 검증하며 메모는 그대로 최대 200 code point다. 메모 생략은 빈 문자열, 명시적 null은 서비스에서 거부한다. 날짜는 비어 있지 않은 문자열 list, 날짜/월 형식은 각각 정확한 ASCII YYYY-MM-DD/YYYY-MM이다.

| 서비스 예외 | 후속 HTTP 매핑 | 노출 가능한 정보 |
|---|---|---|
| `InvalidInputError` | 400/422 (RS-004에서 확정) | `code=INVALID_INPUT`, 필드명과 고정 안내 |
| `DateConflictError` | 409 | `code=DATE_CONFLICT`, `dates`의 충돌 날짜만 |
| `TemporaryDatabaseError` | 503 | `code=DATABASE_BUSY`, 고정 일시 장애 안내 |
| `InvalidPolicyError` / 예상외 SQLite 오류 | 내부 오류로 처리 | 원본 오류/SQL/설정/입력값은 숨김 |

생성 응답의 토큰은 commit 이후 반환하는 명시적 예외다. 생성 결과 전체를 로깅/캐시하지 않으며 no-store와 사용자에게 1회 표시하는 HTTP/UI 처리는 RS-004/005에서 구현한다. 달력은 이름/메모/예약 ID/토큰을 포함하지 않는다. 기간 밖 미래도 AVAILABLE/RESERVED/BLOCKED를 유지하므로 소비자는 서버의 min_date/max_date를 함께 적용한다. 날짜 계산은 서버 KST이며 브라우저 시간대에 의존하지 않는다. 상세 트랜잭션/helper 계약은 DESIGN의 RS-003 절을 따른다.
