RS-002: `python scripts/migrate.py`는 설정한 DB 경로에 순차 SQL 마이그레이션을 적용한다. `--db-path`로 대상 경로를 지정하고 `--check`로 기존 DB의 버전·WAL·무결성을 읽기 전용으로 확인한다. 실행 방법과 초기 설정 보존 계약은 [README](../README.md), 연결 소유권은 [DESIGN](../docs/DESIGN.md) 참조. 실패 시 종료 코드 1이며 SQL/경로/비밀값을 출력하지 않는다.

RS-001의 `verify_bootstrap.py`는 깨끗한 venv와 실제 loopback HTTP를 검사한다. RS-008의 `install.sh`, `deploy.sh init|upgrade`, `snapshot.py backup|restore` 사용법·권한·호환 버전·운영 복원 승인 절차는 [배포 실행서](../docs/DEPLOYMENT.md)를 따른다. 도구는 systemd/Nginx를 자동 재시작하지 않고 기존 DB에 복원본을 덮어쓰지 않는다.
