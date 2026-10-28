# 실시간 STT (WebSocket)

코드 경로는 `backend/src/pitch_coach_backend/` 기준이고, 명령은 `backend/` 에서 실행한다.
설치 · 실행 · 다른 API 는 [backend/README.md](../../backend/README.md) 에 있다.

발표 중 마이크 오디오를 Deepgram 으로 흘리고 전사를 FE 로 돌려준다. **BE 가 Deepgram 에 API 키를
붙인다** — 브라우저 WebSocket 은 헤더를 못 붙이므로 FE → BE → Deepgram 구조가 필수다.

```
WS /api/ws/takes/{take_id}
```

| 순서 | 방향 | 프레임 | 내용 |
|---|---|---|---|
| 1 | FE → BE | text | `{"type":"auth","token":"<access>"}` — 5초 안에 와야 한다. 브라우저는 헤더를 못 붙이므로 첫 메시지로 |
| 2 | BE → FE | text | `{"type":"ready","take_id":…,"stt_session_no":0,"stt_state":"connecting"}` — 사용자·Take(존재·소유·상태) 검사를 통과했다. 이제 오디오를 보내도 된다 |
| 3 | FE → BE | **binary** | `[seq u32 LE][offset_ms u32 LE][PCM 16-bit LE 16 kHz mono]`. 100 ms = 3,200 bytes. `offset_ms` 는 Take 시작 = 0 |
| 4 | BE → FE | text | `{"type":"transcript","segment_id","is_final","speech_final","start_ms","end_ms","text","confidence","words":[…]}` — `segment_id` 가 같으면 덮어쓴다. **타임스탬프는 Take 기준** |
| 4-1 | FE → BE | text | `{"type":"metrics","t_ms","gaze","voice","slide_number","buffered"}` — 1초마다 시선 · 음량 1초 기록. `t_ms` 는 그 1초가 끝난 Take 시각. `slide_number` 는 참고값(장은 `slide` 로 정한다). `buffered` 는 끊긴 동안 쌓아 둔 것을 다시 보낼 때만 `true`(없으면 false). `stop` 직전에 마지막 1초 조각을 한 번 더 |
| 4-2 | FE → BE | text | `{"type":"slide","slide_number","t_ms"}` — 장을 넘길 때마다. Take 시작(1번 장, `t_ms: 0`) · 새로고침해 이어 갈 때(이어받은 장과 그 장으로 넘어온 원래 시각)도 보낸다. 끊긴 동안 넘긴 장은 다시 붙은 뒤 시각 순서대로 |
| 4-3 | BE → FE | text | `{"type":"coach","t_ms","action","feedback","indicators"}` — `COACHING` 모드에서 코치가 말을 걸 때(`INTERVENE`, `feedback` 이 있음)와 상태 표시(`indicators`)가 바뀔 때만. `EXAM` 은 보내지 않는다. 빈 `indicators` 는 '바뀌지 않음' |
| 5 | FE → BE | text | `{"type":"stop"}` — BE 가 Deepgram 을 정리(`CloseStream` → `Metadata`)하고 |
| 6 | BE → FE | text | `{"type":"stt_status","state":"closed",…}` 를 보낸 뒤 `1000` 으로 닫는다. **FE 는 이걸 받기 전에 닫지 않는다** |

- **한 번 보낸 오디오 프레임은 재연결 뒤에도 다시 보내지 않는다.** `seq` 는 Take 안에서 계속 오르고 재연결해도
  이어 센다. FE 는 아직 못 보낸 프레임만 들고 있다가 `ready` 뒤에 순서대로 보낸다. BE 는 새 연결의 첫 프레임이
  뒤로 가면 탭을 새로고침해 처음부터 다시 센 것으로 본다 — 보낸 프레임을 다시 보내면 중복이 아니라 재시작으로
  읽힌다. 확인 응답이 없을 때 다시 보내는 기능을 넣으려면 먼저 이어 보내는지 처음부터 다시 세는지를 FE 가
  첫 메시지에서 알려 주도록 이 규약부터 바꾼다.
