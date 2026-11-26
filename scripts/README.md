RS-002: `python scripts/migrate.py`는 설정한 DB 경로에 순차 SQL 마이그레이션을 적용한다. `--db-path`로 대상 경로를 지정하고 `--check`로 기존 DB의 버전·WAL·무결성을 읽기 전용으로 확인한다. 실행 방법과 초기 설정 보존 계약은 [README](../README.md), 연결 소유권은 [DESIGN](../docs/DESIGN.md) 참조. 실패 시 종료 코드 1이며 SQL/경로/비밀값을 출력하지 않는다.

RS-001의 `verify_bootstrap.py`는 깨끗한 venv와 실제 loopback HTTP를 검사한다. RS-008에서 설치/배포/백업/복원 스크립트를 작성한다. 현재 마이그레이션 명령은 운영 배포·백업·복원을 대신하지 않는다.
