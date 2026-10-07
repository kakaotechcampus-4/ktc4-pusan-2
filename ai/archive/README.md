# archive

구조를 바꾸기 전 `ai/workspaces/`를 그대로 옮겨 둔 폴더입니다.
여기 있는 코드는 수정하지 않고, 다른 코드에서 import하지 않습니다. 고칠 것이 생기면 그 기능을 `research/`로 옮겨서 고칩니다.

| 기능 | 경로 | 담당 | 비고 |
|---|---|---|---|
| 시선 | `workspaces/jewon-kim/gaze-tracking/` | jewon-kim | [`research/gaze-tracking/`](../research/gaze-tracking/README.md)로 옮김 |
| 실시간 코치 | `workspaces/jewon-kim/coach-agent/` | jewon-kim | |
| 대본 전달도 | `workspaces/jewon-kim/script-coverage-evaluation/` | jewon-kim | [`research/script-coverage-evaluation/`](../research/script-coverage-evaluation/README.md)로 옮김 |
| 대본 파싱 | `workspaces/seojin-lee/script-parser/` | seojin-lee | |
| 평가 기준 분류 | `workspaces/seojin-lee/evaluation-criteria/` | seojin-lee | |
| 말 속도 | `workspaces/seojin-lee/stt-live/` | seojin-lee | |
| 소리 크기 | `workspaces/seojin-lee/volume-analysis/` | seojin-lee | |

## 실행

각 프로젝트 README의 명령이 그대로 동작합니다. 다만:

- `.venv` · `node_modules` · 모델 파일 · 데이터 · 캐시는 git에 없으니, 각 README의 준비 단계대로 다시 만듭니다.
- 문서 안의 `ai/workspaces/...` 경로는 `ai/archive/workspaces/...`로 읽습니다.
- 노트북은 `ai/.env`를 읽어 실제 LLM API를 부릅니다 (과금).