- `stt_status` 는 상태가 바뀔 때마다 온다. `connecting` → `ok` → (`reconnecting` → `degraded`) → `closed`.
  숫자(`frames`·`dropped_frames`·`silence_ms`·`lost_ms`)는 연결 단위가 아니라 **Take 누적**이다.
- **Deepgram 이 죽어도 연결을 끊지 않는다.** 오디오를 최대 5초 큐에 담고 백오프로 재접속하며
  `degraded` 만 알린다 — 그동안 FE 1단 코치는 계속 돌아야 하기 때문이다.
- **키가 거절되면 다음 키로 넘어간다** (`DEEPGRAM_API_KEYS`). 연결 요청이 401(틀린 키) · 402(크레딧 부족)로
  거절되면 그 키를 소진으로 표시하고 기다리지 않고 다음 키로 붙는다. 발표 도중 크레딧이 떨어져 끊겨도 재접속이
  402 로 거절되므로 같은 길로 넘어간다. 소진 표시는 서버 메모리에만 있고 모든 Take 가 같이 쓴다. 키가 전부
  소진이면 에러 로그를 남기고 위처럼 `degraded` 로 재시도하며, 그때는 첫 키부터 다시 확인한다
- **Deepgram 은 발표 음성 · 전사를 보관하지 않는다.** 연결마다 `mip_opt_out=true` 를 붙여 Deepgram 모델 개선
  프로그램에서 빠진다(기본은 참여 · 보관). 붙이면 처리하는 동안만 쓰고 저장하지 않는다. Pay as you Go · Growth
  요금제는 붙여도 요금이 같다(Deepgram 2026-03-05 변경 기록). 우리 쪽 원자료 보관은 이것과 따로다
  ([realtime-coach.md](realtime-coach.md))
- 에러는 `{"type":"error","code","message"}`. **에러가 곧 종료는 아니다** — `code` 마다 다르다
  (`realtime/dto.py` 의 `WsErrorCode`).

  | code | 연결 |
  |---|---|
  | `UNAUTHORIZED` | `1008` 로 닫는다 — 토큰이 없거나 틀리거나 사용자가 없다. 연결 중에 그 사용자가 로그아웃해도 이 코드로 닫는다(Take 는 끝내지 않고 grace 를 건다). FE 는 토큰을 한 번 새로 받아 다시 붙는다 |
  | `TAKE_NOT_FOUND` | `1008` 로 닫는다 — Take 가 없거나 내 것이 아니다 (REST 404 처럼 둘을 구분하지 않는다). 연결 중에 Take 나 발표가 지워져도 이 코드로 닫는다 |
  | `TAKE_ENDED` | `1008` 로 닫는다 — 이미 끝난 Take (`ANALYZING`·`COMPLETED`·`FAILED`. 자동 종료된 Take 도 `ANALYZING` 이다). FE 는 **재연결하지 않는다**. BE 가 끝냈으면 `end_reason`(`SILENCE`·`TIME_LIMIT`·`ABANDONED`·`CLOSED`)을 싣는다 — `CLOSED` 는 이어 가지 않고, `ABANDONED` 는 끝내기 PUT 이 되살린다 |
  | `FORBIDDEN` | `1008` 로 닫는다 — 다른 사용자가 쓰고 있는 스트림 (정상 흐름에서는 안 나온다) |
  | `BAD_MESSAGE` | 유지 |
  | `BAD_AUDIO_FRAME` | 유지. 연결당 첫 오류만 알린다 |
  | `TAKE_TAKEN_OVER` | `1008` 로 닫는다. FE 는 **재연결하지 않는다** |

