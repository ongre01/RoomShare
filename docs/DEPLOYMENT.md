# Ubuntu 배포·복구 실행서 (RS-008)

**이 절차는 배포 승인이 아니다.** [격리 실행 결과](../qa/reports/RS-008.md)와 아래의 전용 스테이징·외부 클라이언트·OCI 게이트를 구분한다. 명령 예시는 승인된 **전용** Ubuntu에서 운영자가 실행한다. 설치 도구는 계정/방화벽/systemd/Nginx를 변경하거나 트래픽을 전환하지 않는다.

### 1. 환경과 릴리스 준비

- 로컬 시험에는 Python venv·개발 lock·Chromium 외에 `bash`, util-linux(`flock`), `systemd-analyze`, Nginx(검증 버전 1.24), OpenSSL CLI가 필요하다. `python -m pytest -q tests/test_deployment.py tests/deployment/test_proxy.py tests/test_backup_restore.py`는 개인 임시 경로와 loopback 고포트만 사용한다. 운영 인증서나 `/etc/nginx/nginx.conf`로 테스트하지 않는다.
- OCI는 별도로 VM shape/아키텍처, Ubuntu/Python/SQLite, `free -m`, `df -h`, 접근 권한, DNS A/AAAA, 비용 한도와 볼륨/백업 용량을 확인한다. 로컬 결과를 OCI 실측값으로 옮기지 않는다. OCI 접근 없이 공개하지 않는다.
- 전용 로그인 불가 계정 `roomshare:roomshare`를 마련한다(신규 전용 VM 예: `useradd --system --user-group --home-dir /var/lib/roomshare --no-create-home --shell /usr/sbin/nologin roomshare`). 기존 계정/경로라면 **먼저** 소유자와 용도를 확인한다. 앱은 root로 실행하지 않는다.

다음은 승인된 커밋을 새 릴리스로 풀 때의 예다. 릴리스/venv는 배포 관리자 소유, 서비스 계정은 읽기·실행만 가능해야 한다. 운영자가 검토한 checkout에서 실행한다. 현재 서비스 중인 릴리스에 설치하거나 기존 디렉터리에 다시 압축을 풀지 않는다.

```bash
set -euo pipefail
umask 022
rev=$(git rev-parse --verify --end-of-options "${APPROVED_REV:?}^{commit}")
release=/opt/roomshare/releases/$rev
install -d -m 0755 /opt/roomshare/releases
install -d -m 0700 /etc/roomshare
install -d -m 0700 -o roomshare -g roomshare /var/lib/roomshare /var/backups/roomshare
./scripts/deploy.sh prepare "$rev" /opt/roomshare/releases /etc/roomshare
```

`prepare`는 배포 관리자만 수정할 수 있는 checkout에서 실행한다. 두 디렉터리는 미리 준비한 신뢰된 절대 경로여야 한다. 릴리스 상위 디렉터리는 설치자 소유이고 그룹·기타 쓰기가 없어야 하며, 설정 디렉터리는 설치자 소유 0700이어야 한다. `APPROVED_REV`에는 검토한 커밋 ID를 지정한다. 작업트리나 Git 인덱스를 복사하지 않고 해당 커밋의 앱·배포 템플릿·런타임 lock·필수 스크립트만 추출한다.

새 릴리스는 최종 경로에 0700으로 만든다(venv는 이동할 수 없다). 그 커밋의 `install.sh`가 `.venv`를 만들고 **런타임 lock**을 설치한 뒤 `pip check`한다. 기존 0600 환경 파일은 보존하고, 신규 설치만 [deny 환경 예제](../deploy/roomshare.env.example)로 시작한다. 성공하면 lock의 SHA-256과 `Release ready:` 경로를 출력하고 릴리스를 0755로 연다. 이 단계는 DB를 열거나 `current`·서비스·프록시를 전환하지 않는다.

같은 커밋의 경로가 이미 있거나 `current` 링크가 끊어져 있으면 중단한다. 중간 실패로 남은 0700 디렉터리는 사용하지 않는다. 원인을 해결하고 비활성임을 확인한 뒤 다른 빈 이름으로 격리하고 `prepare`를 다시 실행한다. **종료 코드 0과 `Release ready:`를 확인한 뒤에만** §2~3으로 진행한다. 개발용 [.env 예제](../.env.example), 실제 DB·인증서·키는 릴리스에 복사하지 않는다.

### 2. 호스트·인증서·프록시 준비

