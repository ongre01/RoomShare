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

RS-001 구현 계약: `GET /healthz`는 200 및 정확히 `{"status":"ok"}`를 반환한다. DB 준비 상태를 검사하는 endpoint는 아니다. RS-005의 `/`는 사용자 예약 화면이며 `/static/`은 자체 CSS/JavaScript를 제공한다. 관리자 경로는 미구현 404이며 예약 API는 아래 RS-004 절에서 구현하며 관리자 API는 후속 티켓에서 구현한다. 자동 OpenAPI/Swagger/ReDoc 경로는 비활성화한다.

관리자 경로는 관리자 호스트와 검증된 내부 접근 신호 필요. 변경 요청에는 동일 Origin 검증과 CSRF 방어. 공개 API는 CORS 기본 비허용, 요청 크기/빈도 제한. 이름 1~40자, 메모 최대 200자, 날짜 중복 입력 거부, 서버 설정 범위 검증. 공개 예약 생성 응답 및 취소 응답은 Cache-Control: no-store.

## RS-003 서비스 인계 (HTTP 연결은 RS-004)

`create_reservation(db_path, *, name, dates, memo="", clock=utc_now, busy_timeout_ms=5000)`와 `get_calendar(db_path, month, *, clock=utc_now, busy_timeout_ms=5000)`의 반환 dict는 위 생성/달력 payload와 일치한다. 이름은 앞뒤 Unicode 공백을 제거한 뒤 1~40 code point로 검증하며 메모는 그대로 최대 200 code point다. 메모 생략은 빈 문자열, 명시적 null은 서비스에서 거부한다. 날짜는 비어 있지 않은 문자열 list, 날짜/월 형식은 각각 정확한 ASCII YYYY-MM-DD/YYYY-MM이다.

| 서비스 예외 | HTTP 매핑 | 노출 가능한 정보 |
|---|---|---|
| `InvalidInputError` | 422 (RS-004) | `code=INVALID_INPUT`과 고정 안내 (필드/입력 상세 미노출) |
| `DateConflictError` | 409 | `code=DATE_CONFLICT`, `dates`의 충돌 날짜만 |
| `TemporaryDatabaseError` | 503 | `code=DATABASE_BUSY`, 고정 일시 장애 안내 |
| `InvalidPolicyError` / 예상외 SQLite 오류 | 내부 오류로 처리 | 원본 오류/SQL/설정/입력값은 숨김 |

생성 응답의 토큰은 commit 이후 반환하는 명시적 예외다. 생성 결과 전체를 로깅/캐시하지 않으며 HTTP no-store는 RS-004, 사용자 화면의 1회 표시는 RS-005에서 처리한다. 달력은 이름/메모/예약 ID/토큰을 포함하지 않는다. 기간 밖 미래도 AVAILABLE/RESERVED/BLOCKED를 유지하므로 소비자는 서버의 min_date/max_date를 함께 적용한다. 날짜 계산은 서버 KST이며 브라우저 시간대에 의존하지 않는다. 상세 트랜잭션/helper 계약은 DESIGN의 RS-003 절을 따른다.

## RS-004 공개 HTTP 계약

공개 경로 3개를 구현한다. 관리자 경로는 계속 미구현 404이며 자동 API 문서는 비활성이다. POST 본문은 `application/json` 객체만 받는다. 알 수 없는 필드·타입 강제 변환·null을 허용하지 않는다. 이름/메모의 UTF-8로 표현할 수 없는 단독 surrogate는 422다. 이름은 strip 후 1~40자, 메모는 생략 시 빈 문자열/최대 200자, 날짜는 비어 있지 않은 문자열 배열이며 RS-003의 날짜·DB 기간 검증을 적용한다. GET은 `month` 하나만, POST는 query 없이 요청한다. 취소는 `{id,cancel_token}` 본문만 받으며 URL/query 토큰은 지원하지 않는다. ID와 토큰의 잘못된 문자열 값은 아래 공통 404다. 타입 오류·누락은 422다.

