# 대본 파싱 (AI)

코드 경로는 `backend/src/pitch_coach_backend/` 기준이고, 명령은 `backend/` 에서 실행한다.
설치 · 실행 · 다른 API 는 [backend/README.md](../../backend/README.md) 에 있다.

올린 대본을 AI 서버가 슬라이드 단위로 나누고 키워드를 뽑는다. **BE 가 AI 를 부르고 기다린다** —
AI 는 BE 주소를 모른다. FE 쪽은 비동기다 (즉시 `202` + 폴링).

```
POST /api/pitches/{pitch_id}/scripts                          202  {"content"} → script_version_id·version, PENDING
GET  /api/pitches/{pitch_id}/scripts/{script_version_id}           PENDING | DONE(+slides) | FAILED(+error_code)
POST /api/pitches/{pitch_id}/scripts/{script_version_id}/parse  202  FAILED 일 때만. 아니면 409
POST /api/pitches/add/{pitch_id}/presentation                     발표자료(PDF)만. 대본은 받지 않는다
```

- **발표자료는 BE 가 PDF 로 열어 본다** (`pitch/adapter/presentation_file.py`). 40MB 를 넘거나 열리지 않으면(PDF 가 아님 · 깨짐 ·
  여는 암호) `400 INVALID_PRESENTATION` 이고 저장하지 않는다. 쓰기 제한만 걸린 PDF 는 받는다. 확장자 · MIME 은 보지 않고,
  S3 에는 `{version}.pdf` · `application/pdf` 로 올린다. 연 김에 쪽수를 `presentation_versions.page_count` 에 두고
  업로드 응답 · `/resources` · 발표자료 상세에 싣는다. 이 칸이 생기기 전에 올린 발표자료는
  `uv run python dev/backfill-presentation-page-count.py` 로 채운다 (`--dry-run` 으로 먼저 본다).
- **S3 에 올린 뒤 실패하면 한쪽만 남기지 않는다.** 조회 URL 은 커밋 전에 만들고, 버전을 저장하지 못하면 올린 파일을 지운다.
  커밋이 실패했으면 버전 행이 없을 때만 지운다 — 커밋 응답만 잃었으면 그 파일을 버전이 가리킨다. 지우지 못한 파일은 같은 번호의
  다음 업로드가 덮어쓰고 발표를 지울 때 같이 지워진다.
- **대본은 발표자료와 따로 올린다.** 대본만 여러 번 고치는 게 정상 흐름이라, 발표자료 업로드에 묶으면 PDF 를 매번 다시 올려야 한다.
  버전은 pitch 안에서 자동으로 1, 2, … 로 매기고, 발표자료 버전과는 따로 센다.
- 대본은 JSON `{"content": "..."}` 로 받는다 (FE 는 textarea 내용 그대로). 비어 있거나 5만 자를 넘거나 NUL 문자가 있으면
  `400 INVALID_SCRIPT` 이고 아무것도 저장하지 않는다. NUL 은 JSON(`\u0000`)으로는 오지만 PostgreSQL TEXT 가 받지 못한다.
  AI 응답에 NUL 이 있어도 `AI_INVALID_OUTPUT`.
- 대본을 올리면 pitch 행을 `FOR NO KEY UPDATE` 로 잠그고 버전을 매긴다. 같은 pitch 에 동시에 올려도 버전이 겹치지 않는다
  (잠금이 없으면 둘 다 `max+1` 을 받아 한쪽이 unique 위반 500). `FOR UPDATE` 는 같은 pitch 의 Take 생성까지 막아서 쓰지 않는다.
- **대본 원문은 S3 가 아니라 DB(`script_versions.content`)에 둔다.** 5만 자 이하 텍스트라 TEXT 로 충분하고, 슬라이드와
  한 트랜잭션으로 저장되며, 재시도·편집 화면이 네트워크 호출 없이 읽는다. S3 는 발표자료(PDF)만 쓴다.
  `script_slides.full_content` 는 AI 가 구분자·제목을 빼고 공백을 합친 글이라 원문을 대신하지 못한다.
- 이 기능 전에 올라온 대본은 원문이 S3 파일(`pitches/{pitch_id}/scripts/{version}.<확장자>`)로만 있어 migration 이 `FAILED (LEGACY_UNPARSED)` 로 두고,
  재시도하면 `409 SCRIPT_REUPLOAD_REQUIRED` — 새로 올려야 한다.
- BE → AI 는 `POST {AI_BASE_URL}/scripts/parse` `{"script_text", "target_ms"}`. `target_ms` 는 발표 제한 시간(`time_limit_sec` × 1000)이고,
  다시 파싱할 때는 그때의 제한 시간을 싣는다. 응답 모양은 [`docs/ai/features/script-parser.md`](../ai/features/script-parser.md) 5절이고
  `pitch/adapter/script_parser.py` 가 바꾼다 (`status` → `segmented`, highlight `{"start": 0, "end": 6}` · `"0:6"` → `[0, 6]`, 틀린 highlight 는 그 항목만 버림).
  highlight 는 AI 가 새 모양으로 바꾸는 동안 두 모양을 다 받는다.
  시도 한 번 전체 30초(httpx 의 읽기 30초·연결 5초는 단계별 상한이라 따로 묶는다). 타임아웃·연결 실패·5xx·429·408·계약 위반은
  1초 뒤 1회 재시도, 그 밖의 4xx 는 바로 실패. 결과 저장이 실패하면 `FAILED (INTERNAL_ERROR)` 로 남긴다 (PENDING 으로 두지 않는다).
- 장별 `script_chars` · `expected_ms` 는 `script_slides` 에, 응답의 `meta` 에 요청한 `target_ms` 를 더한 것은 `script_versions.parse_meta` 에 둔다.
  이 값들을 주지 않는 응답(지금 AI 연구 코드)이면 비워 둔다. 제한 시간만 바뀌면 다시 파싱하지 않는다 — 쓰는 쪽이 `expected_ms` 를 `parse_meta.target_ms` 에 대한 비율로 읽는다.
- 작업은 `BackgroundTasks` + `async`. **AI 를 기다리는 동안 DB 세션을 쥐지 않고**, 저장만 threadpool 의
  짧은 세션으로 한다 (`realtime/storage/store.py` 와 같은 이유 — 같은 이벤트 루프에서 STT 가 돈다).
- 상태 전이는 전부 **조건부 UPDATE** 다. 재시도 두 번 눌러도 작업은 하나, 늦게 끝난 옛 작업은
  `parse_requested_at` 이 달라 새 결과를 못 덮는다. 만료가 지난 작업도 쓰지 못한다 (FAILED 가 DONE 으로 뒤집히지 않게).
- BE 가 재시작돼 작업이 사라지면 PENDING 이 남는다. 요청 후 **90초**가 지난 PENDING 은 읽을 때
  `FAILED (PARSE_EXPIRED)` 로 보여주고 재시도를 연다. 90초는 어댑터 최악 소요(30 × 2 + 1 = 61초)보다 길어야 한다 (테스트가 확인한다).
- 테스트는 `conftest.py` 가 파싱 작업을 기본으로 끈다(실제 AI 를 부르지 않게). 흐름 테스트는 가짜 파서를 끼운
  실제 runner 로 바꿔 끼운다 (`tests/script_parse/`).
- 파싱된 `terms`(STT 가 틀리기 쉬운 용어)는 리허설 STT `keyterm` 으로 쓰인다 ([realtime-stt.md](realtime-stt.md)).
