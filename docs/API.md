# REST API 계약 (초안)
모든 날짜는 YYYY-MM-DD. JSON 응답. 오류 형식: `{"code":"DATE_CONFLICT","message":"예약할 수 없는 날짜입니다","dates":["2026-12-10"]}`. 400/422 입력 오류, 403 권한 거부, 404 미존재, 409 충돌, 429 요청 제한, 503 DB 일시 오류.

| Method | Path | Request / Response |
|---|---|---|
| GET | /api/calendar?month=2026-12 | dates: [{date,status}], today, min_date,max_date; 상태 AVAILABLE/RESERVED/BLOCKED/PAST |
| POST | /api/reservations | {name,memo,dates:[]} → 201 {id,dates,status,cancel_token} |
| POST | /api/reservations/cancel | {id,cancel_token} → {id,status}; 잘못된 토큰/ID는 동일 오류 |
| GET | /api/admin/csrf | {csrf_token,expires_in:1800}; 인증된 관리자만 발급 |
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

RS-001 구현 계약: `GET /healthz`는 200 및 정확히 `{"status":"ok"}`를 반환한다. DB 준비 상태를 검사하는 endpoint는 아니다. RS-005의 `/`는 사용자 예약 화면이며 `/static/`은 자체 CSS/JavaScript를 제공한다. 공개 예약 API는 아래 RS-004, 관리자 API는 RS-006 절을 따른다. 자동 OpenAPI/Swagger/ReDoc 경로는 비활성화한다.

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

공개 경로 3개를 구현한다. 관리자 경로는 아래 RS-006에서 별도로 보호하며 자동 API 문서는 비활성이다. POST 본문은 `application/json` 객체만 받는다. 알 수 없는 필드·타입 강제 변환·null을 허용하지 않는다. 이름/메모의 UTF-8로 표현할 수 없는 단독 surrogate는 422다. 이름은 strip 후 1~40자, 메모는 생략 시 빈 문자열/최대 200자, 날짜는 비어 있지 않은 문자열 배열이며 RS-003의 날짜·DB 기간 검증을 적용한다. GET은 `month` 하나만, POST는 query 없이 요청한다. 취소는 `{id,cancel_token}` 본문만 받으며 URL/query 토큰은 지원하지 않는다. ID와 토큰의 잘못된 문자열 값은 아래 공통 404다. 타입 오류·누락은 422다.

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

## RS-006 관리자 HTTP 계약

`/admin`, `/api/admin` 및 각각의 모든 하위 경로·메서드를 라우팅과 본문 해석 전에 보호한다. 정확한 관리자 Host와 검증된 컨텍스트가 필요하다. 기본 거부이며 미인증은 없는 경로·메서드에도 403 `ADMIN_FORBIDDEN`이다. 인증된 미구현 `/admin`은 404, 지원하지 않는 메서드는 405다. 관리자 화면은 RS-007 범위다. 운영 프록시와 테스트 주입의 차이는 [신뢰 계약](DEPLOYMENT.md)을 따른다.

인증된 `GET /api/admin/csrf`는 `{csrf_token,expires_in:1800}`을 반환한다. 앱 시작 시 생성한 메모리 전용 256-bit 키로 관리자 호스트·발급 UTC epoch·32바이트 nonce에 HMAC-SHA256 서명한다. DB 변경/로그/영구 저장이 없으며 다른 앱·재시작 후 무효다. 모든 POST/PATCH/DELETE는 단일 `Origin: https://<admin_host>`와 단일 `X-CSRF-Token` 헤더가 필요하다. Origin은 포트/경로/마지막 슬래시를 허용하지 않는 정확 일치다. 토큰은 `0 <= 경과초 < 1800`만 유효하며 미래 발급·만료·변조·누락·중복은 403 `CSRF_REJECTED`다. 로그인/쿠키/URL 전달은 없다. 클라이언트는 토큰을 메모리에만 보관하고 만료 시 재발급한다. 실패한 쓰기는 자동 재제출하지 않는다.