| 상황 | HTTP | code / 고정 message |
|---|---|---|
| 모호하거나 잘못된 Content-Length | 400 | `INVALID_REQUEST` / 올바른 요청이 아닙니다 |
| 잘못된 JSON/인코딩·파서 깊이 초과 | 400 | `INVALID_JSON` / 올바른 JSON 본문이 필요합니다 |
| 필드·타입·날짜·기간·query 오류 | 422 | `INVALID_INPUT` / 입력값을 확인해 주세요 |
| JSON 아닌 Content-Type | 415 | `UNSUPPORTED_MEDIA_TYPE` / application/json 본문이 필요합니다 |
| ID 미존재·잘못된 ID/토큰 | 404 | `CANCELLATION_NOT_FOUND` / 예약 정보 또는 취소 토큰을 확인해 주세요 |
| 오늘/과거 포함 취소 | 409 | `CANCELLATION_NOT_ALLOWED` / 오늘 또는 과거 날짜를 포함한 예약은 취소할 수 없습니다 |
| 예약/차단 날짜 충돌 | 409 | `DATE_CONFLICT` / 예약할 수 없는 날짜입니다; 정렬된 충돌 `dates` 추가 |
| 본문 크기 초과 | 413 | `REQUEST_TOO_LARGE` / 요청 본문이 너무 큽니다 |
| 빈도 초과 | 429 | `RATE_LIMITED` / 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요; `Retry-After` 초 |
| SQLite BUSY/LOCKED | 503 | `DATABASE_BUSY` / 잠시 후 다시 시도해 주세요 |
| 정책 DB 손상·예상외 서버 오류 | 500 | `INTERNAL_ERROR` / 요청을 처리할 수 없습니다 |
| 공개 경로 미존재/지원하지 않는 메서드 | 404/405 | `NOT_FOUND` / 요청한 경로를 찾을 수 없습니다 또는 `METHOD_NOT_ALLOWED` / 허용되지 않은 요청 방식입니다 |

오류 JSON은 `{code,message}`이고 날짜 충돌만 `dates`를 추가한다. raw input, validation 상세, ID/토큰/이름/메모, SQL, traceback은 포함하지 않는다. 생성 201은 정확히 `{id,dates,status:"CONFIRMED",cancel_token}`, 취소 200은 `{id,status:"CANCELLED"}`다. 생성 성공의 본인 취소 토큰만 평문 반환 예외다. 유효한 토큰으로 이미 취소한 예약을 다시 취소하면 동일한 200을 반환하되 상태·UTC 시각·점유·이력은 변경하지 않는다. 인증은 반복 여부 확인보다 먼저 수행한다.

취소는 하나의 BEGIN IMMEDIATE에서 조회/상수 시간 해시 비교/현재 KST 날짜 검사/점유 해제/상태와 UTC 시각 갱신/CANCEL 이력을 처리한다. 모든 활성 점유가 오늘보다 미래일 때 전체 취소한다. 예약 가능 기간을 줄였더라도 미래 예약의 취소는 허용한다. 이력은 날짜 before/after만 보관한다.

모든 공개 API 응답(달력·오류·요청 제한 포함)은 `Cache-Control: no-store`다. CORS 허용 헤더는 추가하지 않는다. 이는 브라우저 읽기 정책이며 인증이나 직접 HTTP 요청 차단 수단이 아니다.

요청 제한의 임시 기본값은 본문 16,384 bytes와 앱 전체 60초당 120요청이다. `ROOMSHARE_MAX_BODY_BYTES`, `ROOMSHARE_RATE_LIMIT_REQUESTS`, `ROOMSHARE_RATE_LIMIT_WINDOW_SECONDS` 1~2,147,483,647 정수로 조정한다. 실제 수신 bytes를 검사하므로 chunked/Content-Length 누락·과소 표기로 우회할 수 없다. 모든 공개 API 요청은 하나의 프로세스 내 고정 시간창을 공유하며 초과 요청은 429다. IP를 구분하거나 X-Forwarded-For/Forwarded를 신뢰하지 않는다. 따라서 IP 헤더 위조로 새 한도를 얻지 못한다. 단일 worker 운영이 전제이며 재시작 시 카운터는 초기화된다. 분산 한도, 프록시 연결/시간 제한 및 실제 신뢰 IP 설정·검증은 RS-008에 남는다. 운영 수치는 승인 전 임시값이다.

RS-005 사용자 UI: 생성 응답 손실은 성공 여부 불명이므로 자동 재제출하지 않는다. 토큰은 생성 성공 화면에서만 1회 표시하고 URL/storage/로그에 넣지 않는다. 달력 enum은 기존 4개를 유지하며 선택 가능 여부는 min_date/max_date도 검사한다. 공개 API의 경로·응답·오류 계약은 변경하지 않는다. `GET /`은 DB를 읽지 않고 서버 KST 초기 월을 HTML에 제공하며 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`를 적용한다. 상세 UI 동작은 DESIGN의 RS-005 절을 따른다.
