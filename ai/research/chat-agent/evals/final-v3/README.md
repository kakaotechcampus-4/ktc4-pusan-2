# 대화 에이전트 독립 평가 v3 (기능 문서 13-5 · 13-8, Issue #1194)

v1 · v2 는 결과를 본 뒤 고쳐 회귀 세트가 됐다(`../regression-holdout/`). Issue #1194 의 마지막 고침 — 처음 보는 세트에서 늘 실패한 상황을 문항이 아니라 상황 종류 단위로 고침 — 이 처음 보는 문항에도 통하는지 보려고, 고치기 전에 새 문항 40개를 쓰고 동결했다. 구현 세션과 맥락을 나눈 별도 작성 에이전트 둘이 묶음을 나눠 쓰고, 작성자와 다른 보조 검수 모델이 기대값을 다시 계산해 검수했다. 같은 동결본으로 고치기 전(봉인 기준선)과 고친 뒤(최종 한 번)를 짝으로 잰다.

| 파일 | 내용 |
|---|---|
| `RULES.md` | 문항을 쓰기 전에 정한 규칙: 구성 · 작성 · 봉인 경계 · 동결 · 유사도 · 실행 설정 · 짝 비교 · 자체 기준 |
| `TARGET.md` | 문항이 기준으로 삼은 목표 동작(동결 커밋 판의 기능 문서 + v1 · v2 TARGET) |
| `set/` | 문항 40개(`v3-01` ~ `v3-40`, 파일 이름은 내용이 드러나지 않게 번호만) |
| `manifest.json` | 동결 목록(파일별 해시 · 규칙 · 스키마 · 채점기 해시 · 동결 커밋 · 에이전트 판) |
| `REVIEW.md` | 작성 · 보조 모델 검수 기록(동결 때는 개수만, 상세는 최종 실행 뒤) |
| `results/` | 봉인 기준선 · 최종 실행의 결과 해시와 실행 기록, 짝 비교 보고 |

## 봉인 경계
최종 실행이 끝날 때까지 문항 · 기대값 · 상세 검수 기록 · 기준선 결과는 구현 세션이 열지 않는다(`RULES.md` 3절). 이 폴더의 도구(`holdout` · `similarity`)는 문항 글을 출력하지 않는다. 기준선 결과 폴더는 저장소 밖 보관 폴더에 두고, 저장소에는 해시만 남긴다.

## 다시 돌리기 (`ai/research/chat-agent`에서)
```bash
.venv/bin/python -m chat_agent_lab.holdout verify --dir evals/final-v3/set --manifest evals/final-v3/manifest.json --rules evals/final-v3/RULES.md
.venv/bin/python -m chat_agent_lab.evals --dir evals/final-v3/set --set all --judge --workers 4 --search hybrid --out outputs/evals/v3-rerun
```
