# OCI 배포 체크리스트 (실행 전 계획)
1. 실제 OCI VM/Ubuntu/Python/RAM/디스크 및 과금 한도 확인. DNS 두 호스트 확보. 포트 443 공개, 80은 인증서 검증/리다이렉트 용도, SSH는 관리 IP 제한.
2. 전용 비권한 서비스 계정 생성. `/opt/roomshare` 코드, `/var/lib/roomshare` DB, `/etc/roomshare` 설정. 파일 권한 최소화.
3. Python venv 구성 및 테스트 검증된 requirements.lock 설치. DB 마이그레이션 실행. systemd에 단일 worker, loopback bind, 재시작 정책 및 로그 설정.
4. Nginx 사용자/관리자 가상 호스트 분리, TLS 인증서 적용. 관리자 mTLS CA 및 클라이언트 인증서 발급/폐기 절차 준비. 사용자 호스트에서 관리자 UI/API 차단, 전달 헤더 덮어쓰기. `nginx -t` 후 reload.
5. SQLite backup API 또는 sqlite3 .backup 기반 일일 백업; WAL 파일 단순 복사 금지. 권한 제한, 별도 위치 보관, 보존 정책 적용, 복구 시험.
6. 모바일 Safari/Chrome, 외부 사용자 및 관리자 인증서 유무, 중복 예약, 재시작 후 지속성 검증. 실패 시 이전 릴리스/호환 DB 백업으로 복구.

실제 서버/도메인/인증서는 제공되지 않았으므로 이 문서는 배포 실행 결과가 아니다. PWA는 핵심 기능 이후 선택 적용; 관리자/예약 응답을 서비스워커에 캐시하지 않는다.
