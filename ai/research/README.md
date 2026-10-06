# research

기능별 **실험** 폴더입니다. 비교 실험, 평가, 보고용 노트북이 여기에 있고, **배포하지 않습니다.**

기능을 [archive](../archive/README.md)에서 새 구조로 옮길 때 `research/<feature>/`가 하나씩 생깁니다. 이전 계획은 [../README.md](../README.md)의 이전 현황 표를 보세요.

현재 기능 폴더:

- [script-coverage-evaluation/](script-coverage-evaluation/README.md) — 대본 기준 생성 · STT 전달도 채점 (담당 jewon-kim)

---

## 기능 폴더 구성

```
research/<feature>/
├── README.md          # 담당 · 목적 · 실행법 · 버전별 결과표
├── pyproject.toml     # 실험 의존성 + pitch-coach-ai (service, editable)
├── experiments/       # 비교 실험 .py (# %% 셀로 대화형 실행)
├── tools/             # 로컬 전용: 수집 · 데모 · 재생 · 시뮬레이터
├── data/              # gitignore. 출처 · 해시는 README
├── artifacts/         # gitignore. 가중치 · 모델 파일. manifest만 커밋
├── reports/           # 결과 JSON + 보고용 노트북 (로직 없이 import · 출력만)
└── tests/             # 실험 코드 테스트
```

---

## 규칙

1. **service는 editable 경로 의존성으로 설치합니다.** `pyproject.toml`에 `pitch-coach-ai`(`service/`)를 editable로 넣고, 코어를 import해서 씁니다.
2. **import는 한 방향**입니다. research → service만 허용합니다. service가 research를 import하지 않고, `archive/`도 import하지 않습니다.
3. **노트북은 `reports/`에만** 둡니다. 코어를 import해 결과를 보여 주기만 하고 로직은 넣지 않습니다. 실험 코드는 `experiments/`의 `.py`에 둡니다.
4. **`data/`와 `artifacts/`는 gitignore**입니다. 데이터는 출처와 해시를, 가중치는 manifest를 README에 남겨 다른 사람이 재현할 수 있게 합니다.
5. 배포 코드는 service에서 고치고 여기서는 평가만 합니다. 코어를 research로 복사하지 않습니다.
6. 비밀값(`ai/.env`), 데이터, 가중치는 git에 올리지 않습니다.

기능(모델) 버전은 `service/src/pitch_coach_ai/features/<x>/version.py`에 있습니다. 출력 의미가 바뀌어 버전을 올렸다면, 이 폴더 README의 **버전별 결과표**에 그 버전의 결과를 추가합니다.