- `Origin` 이 있으면 `FRONTEND_BASE_URL` 과 같아야 한다. 없으면(스크립트) 통과.
- **auth 를 기다리는 연결은 IP 하나에 20개, 전체 200개까지다** (`handshake.pending_auth`). 넘으면 받기 전에
  닫아 브라우저에는 연결 실패(닫힘 코드 `1006`)로 보이고 FE 는 백오프로 다시 붙는다. IP 는 `X-Forwarded-For` 의
  첫 값이다 — 바깥 Caddy 가 믿지 않는 상대가 보낸 값을 버리고 상대 주소로 채운다(`infra/Caddyfile` 의
  `trusted_proxies`). Caddy 를 거치지 않고 BE 에 바로 붙을 수 있게 열면 이 값을 꾸밀 수 있어 IP 별 상한이 의미가
  없어진다. 인증 실패 알림 전송과 닫기도 각각 1초까지만 기다려, 응답을 받지 않는 상대가 인증 전 슬롯을
  계속 붙잡지 못하게 한다.
- **인증 뒤 Take 를 검사한다.** 사용자 존재 → Take 존재·소유(`takes` → `pitches.user_id`) → 상태
  (`READY`·`RUNNING` 만 통과) 를 DB 왕복 한 번으로 본다. `READY` 는 첫 연결이라 받고, 통과하면
  `RUNNING` 으로 바꾼다 (`docs/ai/architecture.md` 1-3). 못 바꾸면(DB 오류) `1011` 로 닫아 FE 가 다시 붙게
  한다 — `READY` 로 남은 채 발표하면 시작하지 않은 Take 로 정리된다. 그사이 정리됐으면 `TAKE_ENDED`(`ABANDONED`).
- **Take 를 시작할 때(첫 연결) 그 Take 에 쓸 값을 고정한다** (`takes.mission_ids` · `standard_version` ·
  `raw_data_consent`). 미션은 리뷰가 있는 Take 중 가장 늦게 끝난 Take 의 리뷰가 만든 `ACTIVE` 미션이고, 그 행의
  `take_id` 를 이 Take 로 바꾼다. Take 를 만들 때 싣지 않는다 — 장치 점검만 하고 버린 Take 가 미션을 가져간다.
  시작 값 저장의 일시 오류는 한 번 다시 시도한다. 끝내 저장하지 못하면 `ready` 전에 1011로 닫아
  재연결하게 한다. 고정되지 않은 기본값으로 코치를 켜거나 원자료 보관 여부를 결정하지 않는다.
  이어서 코치가 Take 동안 들고 있을 값(시간 계획 · 미션 · 반복 문제 · 고른 코칭 계획 · 대본 사용 설정 · 기준
  음량 · 저장된 장 변경)을 읽는다. 못 읽어도 STT 는 돌고 코칭만 끈다.
- **final 은 `take_transcript_segments` 에 저장한다** (interim 은 저장하지 않는다). 리포트의 유일한
  원천이다. 시각은 Take 기준(ms), `words` 는 `[{word, punctuated_word, start_ms, end_ms, confidence}]`
  JSONB. 저장은 realtime 이 아니라 `take/service/record.py` 가 하고(`append_transcript_segment`), 스트림이
  DB 를 직접 만지지 않도록 `realtime/storage/store.py` 가 저장할 때마다 짧은 세션을 연다.
  **저장 실패는 스트림을 멈추지 않는다** — 로그만 남기고 화면 전사는 계속 나간다. 단 Take 가 지워져
  저장할 곳이 없으면(외래 키 위반) 아래처럼 스트림을 끊는다.
