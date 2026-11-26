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

관리자 경로는 관리자 호스트와 검증된 내부 접근 신호 필요. 변경 요청에는 동일 Origin 검증과 CSRF 방어. 공개 API는 CORS 기본 비허용, 요청 크기/빈도 제한. 이름 1~40자, 메모 최대 200자, 날짜 중복 입력 거부, 서버 설정 범위 검증. 공개 예약 생성 응답 및 취소 응답은 Cache-Control: no-store.
