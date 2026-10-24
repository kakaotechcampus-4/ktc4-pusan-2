"""대역의 기능 버전. 응답 `meta.feature_version` 과 `criteria_version` 앞부분에 그대로 실린다.

- `<모듈>-<버전>` 꼴로 쓴다(예: `gaze-1.1`, `docs/ai/architecture.md` 5절).
  라우터 · `common/meta.py` 는 접두어를 붙이지 않는다
- 같은 입력에 대한 결과의 뜻이 바뀌면 올린다. 기준값(`config.py`) 조정은 해시가 대신 알린다
"""

#: sample-0.1: 규약 확인용 첫 판. 실제 판정 없음
FEATURE_VERSION = "sample-0.1"