- **`metrics` 는 `take_metric_seconds` 에 1초 원자료로 저장한다** (`realtime/storage/metrics.py`). Take 결과를
  다시 만들 때(`/coach/finalize` 의 `replay`) 읽는다. 시선 · 음량 기록은 해석하지 않고 받은 그대로 두고, 받았을 때의
  STT 상태(`stt_status`)를 같이 남긴다. 5개가 모이면 한 번에 넣고, 연결이 끊기거나 스트림이 닫히면 남은 것을
  넣는다. FE 는 끊긴 동안 쌓아 둔 것을 다시 붙은 뒤 보내므로 같은 1초가 또 온다 — `(take_id, t_ms)` 로 먼저
  저장한 것을 둔다. 저장에 실패하면 10분 치까지 들고 있다가 다시 넣는다. 잘못된 `metrics` 는 `BAD_MESSAGE` 다.
- **`slide` 는 `take_slide_events` 에 저장한다.** 같은 시각은 먼저 저장한 것을 둔다. 실시간 코치의 지금 장 ·
  replay 의 장 변경 · 대본 전달도의 장 구간이 이 표를 쓴다.
- **확정 단어에는 확정 시각(`take_transcript_segments.final_at_ms`)을 붙인다.** 그 결과를 받기 직전에 받은 마지막
  `metrics` 의 `t_ms`(아직 없으면 0)다. replay 가 단어를 원래 들어온 순서대로 다시 넣는 데 쓴다. Deepgram 문장 끝
  신호(`UtteranceEnd`)는 Take 동안 메모리에 두고, Take 를 끝낼 때 `takes.utterance_ends` 에 한 번에 쓴다.
- **Take 나 발표를 지우면 그 Take 의 스트림을 바로 끊는다.** 지우는 요청이 커밋한 뒤 Deepgram 을
  닫고, 붙어 있는 FE 에는 `TAKE_NOT_FOUND` 를 보낸다. 저장할 곳이 없으니 `stop` 처럼 남은 전사를
  기다리지 않는다. 끊기 전에 온 final 이 저장에 실패해도 스트림이 그걸 보고 스스로 끊는다.
- **STT 파이프라인은 연결보다 오래 산다** (`realtime/stream/`). 연결이 끊겨도 30초 동안 Deepgram
  세션을 살려 두고, 그 안에 다시 붙으면 세그먼트 번호와 프레임 수가 이어진다. 떨어져 있는 동안
  화면에 못 간 final 은 모아 두었다가 `ready` 직후 순서대로 다시 보낸다 (DB 에는 이미 있다).
  번호가 리셋되면 FE 가 같은 `segment_id` 를 덮어써 **발표 앞부분 전사가 사라진다** — 그래서
  30초가 지나 스트림이 사라진 뒤 다시 붙어도 **저장된 마지막 `seq`·`stt_session_no` 에서 이어 받는다.**
  오디오를 한 번도 안 받은 스트림은 살려 둘 전사가 없어서 끊기면 바로 정리한다.
- 스트림은 만든 사용자에게 묶인다 (`FORBIDDEN`). 위 DB 검사의 이중 안전장치라 정상 흐름에서는 나오지 않는다.
- 같은 사용자의 두 번째 연결이 오면 **기존 것을 `TAKE_TAKEN_OVER` 로 쫓아낸다** (탭 복구).
  반대로 하면 재연결 레이스에서 사용자가 영영 못 붙는다. 쫓겨난 연결이 닫히기 전에 보낸 프레임과 `stop` 은 버린다.
  정리 중인 스트림에는 붙이지 않고
  끝나기를 기다렸다가 새로 만든다.
- Deepgram 타임스탬프는 "그 연결의 첫 바이트 = 0" 이라 BE 가 그 세션 첫 프레임의 `offset_ms` 를 더한다.
  프레임 사이 갭(> 50 ms) 은 **무음으로 채워** 두 타임라인을 맞추고, 5초를 넘는 갭은 무음 대신
  **Deepgram 세션을 갈아** base 를 다시 잡는다 — 일부만 채우면 그 뒤 전사 시각이 통째로 앞당겨진다.
  역행·중복 `seq` 는 버린다. 단 **새 연결의 첫 프레임**이 뒤로 가면(`seq` 또는 `offset_ms`) FE 가 처음부터
  다시 센 것이므로 버리지 않고 세션을 갈아 그 offset 으로 base 를 잡는다. 탭을 새로고침하면 FE 는 `seq` 를
  1 부터 다시 세고 `offset_ms` 는 이어받은 무대 시계부터 보낸다 (grace 가 지나 새 스트림이 열린 경우와 같은 결과). 세션이 끝나면 Deepgram 이 보고한 길이와 우리가 보낸 길이를 대조해
  어긋나면 로그에 남긴다.
