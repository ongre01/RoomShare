# OCI 배포 체크리스트 (실행 전 계획)
1. 실제 OCI VM/Ubuntu/Python/RAM/디스크 및 과금 한도 확인. DNS 두 호스트 확보. 포트 443 공개, 80은 인증서 검증/리다이렉트 용도, SSH는 관리 IP 제한.
2. 전용 비권한 서비스 계정 생성. `/opt/roomshare` 코드, `/var/lib/roomshare` DB, `/etc/roomshare` 설정. 파일 권한 최소화.
3. Python venv 구성 및 테스트 검증된 requirements.lock 설치. DB 마이그레이션 실행. systemd에 단일 worker, loopback bind, 재시작 정책 및 로그 설정.
4. Nginx 사용자/관리자 가상 호스트 분리, TLS 인증서 적용. 관리자 mTLS CA 및 클라이언트 인증서 발급/폐기 절차 준비. 사용자 호스트에서 관리자 UI/API 차단, 전달 헤더 덮어쓰기. `nginx -t` 후 reload.
5. SQLite backup API 또는 sqlite3 .backup 기반 일일 백업; WAL 파일 단순 복사 금지. 권한 제한, 별도 위치 보관, 보존 정책 적용, 복구 시험.
6. 모바일 Safari/Chrome, 외부 사용자 및 관리자 인증서 유무, 중복 예약, 재시작 후 지속성 검증. 실패 시 이전 릴리스/호환 DB 백업으로 복구.

실제 서버/도메인/인증서는 제공되지 않았으므로 이 문서는 배포 실행 결과가 아니다. PWA는 핵심 기능 이후 선택 적용; 관리자/예약 응답을 서비스워커에 캐시하지 않는다.

## RS-004 요청 제한 인계 (실제 프록시 검증 전)

앱은 환경 설정으로 본문 bytes 및 프로세스 전체 요청 수를 제한하고 X-Forwarded-For/Forwarded를 읽지 않는다. 기본은 16,384 bytes, 전체 공개 API 120회/60초이며 운영 승인 전 임시값이다. 단일 worker에서 카운터를 공유하고 재시작 시 초기화한다. RS-008에서 실제 Nginx의 body/연결 시간 제한·빈도 제한·429 응답·no-store 유지와 프록시 헤더 신뢰 경계를 따로 검증한다. 여러 worker나 서버에 분산된 한도로 간주하면 안 된다. body/토큰/개인정보는 로그에 넣지 않으며, ASGI 접근 로그는 비활성화하고 프록시 접근 로그에는 raw query/전체 URI를 기록하지 않는 구성을 검증한다. URL/query 토큰 제출은 앱이 거부하지만 앞단 로그까지 소급 제거할 수 없으므로 클라이언트도 본문만 사용한다. 이 문구는 배포 완료 증거가 아니다.

## RS-006 프록시 신뢰 계약 (실제 프록시/OCI 미검증)

기본 `ROOMSHARE_ADMIN_ACCESS=deny`는 호스트/헤더와 무관하게 거부한다. 명시적 `proxy_mtls` 모드는 다음 조건을 모두 요구한다. 앱이 클라이언트 인증서를 직접 검증하는 모드는 아니다.

1. Nginx 관리자 TLS 호스트에서 신뢰하는 CA의 유효 클라이언트 인증서를 필수 검증한다. 공개 호스트·미등록 Host/SNI에서는 `/admin`, `/api/admin`과 하위 경로를 차단한다.
2. Nginx가 단일 `Host: <admin_host>`를 설정하고 외부 내부헤더를 제거/덮어쓴 뒤 `X-RoomShare-Admin-Verified: SUCCESS`, `X-RoomShare-Proxy-Secret`을 전달한다. 공개 upstream에 권한 신호를 전달하지 않는다. 중복 인증 헤더도 앱에서 거부한다.
3. 앱은 실제 transport peer가 `127.0.0.1`/`::1`인지 검사하고 환경의 `ROOMSHARE_ADMIN_PROXY_SECRET`을 상수 시간 비교한다. 비밀은 CSPRNG 32바이트 이상에서 생성한 URL-safe `[A-Za-z0-9_-]` 43~128자이며 없거나 잘못되면 시작 오류다. repr/오류/로그에 노출하지 않는다. 보호된 Nginx·앱 설정에만 저장하고 Git/브라우저에 전달하지 않는다. 회전 시 양쪽을 함께 갱신한다.
4. Uvicorn은 단일 worker, `--host 127.0.0.1 --no-proxy-headers --no-access-log`로 실행한다. ASGI peer 주소가 전달 헤더로 재작성되지 않게 하는 필수 조건이다. 앱은 Forwarded/X-Forwarded-*를 인증에 사용하지 않는다. backend 외부 연결을 OS/네트워크에서 차단한다. 현재 peer 검사는 loopback 전용이며 Unix socket 선택 시 별도 신뢰 adapter와 검증이 필요하다.
5. 앱에서 정확한 HTTPS Origin과 CSRF를 추가 검사한다. CSRF 키는 앱 시작 시 생성하는 메모리 전용 난수라 별도 환경 비밀은 없으며 30분 만료/재시작 무효다. 요청/응답 전체·인증 헤더·CSRF/취소 토큰을 앱·프록시 로그에 넣지 않는다.

`app.dependency_overrides[resolve_admin_context]`는 Python 테스트 코드에서만 인증 상태를 모사하며 Host/Origin/CSRF는 그대로 검사한다. HTTP/환경 변수로 켜는 우회 설정은 없다. TestClient/ASGI 주입의 loopback 주소·합성 비밀·SUCCESS 결과는 실제 mTLS 검증 증거가 아니다.

RS-008/009에 실제 Nginx 인증서 행렬(없음/다른 CA/만료/유효), Host/SNI·내부/Forwarded/중복 헤더 위조, 외부 backend 차단, no-store 및 크기/빈도 제한 검증을 인계한다. 이 티켓에서는 프록시 배포·방화벽·OCI를 변경하지 않았으며 해당 검증은 NOT RUN이다.