1. `/etc/roomshare/roomshare.env`(root:root 0600)의 두 DNS와 절대 DB 경로·기간·제한을 확인한다. [Nginx 사이트](../deploy/nginx/roomshare.conf)의 두 `server_name`과 `map` 이름도 **같이** 교체한다. 서버 chain(`/etc/roomshare/tls/server-chain.pem`)은 두 호스트를 포함해야 한다. 서버 키(`server.key`)는 root만 읽는다. 관리자 전용 발급 CA의 **공개** 체인(`admin-ca.pem`)과 현재 CRL(`admin-ca.crl`)만 설치한다. CA 서명 키는 서버에 두지 않는다.
2. 운영자의 비밀 관리 경로에서 CSPRNG 32바이트 이상의 URL-safe 값 하나를 생성한다. root만 접근 가능한 앱 환경에 `ROOMSHARE_ADMIN_PROXY_SECRET`, root:root 0600 `/etc/roomshare/admin-proxy-header.conf`에 `proxy_set_header X-RoomShare-Proxy-Secret "<같은_보호값>";` 한 줄로 제공한다. 값 자체를 인자·터미널·로그·Git에 출력하지 않는다. `nginx -T`, 환경 전체 출력, 셸 xtrace는 사용하지 않는다. 전용 스테이징에서만 `proxy_mtls`로 함께 시험한 뒤 승인된 환경에 적용한다. `deny`에서는 인증서가 있어도 앱이 관리자 접근을 거부한다.
3. [공통 설정](../deploy/nginx/roomshare-proxy.conf)을 `/etc/nginx/snippets/roomshare-proxy.conf`, 환경별 사이트를 Nginx `http` 문맥에 배치한다. **전용** listener에서 배포판 기본 사이트와 `default_server` 충돌 여부, 상위 `real_ip`/캐시/로그 설정도 확인한다. 알 수 없는 TLS SNI는 handshake를 거부하고 SNI·Host 불일치도 거부한다. 평문 80은 프록시/리다이렉트 없이 거부한다. 불필요하면 80 listener와 인바운드 규칙을 열지 않는다. 임의 호스트로 HTTPS 이동하거나 query를 재전달하지 않는다.
4. 관리자 호스트 전체는 유효하고 폐기되지 않은 인증서가 필수다. 매 요청의 헤더를 허용 목록으로 다시 구성한다. 사용자 호스트는 관리자 경로를 Nginx에서 차단하고 내부 인증 신호를 전혀 보내지 않는다. 상위 프록시의 IP 헤더를 신뢰하지 않는다. 앱의 정확한 Origin/CSRF 검증은 그대로다. 기본 프록시 제한은 호스트·실제 연결 IP당 2r/s, burst 20, 16KiB(임시값); 앱 카운터와 별개다. 프록시 자체의 413/429도 JSON·no-store, 429에는 Retry-After가 있다. 동적 응답을 캐시하지 않는다. 가상 호스트 접근 로그는 **상태와 시간만** 남기고 raw URI가 붙는 요청 오류 로그는 기록하지 않는다. 설정 검사·기동 실패와 집계된 4xx/5xx, 백업 unit 실패는 별도 감시한다.
5. 만료 전에 서버 chain/키, 승인된 장치의 클라이언트 인증서, CRL의 nextUpdate를 점검한다. 장치 분실/폐기는 발급 CA에서 일련번호 폐기 → 새 서명 CRL 배포 → `nginx -t` → 승인된 reload → 기존 연결 종료 확인 및 폐기/유효 인증서로 재검증. CA 교체 시 신·구 신뢰 기간을 제한하고 최종적으로 구 CA를 제거한다. 내부 비밀 회전은 유지보수 창에서 앱과 프록시를 함께 갱신한다. 실제 키 발급·교체·reload는 자동화하지 않는다.

### 3. DB 준비와 서비스 전환

전환 전 이전 릴리스 ID·lock·스키마 버전, DB 경로, 스냅샷 위치, 승인된 중단 시간과 복귀 조건을 기록한다. 기존 DB이면 먼저 **구 릴리스에서** 백업·아래 격리 복원을 연습하고 새 릴리스로 사본에 migration을 시험한다. 새 버전의 무결성 검사만으로 구 코드 호환성을 추정하지 않는다.

유지보수 중에는 유입을 차단하고 기존 timer를 정지, 실행 중인 백업이 끝난 뒤 서비스를 정지한다. 해당 unit의 `LoadState=loaded`, `ActiveState=inactive` 및 다른 writer 부재를 확인한다. 알 수 없는 상태로 진행하지 않는다. 관리자의 환경 파일을 서비스 사용자에게 읽히거나 셸로 `source`하지 않고, manager가 읽어 주도록 한다. **둘 중 한 명령만** 사용한다:

```bash
# 최초 (DB 및 sidecar가 없어야 한다)
systemd-run --wait --collect --pipe -p User=roomshare -p Group=roomshare \
  -p EnvironmentFile=/etc/roomshare/roomshare.env "$release/scripts/deploy.sh" init
# 업그레이드 (기존 DB 필수, 이전 릴리스는 검증된 실제 절대 경로)
systemd-run --wait --collect --pipe -p User=roomshare -p Group=roomshare \
  -p EnvironmentFile=/etc/roomshare/roomshare.env "$release/scripts/deploy.sh" upgrade "$previous_release"
```

`deploy.sh`는 DB별 배타 유지보수 lock을 획득한다. 앱/백업 unit의 공유 lock이 있으면 nonzero다. `upgrade`는 이전 릴리스의 도구로 검증된 스냅샷을 먼저 발행한 후에만 새 migration·`--check`를 수행한다. 초기 RS-008 도입 시 구 릴리스에 백업 도구가 없다면 기존 스키마와 호환되는 백업 릴리스를 **사본에서 먼저** 검증한다. 실패하거나 백업이 없으면 `init`로 우회하지 않는다. 전환 직전 스냅샷을 보존 정책 밖의 별도 보호 위치에도 고정한다.

DB 준비가 성공한 뒤에만 코드 포인터를 전환한다. 다음은 승인된 전용 스테이징의 예다. `current`가 예상한 이전 symlink(또는 최초에만 없음)인지 확인하고, 동일 파일시스템의 **새 이름**을 사용한다.

```bash
ln -s "$release" "/opt/roomshare/.next-$rev"
mv -T "/opt/roomshare/.next-$rev" /opt/roomshare/current
systemd-analyze verify "$release"/deploy/systemd/roomshare*
install -m 0644 "$release"/deploy/systemd/roomshare* /etc/systemd/system/
systemctl daemon-reload
systemctl start roomshare.service
curl --fail --silent http://127.0.0.1:8000/healthz
nginx -t
```

`healthz`는 DB 검사가 아니다. **별도** 달력·관리 API·CSS/JS를 실제 HTTPS로 확인한다. 프록시 reload/최초 start 및 서비스·timer의 부팅 활성화(`systemctl enable roomshare.service`, `systemctl enable --now roomshare-backup.timer`)는 검증과 운영자 승인 후 수행한다. 오류 시 트래픽을 계속 차단한다.

### 4. 온라인 백업·격리 복원·롤백

DB 계정으로 아래 도구를 실행한다. 예시의 `$db`, `$archive_dir`, `$snapshot`은 **확인한 절대 경로**다. `backup`은 WAL 사용 중에도 SQLite backup API로 복사하며 migration 버전/WAL/integrity/FK 검사를 거쳐 **단일 0600 파일**을 같은 파일시스템에서 덮어쓰기 없이 발행한다. 실패는 stderr/비정상 종료로 표시한다.

```bash
"$release/.venv/bin/python" "$release/scripts/snapshot.py" backup "$db" "$archive_dir"
"$release/.venv/bin/python" "$release/scripts/snapshot.py" restore "$snapshot"
# 또는 새 0700 디렉터리를 명시한다 (기존 디렉터리/운영 DB 교체 불가).
"$release/.venv/bin/python" "$release/scripts/snapshot.py" restore "$snapshot" --to "$new_directory"
"$release/.venv/bin/python" "$release/scripts/migrate.py" --db-path "$new_directory/roomshare.db" --check
```