- 한국어 군더더기("음", "어", "그", "이제")는 Deepgram 이 기본으로 버리므로 `realtime/stt/fillers.py` 의
  목록을 `keyterm` 으로 넘겨 전사에 남긴다. `filler_words` 옵션은 영어 전용이다.
- filler 뒤에 Take 대본의 용어를 `keyterm` 으로 붙인다 (고유명사 오인식 방지). 파싱된 `terms` 만
  우선순위 순으로 쓰고, 슬라이드 `keywords` 는 발음 잡기용이 아니라 넣지 않는다. 인가 때 같은 DB 왕복에서
  읽고, 파싱이 안 끝난 대본이면 filler 만 보낸다.
  Deepgram 한도는 **100개·전체 500 토큰**이고 넘으면 연결 자체가 거절된다. 토큰 수는 실측으로 정한 상한
  (한글·ASCII 글자당 1, 그 밖 문자는 UTF-8 바이트, 용어당 +2)으로 세고 50 토큰을 남긴다 (`realtime/stt/keyterms.py`).
  한도로 빠진 개수는 로그에 남는다. 그래도 추정이 빗나가 Deepgram 이 400 으로 거절하면 뒤쪽 대본 용어를
  5개씩 빼며 기다리지 않고 바로 다시 붙는다 (filler 는 남긴다). 용어 조회가 실패해도 연결은 닫지 않고 filler 만 쓴다.
- 정리가 늦어도 FE 를 붙잡아 두지 않는다. `stop` 뒤 Deepgram drain 이 12초 안에 안 끝나면 태스크를
  끊고, 그마저 2초 안에 안 끝나면 닫힌 것으로 확정해 `closed` 를 보낸다. Deepgram 소켓 닫기 자체도
  3초 상한이 있다. `stop` 을 보낸 직후 FE 가 사라져도(탭 닫힘) 서버 쪽 종료·저장 절차는 같다.
- **`stop` 이 Take 를 끝낸다.** 코치 판단을 멈추고(진행 중인 것은 기다린다) 마지막 확정 전사 · 1초 원자료 · 장 변경까지
  저장한 뒤 Take 를 `ANALYZING` · 분석 단계 `FINALIZING` 으로 바꾸고(문장 끝 신호도 같이) `closed` 를 보낸다
  (`ended_at` 이 비어 있으면 지금 시각으로 채운다). 이어서 Take 결과([realtime-coach.md](realtime-coach.md))를 백그라운드로 만든다. `stop` 을 받은 순간부터 같은 Take 로 붙는 연결은
  `TAKE_ENDED` 로 거절한다 — DB 에 적기 전의 틈은 프로세스 안의 기록이 막는다. 종료를 DB 에 못 적어도
  `closed` 는 보낸다. 30초 대기 만료 · 오디오 없는 연결 · 앱 종료는 스트림만 정리하고 Take 는 끝내지 않는다
  (FE 는 끊긴 뒤에도 다시 붙는다).
