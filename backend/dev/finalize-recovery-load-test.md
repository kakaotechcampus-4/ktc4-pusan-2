# finalize 복구 부하 측정 (#416)

재시작 복구는 원자료를 읽기 전부터 결과 저장까지 4개로 제한한다. ID도 4개씩 읽고 앞 배치가
끝나면 다음 배치를 읽는다. 앱 시작과 주기 복구는 같은 복구 태스크 하나를 쓰고 Take별 중복
실행 방지도 유지한다. 정상 종료의 finalize도 같은 작업 제한을 쓴다.

기존 HTTP 제한은 원자료·전사를 모두 읽고 replay를 만든 뒤 적용됐다. 이번 변경은 BE 내부의
작업 범위만 바꾸며 AI 요청·응답, 재시도, `FINALIZING` 조건부 저장 규칙은 그대로다.

## 재현

`backend/`에서 실행한다. `.env`의 `LOCAL_DATABASE_URL`을 우선 사용하고 없으면 `DATABASE_URL`을
쓴다. 임시 DB를 만들 권한이 필요하다. 기존 DB에는 쓰지 않으며 임시 DB는 종료할 때 지운다.

```bash
uv run python dev/finalize-recovery-load-test.py --takes 24 --seconds 3600 --http-delay 1
```

Take마다 1초 원자료 3,600행과 전사 구간 3,600행(단어 7,200개)을 넣는다. 원자료 사용 동의는
true로 두어 비교 사이에 원자료가 삭제되지 않게 한다. 각 비교 전에 Take를 `FINALIZING`으로
되돌리고 새 프로세스에서 측정한다. 모든 비교는 실제 `DbCoachStore`, replay 생성,
`HttpCoachClient`의 HTTP 4개 제한과 결과 저장을 사용한다. 응답만 `MockTransport`로 1초 뒤
고정 예시를 반환한다. 실제 AI 서버는 부르지 않는다.

`limit=0`은 변경 전처럼 ID 전체를 읽고 Take마다 태스크를 만드는 방식을 재현한다.
`--limits 2,4`가 기본이며 작업 제한과 ID 배치 크기를 함께 비교한다.

## 결과

2026-10-24, macOS arm64, Python 3.14.6, Homebrew PostgreSQL 17.11에서 다른 검증 작업과
겹치지 않게 한 번씩 측정했다. backlog는 모든 비교에서 24개였고 24개 모두 저장했다.

| 지표 | 기존 방식 재현 | 작업 2개 · ID 2개 | 작업 4개 · ID 4개 (채택) |
|---|---:|---:|---:|
| 복구 전 프로세스 RSS 최고값 (MiB) | 116.3 | 115.6 | 115.8 |
| 복구 포함 프로세스 RSS 최고값 (MiB) | 445.8 | 193.7 | 221.0 |
| 동시에 살아 있는 finalize 태스크 최고값 | 24 | 2 | 4 |
| 읽은 뒤 저장을 기다리는 원자료 최고값 | 20 | 2 | 4 |
| 복구 DB 연결 동시 사용 최고값 | 15 | 2 | 4 |
| DB 연결 획득 p95 (ms) | 861.78 | 1.30 | 4.70 |
| DB 연결 획득 최대 (ms) | 1232.26 | 16.18 | 16.55 |
| DB 연결 획득 평균 (ms) | 171.42 | 0.67 | 1.21 |
| 표본에서 관측한 DB 대기 세션 최고값 | 2 | 1 | 0 |
| 전체 복구 시간 (초) | 7.110 | 14.421 | 8.074 |

RSS는 `getrusage(RUSAGE_SELF).ru_maxrss`로 읽은 프로세스 최고값이다. 원자료 수는 조회를
완료한 시점부터 결과 저장이 끝날 때까지 센다. DB 연결 획득 시간은 `db.connection()`의
풀 대기·연결 생성·pre-ping을 포함하며 SQL 실행 시간이나 threadpool 진입 대기는 포함하지 않는다.
DB 대기 세션은 별도 연결에서 약 50ms 간격으로 `pg_stat_activity`의 `Lock`·`LWLock`·`IO`를
표본으로 읽는다. 표본 0은 대기가 전혀 없었다는 뜻이 아니다. 감시용 연결은 복구 연결 수에서 뺀다.

4개 제한에서 RSS 최고값이 약 50% 줄었고 복구 시간은 약 14% 늘었다. 2개 제한은 RSS가 조금 더
낮지만 복구 시간이 약 2배여서 기존 HTTP 상한과 같은 4개를 초기 작업 상한으로 정했다.
배치 안의 가장 느린 작업이 끝나야 다음 배치를 읽으므로 처리 시간이 고르지 않으면 지연이 더
늘 수 있다. DB 재시도 대기 중에도 작업 슬롯을 보유해 장애 중의 추가 원자료 적재를 막는다.

이 값은 합성 입력·고정 HTTP 지연의 로컬 비교다. 실제 AI의 한 시간 Take 처리 시간, 다른
백그라운드 plan과의 경쟁, 실시간 사용자 트래픽, 여러 BE 프로세스의 총부하, 운영 서버의 메모리
한도는 확인하지 않았다. 상한을 늘리기 전에는 그 환경에서 다시 측정해야 한다. 개별 Take의
원자료 크기와 정상 종료 때 이미 메모리에 있던 snapshot 수까지 제한하는 변경은 아니다.

## 검증

실행 위치는 해당 worktree의 `backend/`다.

- `uv run ruff check src`: 통과.
- `uv run python -c "import pitch_coach_backend.main"`: 통과.
- 빈 임시 DB의 `uv run alembic upgrade head` → `uv run alembic check`:
  통과, `No new upgrade operations detected.`. 검사 뒤 임시 DB 삭제.
- `uv run alembic heads`: `c02400000010 (head)` 1개.
- `ADMIN_USERNAME= ADMIN_PASSWORD= uv run pytest -q`: `954 passed, 1 skipped`.
  로컬 관리자 설정을 비워 실행했다.
- 복구·저장 회귀 테스트: 원자료 읽기 전 제한, 다음 ID 배치 조회 시점, 복구 중복 방지,
  커서 앞 Take 완료 후 누락 없음, 앱 시작 지연 없음, 조회 실패 시 snapshot 보존,
  슬롯 대기 중 취소 이벤트 보존, 종료 후 미처리 Take 재개를 확인했다.

스키마 변경은 없다. 되돌릴 때는 이 변경 커밋을 revert하면 된다.