- 일일 timer는 서버 현지 자정, `Persistent=yes`다. 보존 기본 `ROOMSHARE_BACKUP_RETENTION_DAYS=0`은 **무삭제**다. 운영자가 승인한 양의 일수만 설정한다. 예약 이력 90일 임시안과 무관하다. 새 정상 스냅샷 발행 뒤에만 UTC 파일명 기준 만료된 도구 소유 형식의 일반 파일을 사본에서 재검증 후 정리한다. 손상/호환 불가본은 삭제하지 않고 실패를 알린다. 최신본이나 임의 파일을 연령만으로 삭제하지 않는다. 디스크 여유·timer 지연·oneshot 종료 코드/저널을 감시한다.
- VM 내 백업만으로 볼륨 유실에 대비할 수 없다. 승인된 암호화 별도 저장소·최소권한·키 관리·보존/RPO/RTO를 결정하고 **검증된 단일 스냅샷만** 운반한다. 운영 DB/WAL을 개별 복사하지 않는다. 반출입 후 체크섬을 대조하고 주기적으로 격리 복원해 예약·차단·설정·이력을 비교한다.
- **운영 복원에는 강제 덮어쓰기 옵션이 없다.** 손실 가능 변경분과 복원 시점을 밝히고 명시적 승인을 받는다. 트래픽·서비스·timer/writer 정지 확인 → 직전 백업 및 원래 DB/WAL/SHM·환경·코드 보존 → 호환 릴리스로 `/var/lib/roomshare` 아래 새 디렉터리에 격리 복원·검사 → 기능/소유권/0600 확인 → 보호된 환경 파일의 `ROOMSHARE_DB_PATH`를 **검증된 새 파일**로 전환한다. 이전 파일이나 sidecar를 이동·삭제하지 않는다. 기존 DB가 손상되어 백업할 수 없으면 원본을 보존하고 별도 복구 승인을 받는다.
- 코드 롤백은 이전 릴리스의 `migrate.py --check`와 기능 검증이 현재 DB **사본**에서 성공할 때만 기존 DB를 재사용한다. 호환되지 않으면 위 승인 절차로 구 코드와 짝이 맞는 스냅샷을 새 경로에 복원한다. 이전 코드로 symlink를 전환하고 앱/HTTPS/데이터를 검증한 뒤에만 timer와 유입을 재개한다. 검증 실패 시 서비스는 정지 상태로 유지한다.

```bash
# 롤백 승인, 서비스 정지, 호환 DB 경로 확정 후. 새 symlink 이름은 기존에 없어야 한다.
ln -s "$previous_release" /opt/roomshare/.rollback-next
mv -T /opt/roomshare/.rollback-next /opt/roomshare/current
systemctl start roomshare.service
curl --fail --silent http://127.0.0.1:8000/healthz
```

### 5. 승인 환경에서 남기는 증거 (로컬과 OCI 별도)

| TC | 실행 및 판정 기준 |
|---|---|
| 03 | `systemctl show roomshare -p MainPID -p User -p Group -p ProtectSystem -p ReadWritePaths`; 해당 PID의 `ps -o euid,egid,args -p PID`, `ss -ltnp`, `stat`로 전용 비root UID·127.0.0.1만 listen·DB 0600/디렉터리 0700 확인. unit의 mount namespace에서 코드/설정/백업 위치 쓰기가 허용되지 않는지 확인. |
| 08 | **별도 승인된 외부 클라이언트**에서 두 도메인의 정상 443과 배포 대상의 8000 거부/timeout을 각각 기록(IPv4/공개 IPv6 모두). loopback 성공 또는 방화벽 표만으로 PASS 처리하지 않는다. |
| 10 | 스테이징에 합성 예약/차단/설정/로그를 만든 뒤 쓰기를 멈추고 5개 테이블의 정렬 논리 스냅샷·권한 비교. `systemctl restart roomshare`, 새 MainPID, healthz·달력 및 인증된 조회, 같은 스냅샷/권한 확인. 개인정보는 보고서에 남기지 않는다. |
| 14 | 전용 timer의 `systemctl list-timers roomshare-backup.timer`와 실제 발화 시각, `systemctl show roomshare-backup.service -p Result -p ExecMainStatus` 확인. 합성 DB에서 보존 경계 직전/직후·손상·실패를 시험하고 최신 정상본 유지/실패 알림을 확인. |
| 15 | 승인된 OCI 인스턴스에서만 VCN 보안 목록·NSG·OS 방화벽 및 DNS/TLS/만료·외부 mTLS·직접 접근 차단·재시작·복원을 재검증한다. SSH는 관리 CIDR, 443만 서비스 공개, 80은 별도 필요/승인 시만. 백엔드는 공개하지 않는다. 로컬이 성공해도 대체 불가. |
| 16 | 승인된 OCI 스테이징/합성 데이터에서 idle 및 합의 동시성·구간·샘플 수를 정해 CPU/RSS/디스크·p95·오류율(예상 409와 5xx 분리)을 측정한다. 사전 합의 임계값이 없으면 기준 미확정으로 기록한다. 운영 DB 부하 시험 금지. |

## 앱과 프록시 제한의 관계 (RS-004/008)

앱은 환경 설정으로 본문 bytes 및 프로세스 전체 요청 수를 제한하고 X-Forwarded-For/Forwarded를 읽지 않는다. 기본은 16,384 bytes, 전체 공개 API 120회/60초이며 운영 승인 전 임시값이다. 단일 worker에서 카운터를 공유하고 재시작 시 초기화한다. 앞단 제한은 §2와 같고 여러 worker나 서버에 분산된 한도가 아니다. URL/query 토큰 제출은 앱이 거부하지만 앞단 로그까지 소급 제거할 수 없으므로 클라이언트도 본문만 사용한다.