- **아무도 다시 붙지 않으면 BE 가 Take 를 실패(`FAILED`)로 끝낸다.** 스트림이 끝나지 않은 채 정리된 뒤 기다리는
  시간과 이유는 끊긴 방식으로 정한다. 시선 기록 · 끝난 시각을 FE 가 보내지 못한 Take 라 분석하지 않는다.
  `ended_at` 은 마지막으로 연결이 끊긴 시각이다. 그 안에 다시 붙으면 이어서 연습하고, 그 뒤로는 `TAKE_ENDED` 로
  거절한다. 전체 조건은 [Take 연결 · 재연결 · 종료 조건](take-lifecycle.md) 에 있다.
  - 창을 닫거나 새로고침해 끊김(닫힘 코드 1001): **2분** 기다리고 `CLOSED`. 새로고침이면 그 안에 다시 붙는다.
    창을 닫은 Take 는 이어 가지 않는다.
  - 그 밖(네트워크 장애 · FE 가 재연결을 포기함): **5분** 기다리고 `ABANDONED`. 재연결을 포기한 FE 는 STT 없이
    발표를 이어 가는 중일 수 있다. 그 FE 가 발표를 마치고 보내는 끝내기 PUT 이 되살린다 (아래).
  - 타이머는 메모리에 있어서 앱이 다시 뜨면 `RUNNING` Take 의 마지막 활동 시각(만든 시각 · 시작 시각 · 마지막
    전사 저장 시각 중 가장 늦은 것)에서 5분으로 다시 건다. 닫힘 코드도 사라져 `ABANDONED` 로 적는다. 이 Take 들을 10초 안에 읽지 못하면 걸지 않고 앱을 띄운다 (다음에 연결이 붙었다 끊길 때 걸린다).
- **창을 다시 연 FE 는 Take 를 바로 실패로 끝낸다.** `POST /api/pitches/{pitch_id}/takes/{take_id}/abandon`.
  `READY` · `RUNNING` 이면 `FAILED`(`CLOSED`)로 바꾸고(끝난 시각은 마지막 활동 시각) 타이머를 끈 뒤 남은 스트림을
  끊는다. 서버가 아직 붙어 있다고 믿는 연결(닫힘 프레임 없이 사라진 브라우저)에는 `TAKE_ENDED` 를 보내고 닫는다.
  이미 끝났으면 바꾸지 않는다. 어느 쪽이든 `{"take_id","status"}` 로 지금 상태를 돌려준다 (다시 불러도 같다).
  연결이 붙어 있고 최근 10초 안에 오디오가 왔으면 다른 창이 발표 중이라 `409 TAKE_IN_PROGRESS` 로 거절한다.
- **STT 없이 끝낸 Take 는 Take PUT 이 끝낸다.** 발표 중에 재연결을 포기한 FE 는 `stop` 을 보내지 못한다.
  종료 흐름의 `PUT /api/pitches/{pitch_id}/takes/{take_id}` 에 `ended_at` 이 있으면 BE 가 `RUNNING` 을 `ANALYZING`
  으로 바꾼다. 시작하지 않은 Take(`READY`)는 `409 TAKE_NOT_STARTED` 로 거절하고 시각도 적지 않는다.
  연결이 붙어 있거나 `stop` 으로 끝내는 중이면 그 연결에 맡긴다. 끊긴 뒤 30초 안이라 스트림이 남아 있으면
  `stop` 처럼 남은 전사까지 저장한 뒤 바꾼다. 떠난 것으로 끝낸 Take(`ABANDONED`)도 `ANALYZING` 으로 되살리고
  이유를 지운다. 창을 닫아 끝낸 Take(`CLOSED`)는 되살리지 않는다.
- Take PUT 은 상태와 순서를 본다. `COMPLETED`·`FAILED` 는 `409 TAKE_ENDED`, `ANALYZING` 은 시각만 받는다
  (`event_logs` 는 409). `ABANDONED` 로 끝난 Take 는 `ended_at` 이 있는 끝내기만 받는다 (`CLOSED` 는 409).
  `ended_at` 이 `started_at` 보다 앞서면 `400 INVALID_TAKE_TIME`, 시간대가 없는 시각은 422.
  Take 를 만든 때 1일 전 ~ 지금 1일 뒤를 벗어난 시각도 `422 VALIDATION_ERROR`.
  `null` 은 보내지 않은 것으로 본다. 점검 결과(`calibration`)는 `READY` 일 때만 받는다 (아니면 `409 TAKE_STARTED`).