POST/PATCH는 strict JSON 객체만 받으며 추가 필드·타입 강제 변환·null을 금지한다. PATCH 생략 필드는 유지하고 빈 객체는 422다. 이름 strip 후 1~40자, 메모 최대 200자이며 단독 surrogate는 422다. 변경 query·DELETE 본문·GET 본문·알려지지 않은 query·중복 query도 422다. 인증/CSRF 이후 기존 본문 bytes 제한과 관리자 전용 고정 시간창 카운터를 적용한다(공개와 설정값은 같고 카운터는 별개). 400/413/415/422/429/503/500은 RS-004의 고정 오류 형식·메시지를 사용한다. 관리자 성공/거부/오류 응답 모두 `Cache-Control: no-store`다.

| 동작 | 입력 / 응답 및 정책 |
|---|---|
| GET reservations | `month=YYYY-MM` 필수, `status=CONFIRMED` 기본 또는 `CANCELLED`/`ALL`. `{reservations:[{id,name,memo,status,created_at,cancelled_at,dates}]}`. 해당 월 날짜가 있는 예약을 created_at ASC, id ASC로 반환하고 `dates`는 월 밖을 포함한 정렬된 전체 날짜. 취소 예약은 취소 이력의 before 날짜로 조회. |
| POST reservations | `{name,dates,memo?}` → 201 `{id,dates,status:"CONFIRMED"}`. 공개 생성 서비스를 재사용하고 취소 토큰은 반환 없이 폐기한다. DB에는 해시만 저장, 기존 `CREATE` 이력 사용. |
| PATCH reservations/{id} | `{name?,memo?,dates?}` → 200 위 예약 객체. dates는 전체 교체이며 생략 시 보존. 자기 점유는 충돌에서 제외하고 추가 날짜만 현재 예약 기간 검사. 기간 축소로 기존 날짜를 잃지 않는다. 정보/점유/`ADMIN_UPDATE` 한 건을 원자적 변경. 같은 값은 이력 없음. |
| DELETE reservations/{id} | 200 `{id,status:"CANCELLED"}`. 점유 삭제·상태·UTC 취소시각·`ADMIN_CANCEL` 이력 원자적 처리. 반복 200은 추가 변경 없음. 미존재 404 `NOT_FOUND`. |
| POST blocked-dates | `{dates,memo?}` → 201 `{dates,status:"BLOCKED"}`. 현재 기간 내 날짜만. 예약/차단 일부 충돌도 409 `DATE_CONFLICT`와 충돌 날짜를 반환하고 전체 롤백. `BLOCK` 한 건. |
| DELETE blocked-dates/{date} | 200 `{date,status:"AVAILABLE"}`. BLOCKED만 해제·`UNBLOCK` 기록. RESERVED/미존재는 404 `NOT_FOUND`, 예약 삭제 없음. 기간 밖 미래 차단도 해제 가능. |
| GET logs | `page=1`, `page_size=50` 기본. page 1~2,147,483,647, page_size 1~100의 정규 십진수. `{logs:[{id,reservation_id,action,detail,created_at}],page,page_size,total}`. created_at DESC, id DESC. 정적 데이터 중복/누락 없음; 페이지 사이 새 쓰기 발생 시 offset은 이동 가능. |
| GET/PATCH settings | GET `{booking_horizon_days}`. PATCH JSON 정수 1~366만(bool/문자열 금지). 변경과 `SETTINGS` 이력 같은 트랜잭션, 동일 값은 이력 없음. 기존 점유 삭제 없음. |

관리자 과거일 권한은 운영 승인 전 임시 제한이다. KST 오늘 이전 날짜를 하나라도 포함한 예약은 이름/메모를 포함한 모든 PATCH 및 취소를 409 `CHANGE_NOT_ALLOWED`로 거부한다. CANCELLED 수정도 거부한다. 오늘 예약은 관리자 수정/취소를 허용하고 공개 취소의 오늘 금지와 분리한다. 과거 BLOCKED 해제도 409다. 새 예약/차단의 과거일은 기존 입력 검증 422다. 기간 상한 366일과 로그 기본50/상한100은 임시값이다.

로그 detail은 날짜 동작 `{before:[날짜],after:[날짜]}`, 설정 동작 `{before:정수,after:정수}`만 반환한다. 이름/메모/토큰/해시/프록시 비밀을 복제하지 않는다. 저장된 임의 추가 JSON 필드는 조회에서 제외하고 손상된 필수 값은 일반화된 500으로 실패한다. 관리자 조회의 이름/메모 허용과 공개 달력의 상태 전용 계약을 구분한다. 로그 SQL/commit 오류도 전체 롤백하고 SQLite BUSY/LOCKED만 503으로 분류한다.