## RS-006 프록시 신뢰 계약

기본 `ROOMSHARE_ADMIN_ACCESS=deny`는 호스트/헤더와 무관하게 거부한다. 명시적 `proxy_mtls` 모드는 다음 조건을 모두 요구한다. 앱이 클라이언트 인증서를 직접 검증하는 모드는 아니다.

1. Nginx 관리자 TLS 호스트에서 신뢰하는 CA의 유효 클라이언트 인증서를 필수 검증한다. 공개 호스트·미등록 Host/SNI에서는 `/admin`, `/api/admin`과 하위 경로를 차단한다.
2. Nginx가 단일 `Host: <admin_host>`를 설정하고 외부 내부헤더를 제거/덮어쓴 뒤 `X-RoomShare-Admin-Verified: SUCCESS`, `X-RoomShare-Proxy-Secret`을 전달한다. 공개 upstream에 권한 신호를 전달하지 않는다. 중복 인증 헤더도 앱에서 거부한다.
3. 앱은 실제 transport peer가 `127.0.0.1`/`::1`인지 검사하고 환경의 `ROOMSHARE_ADMIN_PROXY_SECRET`을 상수 시간 비교한다. 비밀은 CSPRNG 32바이트 이상에서 생성한 URL-safe `[A-Za-z0-9_-]` 43~128자이며 없거나 잘못되면 시작 오류다. repr/오류/로그에 노출하지 않는다. 보호된 Nginx·앱 설정에만 저장하고 Git/브라우저에 전달하지 않는다. 회전 시 양쪽을 함께 갱신한다.
4. Uvicorn은 단일 worker, `--host 127.0.0.1 --no-proxy-headers --no-access-log`로 실행한다. ASGI peer 주소가 전달 헤더로 재작성되지 않게 하는 필수 조건이다. 앱은 Forwarded/X-Forwarded-*를 인증에 사용하지 않는다. backend 외부 연결을 OS/네트워크에서 차단한다. 현재 peer 검사는 loopback 전용이며 Unix socket 선택 시 별도 신뢰 adapter와 검증이 필요하다.
5. 앱에서 정확한 HTTPS Origin과 CSRF를 추가 검사한다. CSRF 키는 앱 시작 시 생성하는 메모리 전용 난수라 별도 환경 비밀은 없으며 30분 만료/재시작 무효다. 요청/응답 전체·인증 헤더·CSRF/취소 토큰을 앱·프록시 로그에 넣지 않는다.

`app.dependency_overrides[resolve_admin_context]`는 Python 테스트 코드에서만 인증 상태를 모사하며 Host/Origin/CSRF는 그대로 검사한다. HTTP/환경 변수로 켜는 우회 설정은 없다. TestClient/ASGI 주입의 loopback 주소·합성 비밀·SUCCESS 결과는 실제 mTLS 검증 증거가 아니다.

RS-008은 실제 Nginx와 임시 CA로 인증서 행렬·헤더 경계·no-store·크기/빈도 제한을 로컬 검증한다. 별도 클라이언트의 외부 backend 차단이나 OCI 검증 증거는 아니다.

## RS-007 관리자 화면 인계 (배포 실행 아님)

인증된 관리자 호스트의 `/admin`을 직접 연다. 관리자 호스트 `/` 리다이렉트는 추가하지 않았다. 화면은 `/static/css/admin.css`, `/static/js/admin.js` 및 기존 `/api/admin/*`와 상태 전용 `/api/calendar`를 같은 origin으로 요청한다. HTTPS를 종료하는 프록시 뒤에서도 정적 경로는 상대 URL이므로 mixed content가 생기지 않는다. 관리자 mTLS 보호와 공개 호스트의 `/admin`·`/api/admin` 차단을 함께 적용해야 한다.

변경 요청의 Origin은 브라우저의 실제 HTTPS 관리자 origin이어야 하며 CSRF는 발급 API에서 받아 메모리에만 보관한다. 403 후에는 인증 환경을 확인하고 화면의 `접근 다시 확인`을 사용한다. 실패한 쓰기를 자동 재제출하지 않는다. 브라우저 테스트의 Python 인증 컨텍스트 주입은 테스트 전용이며 운영 실행 방법으로 사용하지 않는다. 실기기 등록/수정/차단/취소 절차 및 미실행 상태는 `tests/manual/admin-ui.md`, 로컬 증거는 `qa/reports/RS-007.md`에 기록한다. 실제 인증서·Nginx·OCI·실기기 완료를 의미하지 않는다.
