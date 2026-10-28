# BE 폴더 구조 분리 계획과 적용 결과

이 문서는 [#475](https://github.com/kakaotechcampus-4/ktc4-pusan-2/issues/475) 의 분리 계획과, 그 계획을 적용하며 달라진
점을 담는다. 그 뒤 `realtime/stream` 을 다시 나눈 것([#501](https://github.com/kakaotechcampus-4/ktc4-pusan-2/issues/501))도
'stream 다시 나누기' 절에 담는다. 지금 폴더 구조와 의존 규칙의 요약은 [backend/README.md](../../backend/README.md)
'폴더 구조' · '의존 방향' 이다.

## 적용 결과 (2026-10-25)

API 경로 · 요청/응답 · DB 스키마는 바꾸지 않았다. 아래는 계획(이 문서의 '계획' 절)을 쓴 뒤 코드가 바뀌어서, 또는 옮기며
더 맞는 자리를 찾아 계획과 다르게 한 것이다.

### 계획 뒤에 생긴 파일

같은 기준으로 자리를 정했다. 트리에 없다는 이유로 지우지 않았다.

| 파일 | 자리 | 이유 |
|---|---|---|
| `pitch/standard_parser.py` · `pitch/script_rubric.py` | `pitch/adapter/` | AI HTTP 어댑터다. DB 를 모른다 |
| `pitch/script_rubric_runner.py` | `pitch/service/` | `script_parse_runner.py` 와 같은 백그라운드 작업이다 |
| 장별 평가 기준 상태 함수 (`begin_rubric` 등) | `pitch/service/script.py` | 파싱 결과 저장과 같은 트랜잭션에서 첫 작업을 둔다 (`_queue_rubric`) |
| 코칭 계획 행 (`chosen_coaching_plan` · `add_coaching_plan`) | `pitch/service/pitch.py` | 발표에 딸린 짧은 조회 · 저장이라 따로 파일을 만들지 않았다 |
| 대본 전달도 분석 (`analyze_script_coverage` 와 그 단계들) | `take/service/coverage.py` | 리뷰와 다른 업무다 (`SCORING`) |
| 문장 끝 신호 저장 (`stored_utterance_ends` · `save_utterance_ends`) | `take/service/record.py` | 실시간 기록이다 |
| `ABANDON_AFTER_SEC` · `READY_EXPIRE_AFTER` · `abandon_stale_ready_takes` | `take/service/lifecycle.py` | Take 상태를 끝내는 기준이다 |
| `take/coach_service.py` · `coach_repository.py` · `ai_response.py` · `ready_sweep.py` | 그대로 | 이미 책임별 파일이다 |
| `realtime/live_takes.py` | 그대로 | `module/hooks.py` 의 realtime 쪽 구현이다 |
| `coach/session.py` · `finalize.py` · `planner.py` · `retention.py` · `store.py` · `background.py` | 그대로 | 이미 책임별 파일이다 |

### 계획과 다르게 한 것

- **controller 의 realtime 호출, admin 의 정책 값**: 계획을 쓴 뒤 `module/hooks.py` 가 생겨 module 의 controller 는 이미
  realtime 을 import 하지 않는다. 그래서 "controller 는 registry 공개 함수를 부를 수 있다" 는 예외를 두지 않고, hooks 의
  구현(`realtime/live_takes.py`)이 `stream/registry.py` 의 공개 함수를 부른다. `ABANDON_AFTER_SEC` 도 그사이 take
  도메인으로 옮겨져 admin service 가 realtime 을 import 하지 않는다. admin 은 import 경로만 바뀌었다
  (`take/service/lifecycle.py`). controller 가 값을 넘기는 변경은 하지 않았다.
- **runtime 이 registry 를 모르게 한 방법**: 스트림이 알릴 일(열림 · 닫힘 · Take 끝냄 · 백그라운드 태스크 · Take 끝내기)은
  `runtime.StreamHost` 프로토콜로 넘기고 registry 가 구현한다. Take 별 기록 타입(`TakeRecord`)은 runtime 에 두고 registry 가
  만들어 넘긴다 (`registry.create_stream`). 떠난 Take 타이머(`abandon.py`)는 걸고 끄기만 하고, 만료되면 registry 가 넘긴
  콜백을 부른다. #501 에서 `StreamHost` · `TakeRecord` 는 `stream/record.py` 로 옮겼다.
- **같이 쓰는 상수**: 한 모듈에만 두고 다른 모듈은 `모듈.상수` 로 읽는다 (예: `stt/loop.py` 의 `ABORT_TIMEOUT_SEC` 를
  registry · runtime 이 `stt_loop.ABORT_TIMEOUT_SEC` 로). 테스트가 한 곳만 바꿔도 모든 사용처에 걸린다.
- **coach**: `_id` 는 요청과 코칭 계획이 같이 써서 `requests.as_id` 로, `_Reply` 는 client 도 써서 `replies.Reply` 로 이름을
  바꿨다. `MAX_T_MS` 는 응답 검사가 쓰므로 `replies.py` 에 두고 client 가 가져다 쓴다.
- **테스트**: `test_realtime_coach.py` 는 `tests/realtime/` 의 fixture 를 그대로 쓰므로 `tests/realtime/test_ws_coach.py`
  로 옮겼다. `tests/take/test_previous_missions.py` 는 옮길 테스트가 없어(이전 미션 테스트는 다른 파일로 옮겨졌다) 만들지
  않았다. auth 의 두 `fake_google` 은 하나로 합쳤다 — 엔드포인트 쪽에서 identity 값을 보던 `/users/me` 테스트의 기대값만
  바뀌었다. 엔드포인트 파일에만 걸리던 Redis fixture 는 `pytestmark = pytest.mark.usefixtures(...)` 로 같은 범위를 지킨다.
  다른 `test_coach_*.py` 는 자리를 그대로 두었다.

### 확인한 것

- 테스트 수집 1173개, 이름 · 파라미터가 분리 전과 같다. 테스트마다 걸리는 fixture 목록도 같다 (`pytest --setup-plan` 비교).
- 전체 테스트 1172 passed · 1 skipped (분리 전과 같다).
- `entities` 를 import 한 뒤 `Base.metadata.tables` 20개 · 각 테이블의 칼럼 이름이 분리 전과 같다.
- 옮긴 함수 · 클래스 · 메서드는 AST 로 옮기기 전후 본문을 비교했다. 달라진 곳은 위에 적은 연결 지점뿐이다.

## stream 다시 나누기 (2026-10-25, #501)

폴더를 나눈 뒤에도 `stream/runtime.py`(925줄) · `stream/registry.py`(785줄)가 BE 소스에서 가장 길었다. registry 에는 프로세스
상태 · 연결 붙이기 · Take 끝내기 · 떠난 Take · 앱 종료가 한 파일에 있었고, runtime 은 `TakeStream` 하나가 710줄이었다.
경계가 분명한 것부터 떼어 냈다. 동작 · API · DB 는 바꾸지 않았다.

### 지금 구조

하위 폴더 없이 평평하게 두고, 층을 정해 위에서 아래로만 import 한다.

| 층 | 파일 | 담는 것 |
|---|---|---|
| Take 수명 (밖에서 부르는 입구) | `registry.py` (440줄) | 연결 붙이기(`attach`) · 떠난 Take 끝내기 · 앱 시작 복구 · 앱 종료 정리. connection · main 이 부른다 |
| | `finish.py` | 사용자 · 자동 종료로 Take 끝내기: 마지막 창 · 원자료 저장 · DB 에 적기 · 늦은 기록 재시도 · Take 결과 띄우기. connection · live_takes 가 부른다 |
| | `abandon.py` | 떠난 Take 타이머. 걸고 끄기만 하고 만료되면 콜백을 부른다 |
| | `state.py` | 스트림 맵 · attach 잠금 · 끝낸 Take · Take 별 기록 · 백그라운드 태스크. registry · finish 만 쓴다 |
| 스트림 하나 | `runtime.py` (716줄) | `TakeStream`: 수명 상태 기계 · 종료 조율 · 입력을 부품에 넘기기 |
| | `record.py` | `CoachDeps` · `TakeRecord` · `StreamHost` (스트림과 위층의 경계) |
| | `client.py` | `ClientSlot`: 붙어 있는 연결 · 이전 연결 쫓아내기 · FE 로 보내기 |
| | `audio_queue.py` | `AudioQueue`: 오래된 것부터 버리는 오디오 큐 · 정리할 때 비우기 |
| 판단 규칙 | `auto_end.py` | `AutoEndPolicy` · `SilenceTracker` · `TimeLimit`(최대 시간 타이머 · 서버 시계) |
| | `heard.py` | `HeardRange`: 끊김 없이 받은 오디오 범위 → 1초 원자료의 STT 상태 |

- 의존 방향: `registry → finish → state → runtime → record · client · audio_queue · heard · auto_end`.
  runtime 과 부품은 registry · finish · state 를 import 하지 않는다. abandon 도 registry 를 모른다.
- 부품(`ClientSlot` · `AudioQueue` · `HeardRange` · `TimeLimit`)은 `TakeStream` 을 모르고 값과 콜백만 받는다.
  `_stopping` · `_closed` · `_discarded` 같은 수명 플래그는 `TakeStream` 만 가진다.
- `record.py` 는 `StreamHost.stream_closed` 의 타입 때문에 `TYPE_CHECKING` 아래에서만 runtime 을 import 한다.
  `TimeLimit` 은 `TakeRecord` 를 받지 않고 앞서 열려 있던 시간을 받는다. `record.py` 가 `auto_end` 를 import 하므로
  반대로 import 하면 순환이 생긴다.

### 정한 것

- **옮긴 이름을 옛 모듈에서 다시 내보내지 않는다.** 상수를 다시 내보내면 `monkeypatch.setattr(registry, "MARK_…")` 가
  옛 이름에만 걸려 테스트가 실패 없이 다른 값으로 돈다. 테스트의 monkeypatch 와 비공개 이름(`registry._streams` 등)을
  새 모듈로 바꿨다. `state.py` 의 이름은 registry · finish 가 같이 쓰므로 밑줄을 뺐다 (`state.streams`, `state.ended`).
- **모듈 이름은 `finish.py` 다.** `ending.py` 로 하면 `live_takes.py` 와 Take 끝내기 함수의 지역 변수 · 매개변수 `ending`
  에 가려진다.
- **떠난 Take 처리는 registry 에 남겼다.** `abandon.py` 에 합치면 "abandon 은 registry 를 모른다" 가 깨진다.
- **입구는 registry · finish 둘이다.** 하나로 모으려면 registry 가 finish 를 다시 내보내야 하고 위의 monkeypatch 문제가 생긴다.
- **수명 관리(`attach` ~ `_finish`)는 나누지 않았다.** 수명 플래그 · 연결 · 기록을 같이 만지는 상태 기계라 믹스인이나
  객체로 쪼개면 한 흐름을 여러 파일에서 읽어야 한다. runtime 716줄 중 코드는 435줄이고 나머지는 docstring · 주석이다.
- **하위 폴더(`stream/take/` · `stream/pipeline/`)는 만들지 않았다.** 10개 파일은 이름만으로 찾을 수 있다. 파일이
  15개를 넘거나 다른 종류의 스트림이 생기면 다시 본다.
- 그대로 둔 것: `registry.SEAL_WAIT_SEC` 는 `coach.session` 에서 값으로 가져와 '같이 쓰는 상수' 규칙과 맞지 않지만,
  고치면 `test_ws_coach.py` 의 종료 테스트가 바꾸는 범위가 달라져 두었다. `_quiet` 는 `stt/loop.py` · connection 에도 각자
  있어 stream 안의 것만 `client.quiet` 로 옮겼다.

### 확인한 것

- 테스트 수집 1228개가 분리 전과 이름까지 같다. 전체 테스트 1227 passed · 1 skipped (분리 전과 같다).
- 빈 DB 에 `alembic upgrade head` 뒤 `alembic check` 가 "No new upgrade operations", head 1개.
- `state` · `finish` · `record` · `TimeLimit` · `HeardRange` 로 옮긴 정의는 AST 로 비교했다. 122개 중 112개가 같고, 다른 10개는
  부품을 쓰도록 바꾼 `TakeStream` 메서드와 docstring 경로만 바뀐 `StreamHost` · `_Host` 다. `ClientSlot` · `AudioQueue` 는
  옮긴 본문을 diff 로 대조했다.

---

## 계획 (원문)

아래는 분리 전에 쓴 계획이다. 줄 수 · 마이그레이션 이름 · 트리는 계획을 쓸 때의 값이다.

### 현재 DB 구조 (계획 작성 시점)

`module/*/entity.py` 기준입니다. 마지막 마이그레이션은 `2026_10_23_1805-f0407b32a13e_add_take_metric_seconds` 입니다.

#### 관계

```
users
├── oauth_accounts              user_id  → users           CASCADE
├── refresh_tokens              user_id  → users           CASCADE
│   └── (자기 참조)             replaced_by_id → refresh_tokens   SET NULL
│
└── pitches                     user_id  → users           CASCADE
    ├── standards               pitch_id → pitches         CASCADE
    ├── standard_analyses       pitch_id → pitches         CASCADE
    ├── presentation_versions   pitch_id → pitches         CASCADE   ◀─┐
    ├── script_versions         pitch_id → pitches         CASCADE   ◀─┤ takes 가 연습한 버전을 가리킴
    │   └── script_slides       script_version_id → script_versions  CASCADE
    │                                                                  │ (ON DELETE 없음)
    └── takes                   pitch_id → pitches         CASCADE   ──┘
        ├── take_summaries      take_id  → takes   CASCADE   1:1 (take_id 가 PK)
        ├── calibrations        take_id  → takes   CASCADE   1:1 (take_id UNIQUE)
        ├── take_ai_results     take_id  → takes   CASCADE   (take_id, kind) 당 1행
        ├── live_feedbacks      take_id  → takes   CASCADE   1:N
        ├── missions            source_take_id → takes  CASCADE   과제를 만든 Take
        │                       take_id        → takes  SET NULL  과제를 수행하는 Take
        ├── take_transcript_segments  take_id → takes  CASCADE   1:N (확정 전사)
        └── take_metric_seconds       take_id → takes  CASCADE   1:N (1초 원자료)
```

### 도메인과 책임에 따른 폴더 구조

모듈은 사용자 · 인증 · 발표 · 연습처럼 관련 업무를 묶는 단위입니다. 현재 DB 관계와도 잘 맞으므로 기존 모듈 경계를 유지합니다. 테이블이 추가될 때마다 모듈을 새로 만들지는 않습니다.

| 모듈 | 테이블 |
|---|---|
| user | users |
| auth | oauth_accounts · refresh_tokens |
| pitch | pitches · presentation_versions · script_versions · script_slides · standards · standard_analyses |
| take | takes · calibrations · missions · take_summaries · take_ai_results · take_transcript_segments · take_metric_seconds · live_feedbacks |

이 문서는 **앞으로 적용할 분리 계획**입니다. 이번 문서 수정은 실제 파일 이동 · import 변경 · API나 DB 변경을 포함하지 않습니다.

#### 분리 기준

- **역할과 이름을 통일하고, 폴더 깊이는 필요한 만큼만 늘립니다.** 기본은 현재의 `controller.py` · `service.py` · `repository.py` · `entity.py` · `dto.py` 입니다. FastAPI 때문에 이 파일들을 전부 하위 폴더로 바꿀 필요는 없습니다.
- **서로 다른 업무가 섞여 읽기 어려운 계층만 나눕니다.** `pitch/service.py`의 발표 관리 · 자료 업로드 · 대본 파싱 · 평가 기준처럼 변경 이유가 다른 코드를 분리합니다. service를 나눠도 controller · repository · entity · dto는 파일로 유지할 수 있습니다.
- **줄 수는 검토 신호이지 목표가 아닙니다.** `user`는 service 43줄 · repository 29줄 · entity 13줄 · dto 11줄이어서 현재 구조를 유지합니다. `auth` · `admin`도 지금은 계층 폴더로 바꾸지 않습니다. 아래 줄 수는 계획 작성 시점의 참고값입니다.
- **테이블 하나당 파일 하나를 강제하지 않습니다.** 밀접한 entity는 같은 파일에 둡니다. repository도 관련 조회 · 저장을 함께 두며, 커지면 대본 처리 · 리뷰 저장처럼 업무 단위로 나눕니다. 여러 테이블을 조인하는 조회를 억지로 테이블별 repository로 분해하지 않습니다.
- **API 자원마다 모든 계층의 파일을 만들지 않습니다.** 현재 짧은 장치 점검 · 이전 미션 함수는 `take/service/take.py`에 함께 둡니다. 별도 로직이 늘어나면 그때 분리합니다. 빈 파일이나 한 함수를 전달하기만 하는 파일은 만들지 않습니다.
- `exception.py` · `dependencies.py`는 모듈마다 한 파일로 유지합니다. 소유권 확인의 진입점은 현재의 `OwnedPitch` · `OwnedTake`입니다.
- `service/`를 만들면 기존 `service.py`는 제거하고 호출부가 실제 하위 모듈을 import하도록 바꿉니다. `service/__init__.py`는 비워 둡니다. entity는 이번에 나누지 않으므로 `entities.py` · Alembic의 모델 등록 경로도 유지합니다.
- 외부 연동은 업무 로직과 분리합니다. 우선 `pitch`의 S3 · PDF · AI 파서를 `adapter/`에 모읍니다. `auth/google.py` · `auth/state_store.py`는 이미 별도 파일이므로 현재 위치를 유지하고, 연동 코드가 늘어나면 `adapter/`로 묶습니다. 연결 설정은 계속 `core/`가 맡습니다.
- realtime은 **연결 하나 · Take 스트림 · STT 통신 · 기록 저장**의 경계에 따라 `connection/` · `stream/` · `stt/` · `storage/`로 묶습니다. 공통 메시지와 오디오 규격은 바로 아래에 둡니다. 폴더 안에서는 `session.py` · `runtime.py` · `adapter.py`처럼 역할을 드러내는 이름을 쓰고, 각 `__init__.py`는 비워 둡니다.

#### 트랜잭션과 상태의 경계

- **한 업무의 DB 변경은 같은 세션에서 처리하고, service가 커밋을 결정합니다.** repository를 추가로 나누더라도 repository마다 따로 커밋하지 않습니다.
- `save_review()`의 리뷰 결과 · 다음 미션 · 기존 미션 결과 · 요약 · Take 완료 상태 저장은 `service/review.py` 안에서 하나의 트랜잭션으로 유지합니다. 외부 AI 호출 전후의 상태 저장은 현재의 별도 트랜잭션 경계를 유지합니다.
- `mark_running` · `mark_ended` · `mark_abandoned` · `running_takes`는 Take 상태 관리이므로 `service/lifecycle.py`가 맡습니다. 전사 · 1초 원자료 저장은 `service/record.py`가 맡습니다. 호출자가 realtime이라는 이유로 상태 변경까지 record에 모으지 않습니다.
- `service/take.py`에서 상태 변경이 필요하면 `service/lifecycle.py`를 호출합니다. lifecycle은 take service를 역으로 import하지 않습니다. 모든 service 파일을 서로 연결하는 방식은 피합니다.
- realtime은 연결 · 타이머 · 스트림 수명을 관리하고, DB의 Take 상태 변경은 계속 take service에 위임합니다. 코드 이동 과정에서 재연결 · 종료 · 자동 종료의 순서를 바꾸지 않습니다.

현재 README의 도메인 간 service 호출 원칙과 달리 실제 코드에는 다른 도메인의 repository · entity를 직접 import하는 부분이 있습니다. 이번 계획에서는 파일 이동과 그 의존 관계 정리를 구분합니다. 기존 동작을 보존하는 분리를 먼저 하고, 도메인 간 공개 service와 트랜잭션 경계를 정하는 작업은 별도로 다룹니다.

#### controller와 도메인 내부의 의존 규칙

현재 `backend/README.md`의 "module은 realtime·task를 모른다"는 문장은 controller까지 포함하지만, 실제 take · pitch · user controller는 종료 · 삭제 시 스트림을 정리합니다. 이 계획을 적용할 때 README의 realtime 의존 규칙도 아래 범위로 갱신합니다. 이번 문서 수정에서 README나 실제 코드를 변경하지는 않습니다.

- **controller는 요청의 처리 순서를 조율합니다.** 도메인 service와 `realtime/stream/registry.py`의 공개 함수(`discard` · `end_without_stop` 등)를 호출할 수 있습니다. DB 변경은 도메인 service에 맡기고 스트림 맵 · 잠금 · 큐 같은 내부 상태에는 직접 접근하지 않습니다.
- **도메인 service · repository · entity는 realtime에 의존하지 않습니다.** realtime은 반대로 도메인 service를 통해 기록과 Take 상태를 저장할 수 있습니다. `stream → module/*/controller` 같은 역방향 import도 금지합니다.
- **admin의 정책 값도 controller에서 전달합니다.** 현재 `admin/service.py`가 직접 import하는 `ABANDON_AFTER_SEC`는 분리 후 `admin/controller.py`가 `stream/abandon.py`에서 읽고, `list_stuck_takes`에 `abandon_after_sec` 같은 숫자 인자로 전달합니다. admin service는 그 값으로 조회 기준만 계산하고 realtime을 import하지 않습니다. 상수의 값과 조회 결과는 유지합니다.
- 순환을 피하기 위해 controller가 registry를 부를 수 있다는 예외를 도메인 service의 내부 참조 허용으로 확대하지 않습니다. 기존 도메인 간 repository · entity 직접 참조 정리는 앞 절처럼 별도 단계에서 다룹니다.

`+` 새 파일 · 폴더, `~` 줄어들거나 내용이 바뀌는 파일, `-` 분리 · 이동 후 원래 경로에서 삭제되는 파일, `← 이름` 옮겨 오거나 쪼개 온 원래 파일입니다. 삭제되는 파일도 원래 위치에 남겨 `-`와 이동 목적지를 표시합니다. 내용 일부를 추출하고 원본 파일을 유지하는 경우는 `~`로 표시하며, import 경로만 바뀌는 파일은 별도로 표시하지 않았습니다. `-`는 계획 적용 후의 삭제를 뜻하며, 이번 문서 수정에서 실제 파일을 삭제하지는 않습니다.

```
ktc4-pusan-2/
├── docs/
│   └── be/
│       ├── README.md                            ~ 계획과 아래 세 동작 문서의 링크 추가
│       ├── file-split-plan.md                   ← backend/docs/file-split-plan.md (공유 시 이동)
│       ├── take-lifecycle.md
│       ├── realtime-stt.md                      + ← backend/README 「실시간 STT (WebSocket)」 절
│       ├── script-parse.md                      + ← backend/README 「대본 파싱 (AI)」 절
│       └── realtime-coach.md                    + ← backend/README 「실시간 코치 (AI)」 절
│
└── backend/
    ├── .dockerignore
    ├── .env.example
    ├── .python-version
    ├── Dockerfile
    ├── README.md                                ~ 긴 절 3개는 링크로 분리 · 폴더 구조 · 필요한 계층만 분리하는 규칙 갱신
    ├── alembic.ini
    ├── pyproject.toml
    ├── uv.lock
    │
    ├── alembic/
    │   ├── README
    │   ├── env.py
    │   ├── script.py.mako
    │   └── versions/
    │       ├── ...
    │
    ├── dev/
    │   ├── backfill-presentation-page-count.py
    │   ├── oauth-test.html
    │   ├── stt-send-pcm.py
    │   └── stt-test.html
    │
    ├── src/pitch_coach_backend/
    │   ├── __init__.py
    │   ├── entities.py
    │   ├── main.py
    │   ├── py.typed
    │   │
    │   ├── core/
    │   │   ├── __init__.py
    │   │   ├── config.py
    │   │   ├── database.py
    │   │   ├── exceptions.py
    │   │   ├── logging.py
    │   │   ├── redis.py
    │   │   ├── s3.py
    │   │   ├── security.py
    │   │   └── validation.py
    │   │
    │   ├── module/
    │   │   ├── __init__.py
    │   │   │
    │   │   ├── user/                            현재 구조 유지
    │   │   │   ├── __init__.py
    │   │   │   ├── controller.py
    │   │   │   ├── service.py
    │   │   │   ├── repository.py
    │   │   │   ├── entity.py
    │   │   │   ├── dto.py
    │   │   │   └── exception.py
    │   │   │
    │   │   ├── auth/                            현재 구조 유지
    │   │   │   ├── __init__.py
    │   │   │   ├── controller.py
    │   │   │   ├── service.py
    │   │   │   ├── repository.py
    │   │   │   ├── entity.py
    │   │   │   ├── dto.py
    │   │   │   ├── dependencies.py
    │   │   │   ├── exception.py
    │   │   │   ├── google.py
    │   │   │   └── state_store.py
    │   │   │
    │   │   ├── pitch/                           service와 외부 연동만 분리
    │   │   │   ├── __init__.py
    │   │   │   ├── controller.py                ~ service import · monkeypatch 대상 경로 갱신
    │   │   │   ├── repository.py                여러 테이블의 관련 조회 · 저장을 함께 유지
    │   │   │   ├── entity.py                    관련 모델을 한 파일에 유지
    │   │   │   ├── dto.py
    │   │   │   ├── dependencies.py
    │   │   │   ├── exception.py
    │   │   │   ├── service.py                   - service/의 업무별 파일로 분리
    │   │   │   ├── script_parse_runner.py       - service/script_parse_runner.py로 이동
    │   │   │   ├── s3_service.py                - adapter/storage.py로 이동 · 이름 변경
    │   │   │   ├── presentation_file.py         - adapter/presentation_file.py로 이동
    │   │   │   ├── script_parser.py             - adapter/script_parser.py로 이동
    │   │   │   ├── service/                     ← pitch/service.py (636)
    │   │   │   │   ├── __init__.py              + 비워 둠
    │   │   │   │   ├── pitch.py                 + 발표 생성 · 목록 · 자료 모음 · 준비 화면 · 수정 · 삭제
    │   │   │   │   ├── presentation.py          + 자료 업로드 · 조회
    │   │   │   │   ├── script.py                + 대본 검사 · 생성 · 파싱 상태 · 재파싱 · 결과 저장 · keyterm 후보
    │   │   │   │   ├── script_parse_runner.py   ← pitch/script_parse_runner.py
    │   │   │   │   └── standard.py              + 평가 기준 저장 · 나누기 · 조회
    │   │   │   └── adapter/                     + 외부 연동
    │   │   │       ├── __init__.py              + 비워 둠
    │   │   │       ├── storage.py               ← pitch/s3_service.py
    │   │   │       ├── presentation_file.py     ← pitch/presentation_file.py
    │   │   │       └── script_parser.py         ← pitch/script_parser.py
    │   │   │
    │   │   ├── take/                            service만 업무별로 분리
    │   │   │   ├── __init__.py
    │   │   │   ├── controller.py                ~ service import 갱신
    │   │   │   ├── repository.py                관련 조회 · 저장 유지. 테이블별 분리하지 않음
    │   │   │   ├── entity.py
    │   │   │   ├── dto.py
    │   │   │   ├── dependencies.py
    │   │   │   ├── exception.py
    │   │   │   ├── service.py                   - service/의 업무별 파일로 분리
    │   │   │   └── service/                     ← take/service.py (472)
    │   │   │       ├── __init__.py              + 비워 둠
    │   │   │       ├── take.py                  + 생성 · 수정 · 삭제 · 소유권 조회 · 장치 점검 · 이전 미션
    │   │   │       ├── lifecycle.py             + mark_* · running_takes · close_take_service
    │   │   │       ├── review.py                + 리뷰 요청 조립 · 호출 · 결과 저장. 저장 트랜잭션 유지
    │   │   │       └── record.py                + 전사 커서 · 전사 저장 · 1초 원자료 저장
    │   │   │
    │   │   └── admin/                           폴더 구조 유지. 정책 값 전달 방식만 정리
    │   │       ├── __init__.py
    │   │       ├── controller.py                ~ 떠남 대기 정책 값을 service에 전달
    │   │       ├── service.py                   ~ realtime 상수 import 제거 · 정책 값을 인자로 받음
    │   │       ├── repository.py
    │   │       ├── dto.py
    │   │       ├── dependencies.py
    │   │       ├── exception.py
    │   │       └── static/
    │   │           ├── app.js
    │   │           ├── index.html
    │   │           └── style.css
    │   │
    │   ├── coach/
    │   │   ├── __init__.py
    │   │   ├── client.py                        ~ HTTP 호출 · 재시도 · 409 · 동시 개수
    │   │   ├── replies.py                       + ← client.py  AI 응답 모델 · SummaryMeasures
    │   │   ├── requests.py                      ~ 시간 계획 · Take 상수 · 요청 조립
    │   │   ├── windows.py                       + ← requests.py  창 · replay 입력. 순수 함수
    │   │   ├── coaching_plan.py                 + ← requests.py  코칭 계획 보관 · 고르기
    │   │   └── dependencies.py
    │   │
    │   └── realtime/
    │       ├── __init__.py
    │       ├── controller.py                    ~ connection/session.py를 호출하는 WebSocket 진입점
    │       ├── dependencies.py                  ~ STT · 저장소 조립과 주입. 기존 get_* 진입점 유지
    │       ├── dto.py                           FE ↔ BE 공통 메시지 · 오류 코드
    │       ├── audio.py                         프레임 처리와 STT가 함께 쓰는 오디오 규격
    │       ├── event_ingestion.py               공통 프레임 파싱 · 순서 · 중복 · 갭 계산. I/O 없음
    │       ├── fillers.py                       - stt/fillers.py로 이동
    │       ├── service.py                       - connection/session.py · handshake.py로 분리
    │       ├── take_stream.py                   - stream/runtime.py · registry.py · abandon.py · stt/loop.py · storage/transcripts.py로 분리
    │       ├── auto_end.py                      - stream/auto_end.py로 이동
    │       ├── stt_adapter.py                   - stt/adapter.py로 이동 · 이름 변경
    │       ├── keyterms.py                      - stt/keyterms.py로 이동
    │       ├── metric_buffer.py                 - storage/metrics.py로 이동 · 이름 변경
    │       ├── transcript_store.py              - storage/store.py로 이동 · 이름 변경
    │       ├── connection/                      + FE 연결 하나의 처리
    │       │   ├── __init__.py                  + 비워 둠
    │       │   ├── session.py                   ← service.py  연결 하나의 수명 · 프레임 전달 · stop 처리
    │       │   └── handshake.py                 + ← service.py  Origin · 인증 · Take 권한 확인 · 거절
    │       ├── stream/                          + Take별 스트림 수명. FE 재연결 뒤에도 유지
    │       │   ├── __init__.py                  + 비워 둠
    │       │   ├── registry.py                  + ← take_stream.py  스트림 맵 · attach 잠금 · Take별 기록 · 종료 기록
    │       │   ├── runtime.py                   ← take_stream.py  TakeStream · 큐 · 현재 클라이언트 · 종료 조율
    │       │   ├── auto_end.py                  ← auto_end.py  말 없음 · 최대 시간 판단
    │       │   └── abandon.py                   + ← take_stream.py  떠남 · 탭 닫힘 타이머. 만료 시 registry에 통지
    │       ├── stt/                             + 외부 음성 인식 통신
    │       │   ├── __init__.py                  + 비워 둠
    │       │   ├── adapter.py                   ← stt_adapter.py  Deepgram 연결 · 프로토콜 · 이벤트 변환
    │       │   ├── fillers.py                   ← fillers.py  STT 인식 보정용 군더더기 사전
    │       │   ├── keyterms.py                  ← keyterms.py  인식 용어 조립 · 한도 계산
    │       │   └── loop.py                      + ← take_stream.py  STT 접속 · 재접속 · 송수신 · 세션 시간 기준
    │       └── storage/                         + 기록 버퍼와 도메인 저장 연결
    │           ├── __init__.py                  + 비워 둠
    │           ├── store.py                     ← transcript_store.py  저장소 Protocol · DB 구현 · 짧은 세션
    │           ├── transcripts.py               + ← take_stream.py  전사 번호 · 저장 재시도 · 미전송 final 보관
    │           └── metrics.py                   ← metric_buffer.py  Take별 1초 원자료 버퍼 · flush · 재시도
    │
    └── tests/
        ├── __init__.py
        ├── conftest.py                          ~ 분리 후 실제 참조 위치에 맞춰 monkeypatch 경로 갱신
        ├── test_admin.py
        ├── test_auth_entity.py
        ├── test_config.py
        ├── test_exceptions.py
        ├── test_get_standards.py
        ├── test_google_oauth.py
        ├── test_health.py
        ├── test_logging.py
        ├── test_pitch_datas_service.py
        ├── test_pitch_db_test.py
        ├── test_pitch_files.py                  ~ service · adapter의 실제 참조 위치에 맞춰 monkeypatch 갱신
        ├── test_pitch_home_service.py
        ├── test_pitch_prepare.py
        ├── test_pitch_standards.py
        ├── test_pitch_upload_endpoint.py        ~ controller · service의 실제 참조 위치에 맞춰 monkeypatch 갱신
        ├── test_pitch_validation.py
        ├── test_realtime_auto_end.py            ~ stream/auto_end.py로 import 경로 갱신
        ├── test_realtime_event_ingestion.py
        ├── test_realtime_keyterms.py            ~ stt/keyterms.py · stt/fillers.py로 import 경로 갱신
        ├── test_realtime_stt_adapter.py         ~ stt/adapter.py로 import · monkeypatch 경로 갱신
        ├── test_s3_connect.py
        ├── test_script_parser.py
        ├── test_security.py
        ├── test_state_store.py
        ├── test_take_create.py
        ├── test_upload_presentation.py          ~ 업로드 · PDF 처리의 실제 참조 위치에 맞춰 monkeypatch 갱신
        ├── test_user_entity.py
        ├── test_auth_service.py                - auth/의 테스트 파일과 conftest.py로 분리
        ├── test_auth_endpoints.py              - auth/의 테스트 파일과 conftest.py로 분리
        ├── test_coach_client.py                - coach/의 테스트 파일과 conftest.py로 분리
        ├── test_coach_requests.py              - coach/의 테스트 파일과 conftest.py로 분리
        ├── test_realtime_ws.py                 - realtime/의 테스트 파일과 conftest.py로 분리
        ├── test_script_parse.py                - script_parse/의 테스트 파일과 conftest.py로 분리
        ├── test_take_api.py                    - take/의 테스트 파일과 conftest.py로 분리
        │
        ├── auth/                                ← test_auth_service.py (813) · test_auth_endpoints.py (769)
        │   ├── __init__.py                      +
        │   ├── conftest.py                      + Redis · 가짜 Google · login · CSRF 도우미
        │   ├── test_login_service.py            + 로그인 · state · 가입
        │   ├── test_token_rotation.py           + 회전 · 재사용 탐지
        │   ├── test_rotation_grace.py           + 동시 회전 · grace 창
        │   ├── test_logout_service.py           + 만료 · 로그아웃 · 탈퇴 시 토큰
        │   ├── test_login_endpoints.py          + start · callback · 쿠키 · 리다이렉트
        │   ├── test_refresh_endpoint.py         + refresh · CSRF · Origin/Referer
        │   ├── test_session_endpoints.py        + logout · me
        │   └── test_withdraw.py                 + 탈퇴
        │
        ├── coach/                               ← test_coach_client.py (631) · test_coach_requests.py (595)
        │   ├── __init__.py                      +
        │   ├── conftest.py                      + AI 예시 파일 읽기 · Clock
        │   ├── test_replies.py                  + 응답 읽기 · 요약 값
        │   ├── test_client.py                   + 호출 · 재시도 · 409 · 응답 모양 · 동시 개수
        │   ├── test_client_failures.py          + AI가 없을 때 · 로그
        │   ├── test_requests.py                 + 시간 계획 · Take 상수 · 요청 모양
        │   ├── test_windows.py                  + 창 · replay
        │   └── test_coaching_plan.py            + 코칭 계획
        │
        ├── realtime/                            ← test_realtime_ws.py (4136)
        │   ├── __init__.py                      +
        │   ├── conftest.py                      + 가짜 STT · 가짜 저장소 · handshake 도우미
        │   ├── test_ws_connection.py            + 인증 · keyterm · 타임라인 · 재연결 · grace
        │   ├── test_stream_lifecycle.py         + 핸드오버 · cancel · 회전 경합
        │   ├── test_ws_take_status.py           + READY→RUNNING→ANALYZING · stop · PUT end
        │   ├── test_ws_transcripts.py           + final 저장 · 번호 이어받기 · 삭제 시 끊기
        │   ├── test_ws_auto_end.py              + 말 없음 · 최대 시간
        │   ├── test_ws_abandon.py               + 떠남 타이머
        │   └── test_ws_metrics.py               + 1초 원자료
        │
        ├── script_parse/                        ← test_script_parse.py (983)
        │   ├── __init__.py                      +
        │   ├── conftest.py                      + FakeParser · pitch fixture · 요청 도우미
        │   ├── test_parse_flow.py               + 업로드→파싱→조회 · 입력 검사
        │   ├── test_parse_retry.py              + 재시도 · 만료 · 늦게 끝난 옛 작업
        │   ├── test_parse_races.py              + 권한 · 조건부 UPDATE · 동시 생성
        │   └── test_parse_runner.py             + 저장 실패해도 던지지 않음
        │
        └── take/                                ← test_take_api.py (735)
            ├── __init__.py                      +
            ├── conftest.py                      + auth_headers · pitch · _make_take
            ├── test_previous_missions.py        +
            ├── test_take_put.py                 +
            ├── test_take_abandon_delete.py      +
            └── test_take_validation.py          +
```

#### realtime의 폴더 경계

폴더는 처리 대상과 수명을 기준으로 나눕니다. 브라우저 WebSocket 연결이 바뀌어도 Take의 스트림과 아직 저장하지 못한 기록이 유지되어야 합니다. STT 재접속으로 바뀌는 상태도 이 둘과 구분합니다.

| 폴더 | 맡는 일 | 수명과 경계 |
|---|---|---|
| `connection/` | FE WebSocket의 인증 · 권한 확인 · 메시지 수신 · stream 연결과 분리 | 브라우저 연결 하나. 재접속하면 새 session 생성 |
| `stream/` | Take별 스트림 관리 · 오디오 큐 · 자동 종료 · 떠남 판단 · 종료 조율 | FE 연결보다 오래 유지. registry는 스트림이 교체되어도 이어질 Take별 기록을 보관 |
| `stt/` | 음성 인식 연결 · 재접속 · 프로토콜 변환 · 인식 용어 조립 | 외부 STT 세션별 상태. FE 연결이나 DB의 Take 상태를 직접 관리하지 않음 |
| `storage/` | 전사 · 1초 원자료 버퍼 · 저장 재시도 · take service 호출 | 실제 DB 저장과 기록 보관. Take 상태 전이 규칙은 take 도메인에 유지 |

`controller.py`는 FastAPI 진입점이고, `dependencies.py`는 STT 어댑터와 저장소를 조립해 주입하는 곳입니다. `dto.py` · `audio.py` · `event_ingestion.py`는 여러 폴더가 사용하는 메시지 · 오디오 규격 · 순수 프레임 처리이므로 realtime 바로 아래에 유지합니다. `common/` 같은 폴더를 추가하지 않습니다.

`fillers.py`는 `stt/`에 둡니다. 현재 BE에서 실제로 사용하는 것은 STT keyterm 조립에 넣는 `KEYTERM_FILLERS`이고, `FILLER_LEXICON`은 정의 외에 BE 실행 코드의 사용처가 없습니다. 후처리용 상수도 들어 있다는 이유만으로 realtime 전체의 공통 사전으로 분류하지 않습니다. 기존 상수는 함께 옮기며, 실제 파일 이동 시 STT 설정 조립 · 관련 테스트 · README의 경로를 갱신합니다.

`event_ingestion.py`의 순서 계산은 코드가 순수 함수 · 상태 객체라는 뜻이지, 상태를 프로세스 전체에서 공유한다는 뜻은 아닙니다. `FrameSequencer` 인스턴스는 각 runtime이 소유하고, 연결이 바뀔 때 기존 재연결 규칙에 맞춰 갱신합니다.

#### realtime의 의존 방향

다음은 목표 호출 관계입니다. 실제 코드 이동은 단계적으로 진행하며, 이름만 바꾸면서 기존 동작도 함께 재설계하지 않습니다.

- `controller.py → connection/session.py → stream/registry.py → stream/runtime.py`
- `connection/session.py → connection/handshake.py → auth · user · pitch · take의 service`
- `connection/session.py → stt/keyterms.py · stt/fillers.py`. 인증 후 대본 용어와 군더더기 사전으로 STT 설정 조립
- `stream/runtime.py → stt/loop.py → stt/adapter.py`
- `stream/runtime.py → storage/transcripts.py · storage/metrics.py → storage/store.py → take의 record · lifecycle service`
- `stream/registry.py → stream/abandon.py · stream/auto_end.py · storage/store.py`. 앱 재시작 복구와 떠남 만료 시 저장소를 통해 take service 호출

공통 메시지와 규격은 위의 모든 폴더가 참조할 수 있습니다. STT 이벤트와 타이머 만료는 전달받은 콜백 등 명시적인 연결을 통해 상위 처리로 돌려줍니다. stt · storage는 connection · stream을 역으로 import하지 않고, runtime도 registry를 import하지 않습니다. abandon은 registry를 import하지 않고 만료 콜백을 받습니다.

`connection/session.py`는 Origin · 인증 · 권한 확인과 접속 허용 여부를 handshake에 맡기고, 수신한 오디오 · metrics · stop을 stream에 전달합니다. STT 재접속 루프나 전사 저장 재시도는 직접 실행하지 않습니다. 현재 session이 수행하는 stop 뒤 drain · flush · 종료 저장 순서는 우선 그대로 옮기고, runtime의 공개 메서드와 저장소 호출을 통해 기존 순서를 유지합니다. 종료 경로를 하나로 합치는 변경은 파일 이동과 구분합니다.

`storage/store.py`는 기존 `TranscriptStore` Protocol과 `DbTranscriptStore`를 함께 옮깁니다. 전사뿐 아니라 metrics · 종료 저장 · 진행 중 Take 조회를 이미 맡고 있으므로 파일 이름을 `store.py`로 바꾸되 인터페이스 이름과 `get_transcript_store` 진입점은 우선 유지합니다. DB 구현은 저장할 때마다 짧은 세션을 열고 닫으며, 요청용 DB 세션을 장시간 스트림에 보관하지 않습니다. DB 변경과 커밋은 take service가 결정합니다.

#### realtime 분리 시 상태 소유권

현재 `take_stream.py`는 1,606줄입니다. 파일 수나 목표 줄 수보다 각 상태를 누가 생성 · 갱신 · 회수하는지를 먼저 정합니다.

| 파일 | 소유하는 상태와 역할 |
|---|---|
| `connection/session.py` | 현재 WebSocket 연결과 그 연결의 수신 루프. 재접속 시 새 객체로 교체 |
| `connection/handshake.py` | Origin · 인증 · 소유권 · 종료 상태 검사와 거절 응답. 스트림이나 전사 상태를 보관하지 않음 |
| `stream/registry.py` | Take별 스트림 맵 · attach 잠금 · 끝낸 Take 기록 · 스트림 교체 뒤에도 이어질 말 없음 기록 · 서버 경과 시간 · metrics 버퍼의 보관 |
| `stream/runtime.py` | TakeStream의 현재 클라이언트 · 프레임 순서 객체 · 오디오 큐 · 실행 태스크 · grace 대기 · 종료 플래그. STT와 기록 처리 객체를 조립 |
| `stream/auto_end.py` | 자동 종료 정책과 말 없음 판단. 판단과 타이머의 실행 · DB 저장을 구분 |
| `stream/abandon.py` | 떠남 · 탭 닫힘 대기 태스크의 예약과 취소. 만료 시 registry에 통지 |
| `stt/adapter.py` | Deepgram 연결 · 프로토콜 메시지 · 응답 이벤트 변환. DB와 Take 상태를 모름 |
| `stt/loop.py` | STT 접속 · 재접속 시도 · 세션별 시간 기준 · 무음 보충. 전사와 상태 이벤트를 runtime에 전달 |
| `storage/transcripts.py` | 전사 번호 · 저장 실패한 final · 재전송할 final · 재전송 중복 방지 잠금. 실제 WebSocket 전송은 runtime이 수행하고 성공 여부를 받아 버퍼를 갱신 |
| `storage/metrics.py` | 1초 원자료 버퍼 · 저장 재시도 태스크 · flush. 객체의 수명은 registry의 Take별 기록에 연결 |
| `storage/store.py` | 저장소 인터페이스와 DB 구현. 메모리의 스트림 레지스트리는 보관하지 않음 |

registry는 Take별 기록을 생성 · 보관하고 runtime에 필요한 기록 객체를 전달합니다. runtime은 전달받은 말 없음 기록 · 경과 시간 · metrics 버퍼를 갱신하되 registry의 맵이나 잠금에는 접근하지 않습니다. 이를 위해 여러 파일이 임의로 수정하는 전역 `take_state.py`를 추가하지 않습니다.

각 객체는 자신이 생성한 백그라운드 태스크를 보유하고 예외 확인 · 취소 · 완료 대기까지 책임집니다. 앱 종료 때 registry가 각 runtime과 Take별 기록의 정리를 기다립니다. 모듈을 옮기면서 태스크 참조가 사라지거나 flush가 끝나기 전에 저장소가 닫히지 않도록 합니다.

#### registry의 공유 범위와 앱 수명

registry는 **같은 앱을 실행하는 프로세스의 이벤트 루프 안에서 공유**합니다. HTTP 요청 · WebSocket 연결 · 재접속마다 새 registry를 만들지 않습니다. 연결마다 생성되는 것은 `connection/session.py`의 객체이고, 해당 연결은 기존 registry를 통해 같은 Take의 스트림을 찾습니다.

파일 이동 단계에서는 현재 레지스트리의 모듈 상태를 `stream/registry.py`로 함께 옮겨 이 공유 범위를 유지합니다. 클래스나 인스턴스로 바꾸는 작업은 파일 이동과 구분하며, 그 경우에도 앱 수명 동안 하나를 생성하고 모든 호출부가 그 인스턴스를 사용하게 합니다. `Depends`나 getter로 전달하더라도 호출할 때마다 새 registry를 생성하지 않습니다.

`main.py`의 기존 FastAPI `lifespan`을 시작 · 종료의 진입점으로 유지합니다. 시작과 종료를 같은 수명 경계에서 처리하는 기준은 [FastAPI lifespan 공식 문서](https://fastapi.tiangolo.com/advanced/events/)를 따릅니다.

| 시점 | 처리 |
|---|---|
| 앱 시작 | 자동 종료 정책을 검증하고, 같은 registry의 `arm_abandoned_takes`로 DB의 진행 중 Take에 떠남 타이머를 복구. 이때 테스트도 가짜로 바꿀 수 있는 저장소를 사용 |
| 요청 · 연결 · 재접속 | 같은 registry를 참조. Take별 attach 잠금과 종료 기록을 공유하고, 연결 하나가 끊겨도 다른 스트림이나 Take별 기록 전체를 초기화하지 않음 |
| 앱 종료 | 같은 registry의 `stop_all`로 스트림 drain · 기록 flush · 타이머와 백그라운드 태스크 정리를 기다림. 정리가 끝나기 전에 공유 자원을 해제하지 않음 |
| 테스트 종료 | 실제 실행과 같은 정리 진입점을 호출하고 registry의 스트림 · 종료 기록 · Take별 기록 · 잠금 · 타이머가 다음 테스트로 새지 않도록 fixture에서 초기화 |

이 계획의 운영 전제는 **앱 한 개 · uvicorn 워커 한 개 · 프로세스 한 개**입니다. 현재 스트림 · 잠금 · 종료 기록과 WebSocket은 프로세스 메모리에 있으므로 워커나 서버를 늘리면 다른 registry가 생깁니다. 다중 프로세스로 확장할 때는 Take별 스트림 소유권 · 요청 라우팅 · 종료/삭제 통지 · 공유 상태와 복구를 별도로 설계합니다. 단순히 dict를 Redis로 바꾸거나 폴더를 분리하는 것만으로 확장을 완료한 것으로 보지 않습니다.

메모리 기반 연결 관리의 단일 프로세스 제약은 [FastAPI WebSocket 공식 문서](https://fastapi.tiangolo.com/advanced/websockets/#handling-disconnections-and-multiple-clients)에서도 설명합니다.

#### realtime 이동 시 유지할 동작과 호출부

- FE 재접속 때 기존 스트림에 붙이고 전사 번호 · 저장 커서를 이어받는 동작, 새 연결로 교체된 옛 클라이언트가 stream을 종료하지 못하는 동작을 유지합니다.
- STT 재접속 때 세션 시간 기준 · 세션 번호 · 무음 구간을 함께 갱신합니다. STT 세션별 시간 보정은 loop가, FE 메시지 조립과 자동 종료 판단은 runtime이 맡도록 경계를 맞춥니다.
- final 저장 재시도 · 재전송 잠금 · 취소 시 저장 보호를 유지합니다. metrics 버퍼는 스트림이 바뀌었다고 비우지 않습니다.
- 수동 stop · 자동 종료 · Take 종료 PUT · 떠남 만료 · 삭제 · 앱 종료 각각의 drain · flush · DB 상태 저장 · 클라이언트 알림 순서를 기존 시나리오 테스트로 확인합니다. 스트림 정리와 Take 완료를 구분합니다.
- `main.py`의 시작 복구와 종료 정리, take · pitch · user controller의 `discard` · `end_without_stop` · `is_live` · `end_closed_tab` 등 기존 레지스트리 호출은 `stream/registry.py`로 경로를 갱신합니다. admin의 떠남 대기 상수 참조는 `admin/controller.py → stream/abandon.py`로 바꾸고 service에는 숫자 값으로 전달합니다. 기존 레지스트리 공개 함수 이름은 유지합니다.
- `dependencies.py`의 `get_stt_adapter` · `get_transcript_store`는 기존 테스트 교체 진입점으로 유지합니다. 각 모듈의 import와 monkeypatch는 실제 참조 위치를 기준으로 갱신합니다. 테스트 파일은 연결 · 전사 · metrics · 종료 시나리오별 구성을 유지하고 운영 폴더 구조를 그대로 복제하지 않습니다.

공유 상태를 분리하기 어려운 부분은 우선 `stream/runtime.py`에 함께 옮겨 둡니다. 폴더 이동과 책임 추출을 별도 변경으로 나눠 매 단계의 동작을 확인합니다.

#### 적용 순서와 확인

1. **큰 테스트 파일부터 나눕니다.** 위 트리의 `tests/auth` · `tests/realtime` · `tests/script_parse` · `tests/take` · `tests/coach`로 기존 테스트를 옮기고, 함께 쓰는 fixture만 각 `conftest.py`로 이동합니다. 테스트 개수 · 검증 내용 · fixture 범위를 보존합니다. 운영 코드와 테스트를 동시에 대규모로 재작성하지 않습니다.
2. **pitch와 take의 service를 나눕니다.** 함수의 책임에 맞춰 이동하고 controller · dependencies · realtime · 테스트의 import를 갱신합니다. `pitch/adapter`와 파싱 runner 이동도 관련 테스트와 함께 진행합니다. repository · entity · dto는 유지합니다.
3. **realtime은 위치 이동과 책임 추출을 나눕니다.** 우선 `service.py → connection/session.py`, `take_stream.py → stream/runtime.py`와 기존 STT · 자동 종료 · 저장소 파일의 이동만 진행하고 모든 import · monkeypatch 경로를 갱신합니다. 다음으로 handshake · registry · abandon · `stt/loop.py` · `storage/transcripts.py`를 한 책임씩 추출합니다. 단계마다 STT · 인식 용어 · 재연결 · 전사 · metrics · 자동 종료 · 취소 경합 · 시작 복구 · 앱 종료 시나리오를 확인합니다. registry의 공유 범위와 `lifespan` 연결을 유지하고, admin의 정책 값 전달 변경은 별도 변경으로 진행합니다. 코드를 옮기면서 종료 절차나 트랜잭션 경계를 동시에 바꾸지 않습니다.
4. **coach와 문서를 정리합니다.** 응답 모델 · 창 계산 · 코칭 계획처럼 분명한 책임을 추출하고, `backend/README.md`의 구조와 실제 import가 일치하도록 갱신합니다. README의 realtime 의존 규칙은 위의 controller와 도메인 내부 구분에 맞추고, 단일 프로세스 운영 전제와 registry의 앱 수명을 함께 적습니다. 긴 동작 설명은 `docs/be/`로 옮겨 링크합니다. `backend/docs/`는 Git 제외 경로이므로 이 계획을 팀에 공유할 때는 `docs/be/file-split-plan.md`로 옮기고 문서 지도에 추가합니다.

실제 코드 분리 시 실행 위치는 `backend/`입니다. 아래 명령은 `backend/README.md` · `pyproject.toml`에 있는 검증 절차이며, 이번 문서 수정에서 실행한 결과를 뜻하지 않습니다.

| 확인 | 명령과 기준 |
|---|---|
| 테스트 수집 | 이동 전후 `uv run pytest --collect-only -q`. 테스트 개수와 시나리오가 유지되는지 확인 |
| 동작 회귀 | 단계별 관련 테스트 실행 후 최종 `uv run pytest`. PostgreSQL 테스트 DB와 Redis 15번 DB 등 README의 테스트 환경 필요 |
| import · 코드 스타일 | `uv run ruff check` · `uv run ruff format --check` |
| 모델 등록 | 구조 변경 전후 각각 같은 테스트 환경에서 `entities`를 import한 뒤 `Base.metadata.tables`의 테이블 이름 · 개수 비교. 모델 누락이나 의도하지 않은 스키마 변경이 없어야 함 |
| monkeypatch | 함수가 정의된 곳뿐 아니라 **호출부가 실제로 참조하는 위치**를 기준으로 대상 경로 갱신. 기존 가짜 저장소 · 외부 클라이언트가 계속 사용되는지 확인 |
| 의존 경계 | controller의 공개 registry 호출은 허용하되, 도메인 service · repository · entity의 realtime import와 realtime의 도메인 controller import가 없는지 확인. admin은 인자로 받은 정책 값으로 기존 결과를 반환해야 함 |
| registry 수명 | 서로 다른 연결과 재접속이 같은 registry를 사용하는지, 앱 시작 복구 · 종료 drain/flush · 테스트 간 상태 정리가 유지되는지 확인. 단일 프로세스 검증이며 다중 워커 동작을 보장하지 않음 |

API 경로 · 요청/응답 · DB 스키마를 유지하는 구조 변경으로 진행합니다. 동작을 바꿔야 하는 문제를 발견하면 구조 분리와 구분해 다룹니다. 각 단계의 검증 결과에는 실행 위치 · 명령 · 결과 · 실행하지 못한 범위를 기록합니다.
