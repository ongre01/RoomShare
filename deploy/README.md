# RS-008 배포 패키지

| 구성 | 역할 |
|---|---|
| [roomshare.env.example](roomshare.env.example) | 운영 경로 예제. 초기 관리자 거부, 보존기간 미승인 시 백업 삭제 안 함. |
| [nginx/](nginx/) | HTTPS 사이트와 공통 snippet. 두 DNS·TLS 체인·관리 CA/CRL·root 전용 내부 헤더 파일 필요. |
| [systemd/](systemd/) | 단일 loopback 앱, 온라인 백업 oneshot, 일일 timer. |
| [install.sh](../scripts/install.sh) / [deploy.sh](../scripts/deploy.sh) | 버전별 venv·사설 설정 준비 / 유지보수 lock 아래 DB 백업·명시적 migration. 서비스 자동 전환 없음. |
| [snapshot.py](../scripts/snapshot.py) | 검증된 SQLite 온라인 백업 / 새 사설 디렉터리로만 복원. |

[설치 → 구성 → DB 준비 → 전환 → 복구/롤백 실행서](../docs/DEPLOYMENT.md)와 [로컬 검증 및 미실행 게이트](../qa/reports/RS-008.md)를 따른다. 템플릿은 실제 운영 설정이나 OCI 배포 승인이 아니다.