- **Take 를 자동으로 끝낸다** (`realtime/stream/auto_end.py`). 둘 중 하나라도 맞으면 `{"type":"auto_end","reason","message"}`
  를 보내고 `stop` 과 같은 순서로 정리한다 (drain → `ANALYZING` → `closed` → `1000`). 오디오는 그 자리에서 끊는다.
  FE 는 끝내기 버튼과 같은 종료 흐름을 타고 `message` 를 보여 준다.
  - `SILENCE` — 최근 2분 동안 말이 없고 직전 5분 중 80% 이상이 말 없음. **전사로 판단한다** (소리 크기는
    소음에도 커진다). 시간은 벽시계가 아니라 Deepgram 이 final(빈 final 포함)로 "들었다" 고 확인한 오디오로
    잰다 — STT 가 죽어 있거나 오디오가 유실된 구간, BE 가 타임라인을 맞추려고 메워 보낸 무음(프레임 사이 틈 ·
    큐가 넘쳐 버린 오디오)을 말 없음으로 세면 STT · 네트워크 장애가 곧 강제 종료가 된다. 기록은 Take 에
    붙어 있어 30초 넘게 끊겼다 새 스트림으로 붙어도 이어진다.
  - `TIME_LIMIT` — 1시간. 기준은 프레임의 `offset_ms`(FE 무대 시계)다. offset 이 가지 않는 Take(오디오 없이
    연결만 붙여 둠)는 서버 시계가 막는다 — **이 Take 의 스트림이 열려 있던 시간의 합**이 1시간 + 60초를
    넘으면 끝낸다. 스트림이 없던 시간(탭이 죽어 있던 동안)은 세지 않는다. FE 가 떨어져 있는 동안(grace)에도 끝낸다.
  - `stop` 처럼 Take 를 `ANALYZING` 으로 바꾸고, `takes.end_reason` 에 이유를 남긴다. `auto_end` 를 받은 FE 가
    `stop` 을 보내도 이유는 그대로 남는다. 다시 붙으면 `auto_end` 를 한 번 더 보내고 `TAKE_ENDED` 로 거절한다 —
    자동 종료 순간 떨어져 있던 FE 도 종료 흐름을 탄다. 이유를 못 적어도(DB 장애) 종료는 그대로 한다.
  - 판단 코드가 예외를 내도 STT 는 계속 돈다 (로그만). 값은 `TAKE_SILENCE_*`·`TAKE_MAX_DURATION_SEC` 로 바꾸고,
    틀린 값이면 앱이 시작할 때 실패한다.

FE 없이 확인하는 두 가지 —

| | 무엇 | 어떻게 |
|---|---|---|
| 파일 | `dev/stt-send-pcm.py` | `uv run python dev/stt-send-pcm.py 녹음.pcm --email <가입한 이메일>` (파일 상단에 변환 명령). `--take-id` 를 안 주면 그 사용자의 Take 를 하나 만들어 쓴다 |
| 마이크 | `dev/stt-test.html` | `python3 -m http.server 3000 --directory dev` 로 띄우고 `http://localhost:3000/stt-test.html`. **3000 포트로 열어야** Origin 검사를 통과한다. Take 칸에는 `uv run python dev/stt-send-pcm.py --create-take --email <이메일>` 로 만든 id 를 넣는다 |

1초 원자료 저장 부하는 `dev/metrics-load-test.py` 로 잰다. 같은 Postgres 에 임시 DB 를 만들어 Take 마다 1초에
1행씩 넣고 끝나면 지운다. 묶음 크기 5(`METRICS_BATCH`)는 develop 서버에서 잰 결과(#338)로 정했다.
