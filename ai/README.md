# ai

**Pitch Coach(피치코치)**의 AI 파트입니다. 발표 연습의 시선·음량·발화·대본을 분석해 코칭 신호와 리포트를 만듭니다.

## 안내와 기능 지도

- 개발·검토를 시작할 때 [AGENTS.md](AGENTS.md)를 읽습니다.
- 기능의 **담당·현재 상태·활성 위치**는 [AI 문서의 기능 지도](../docs/ai/README.md#기능-지도)에서 확인합니다.
- 공통 흐름·입출력 계약은 [architecture.md](../docs/ai/architecture.md), 기능별 계약은 [기술 문서 지도](../docs/ai/README.md#문서-지도), 서비스 실행·운영은 [서비스 README](service/README.md)에 있습니다.

## 폴더

| 폴더 | 용도 |
|---|---|
| [service/](service/README.md) | 배포되는 AI 서버. 실제 기능 탑재 여부는 기능 지도에서 확인 |
| [research/](research/README.md) | 기능별 코드·실험·평가와 실행법 |
| [archive/](archive/README.md) | 구조를 바꾸기 전 코드의 동결본. 새 작업은 research·service에서 진행 |
| [tools/docs-check/](tools/docs-check/README.md) | AI 문서 검사 도구(docs-check: 절 참조·상대 링크·앵커·PR 본문의 Issue 관계 줄) |

## 환경변수

LLM을 쓰는 기능은 호출마다 과금됩니다. 환경변수의 이름·모델 묶음·설정 규칙은 [서비스 README](service/README.md)와 [공통 계약](../docs/ai/architecture.md#10-환경변수와-비밀값)에서 확인합니다. `ai/.env.example`을 참고해 로컬 `ai/.env`를 채우고 비밀값은 출력·커밋하지 않습니다.
