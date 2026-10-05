# archive

구조 개편 **이전**의 `ai/workspaces/`를 내용 변경 없이 그대로 옮겨 둔 폴더입니다.

- 옮기기 전 원본 상태는 git 태그 **`ai-workspaces-final`** 에 있습니다.
- 폴더 안의 상대 경로는 그대로이므로, 각 프로젝트 안에서는 예전처럼 동작합니다.
- 새 구조(`service/`, `research/`)는 [../README.md](../README.md)를 보세요.

---

## 동결 규칙

- **수정하지 않습니다.**
- **어디서도 import하지 않습니다.** service와 research 모두 `archive/`를 참조하지 않습니다.
- 여기 있는 코드에서 고칠 것이 생기면, 먼저 **새 구조로 이전**한 뒤 그곳에서 고칩니다.

---

## 실행하는 법

각 프로젝트 README의 명령은 그대로 동작합니다. 단, 아래 두 가지를 알아야 합니다.

1. **gitignore된 로컬 파일은 옮겨지지 않았습니다.** `.venv`, `node_modules`, 모델 파일, 데이터, 캐시가 여기에 해당합니다. 각 README의 셋업 단계를 따라 다시 만들어야 합니다.
2. 예전 문서 안에 `ai/workspaces/...` 로 적힌 경로는 이제 `ai/archive/workspaces/...` 를 뜻합니다.

노트북이 읽는 `.env`는 현재 디렉터리부터 위로 올라가며 처음 만나는 파일이라, `ai/.env`를 그대로 찾습니다. `ai/.env.example`을 `ai/.env`로 복사해 두세요. 실제 LLM API가 호출되어 과금될 수 있습니다.

---

## 목차

경로는 `archive/workspaces/` 기준입니다.

| 경로 | 담당 | 설명 |
|---|---|---|
| `jewon-kim/gaze-tracking/` | jewon-kim | 웹캠으로 발표자가 카메라 · 화면 · 대본 · 기타 중 어디를 보는지 판정. v1.1 head-pose 엔진, TS 브라우저 엔진 포함 |
| `jewon-kim/coach-agent/` | jewon-kim | 매초 말할지 · 무엇을 말할지 정하는 규칙 기반 실시간 코치 |
| `jewon-kim/script-coverage-evaluation/` | jewon-kim | 대본을 슬라이드별 기준으로 만들고 STT가 얼마나 전달했는지 채점. 노트북 |
| `seojin-lee/script-parser/` | seojin-lee | 대본을 슬라이드별로 나누고 키워드 추출. 노트북 |
| `seojin-lee/evaluation-criteria/` | seojin-lee | 자유 서술 평가 기준을 판정 가능 · 정보 부족 · 판정 불가로 분류. 노트북 |
| `seojin-lee/stt-live/` | seojin-lee | STT 단어 타임스탬프로 말 속도(CPM)를 계산해 느림 · 보통 · 빠름 판정. 노트북 |
| `seojin-lee/volume-analysis/` | seojin-lee | 캘리브레이션 대비 소리 크기를 작음 · 보통 · 큼으로 판정. 노트북 |

이전 목적지와 진행 상태는 [../README.md](../README.md)의 이전 현황 표에 있습니다.

---

## 언제 삭제하나

모든 기능을 새 구조로 이전하고 **첫 배포가 나간 뒤** 삭제합니다.

삭제 후에도 필요하면 태그에서 복원할 수 있습니다.

```bash
git checkout ai-workspaces-final -- ai/workspaces/<path>
```
