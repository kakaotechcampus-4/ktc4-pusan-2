# docs/ai

피치코치 AI 파트의 기술 문서입니다. 전체 구조와 기능 사이 계약은 `architecture.md`에, 기능마다의 설계는 `features/`에 있습니다.

## 문서 지도

| 문서 | 담는 것 |
|---|---|
| [architecture.md](architecture.md) | 전체 기능 구성 · 호출 흐름, 기능 사이 입출력 계약, 공통 데이터 모양(1초 원자료 · 1초 판정 결과 · 에러 · `meta`), 시간 예산, DB 저장 지도, 환경변수, 담당 |
| [features/](features/) | 기능마다 하나. 목적, 구성, 입력 · 출력 계약(필드 · 예시 · 오류), 처리 흐름, 실패 처리, 배포, DB 저장 상세, 품질 검증 |
| [`ai/research/<기능>/README.md`](../../ai/research/README.md) | 연구 코드의 폴더 · 파일, 실행법, 실험 결과 |

| 기능 | 문서 | 담당 |
|---|---|---|
| 실시간 코치 (시간 판정 포함) | [coach-agent.md](features/coach-agent.md) | JWK0395 |
| 시선 | [gaze-tracking.md](features/gaze-tracking.md) | JWK0395 |
| 군더더기 | [filler.md](features/filler.md) | JWK0395 |
| 대본 전달도 | [script-coverage-evaluation.md](features/script-coverage-evaluation.md) | JWK0395 |
| AI 서비스 · Docker | [ai-service.md](features/ai-service.md) | JWK0395 |
| 대본 파싱 | [script-parser.md](features/script-parser.md) | jinn-ie |
| 평가 기준 분석 | [evaluation-criteria.md](features/evaluation-criteria.md) | jinn-ie |
| 말 속도 · 멈춤 | [pace.md](features/pace.md) | jinn-ie |
| 소리 크기 · 침묵 | [volume.md](features/volume.md) | jinn-ie |
| 리뷰 에이전트 | [review-agent.md](features/review-agent.md) | jinn-ie |

**읽는 순서**: `architecture.md` 0 · 1절(전체 흐름) → 맡은 기능 문서 0 · 3 · 5절(한눈에 보기 · 흐름 · 계약) → 필요한 절.

## 기능 문서의 절

모든 기능 문서는 같은 절 번호를 씁니다. 다른 문서에서 "`coach-agent.md` 5-2"처럼 절 번호로 가리킵니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 · 2 | 왜 필요한가 · 용어 |
| 3 · 4 | 전체 흐름 · 구성과 폴더 |
| 5 | 입력 · 출력 계약 (호출 목록 · 입력 · 출력 · 문제 목록 · 예시) |
| 6 · 7 · 8 | 처리 · 실패할 때 · 시간 예산 |
| 9 · 10 · 11 | 버전 · 실행과 테스트 · 배포 |
| 12 | FE · BE · DB 연결과 저장 |
| 13 · 14 | 품질 검증 · 지표 정의 |
| 15 · 16 | 열린 질문과 알려진 한계 · 확정 값 한눈에 |

문서에 아직 없는 내용은 `> **작성 필요** — <무엇> (#Issue)`로 표시합니다.

## 수정 규칙

고치기 전에 그 변경이 어디까지 영향을 주는지 보고 절차를 고릅니다. 합의는 Issue에서 하고, 결정은 Issue 댓글로 남기고, PR 본문에서 그 Issue를 링크합니다. 그러면 "왜 이렇게 바뀌었나"를 Issue 하나로 따라갈 수 있습니다.

| 변경 | 예 | 절차 |
|---|---|---|
| **기능 안에서 끝남** | 처리 순서 · 실패 처리 · 기준값 설명 · 실행법. FE · BE · 다른 AI 기능과 주고받는 값은 그대로 | 그 기능 담당이 바로 고칩니다. 일반 PR 리뷰 |
| **FE · BE와 주고받는 값이 바뀜** | 요청 · 응답 필드, 에러 코드, 호출 시점, DB에 저장하는 테이블 · 칸 · 형식 (기능 문서 5 · 12절) | 그 팀과 합의한 뒤 고칩니다. Issue에 바꾸기 전 · 후 계약을 적고 그 팀 담당을 @멘션해 동의를 받습니다. PR 리뷰어에 그 팀 담당을 넣습니다 |
| **`architecture.md`가 바뀜** | 공통 결과 형식 · 에러 · `meta` · 시간 예산 · 호출 흐름 · 저장 지도 · 담당 | AI 팀 회의에서 정합니다. 결정을 Issue 댓글로 남기고, PR은 JWK0395 · jinn-ie 두 사람이 모두 승인한 뒤 merge합니다. FE · BE와 주고받는 값이 걸리면 그 팀과도 합의합니다 |

**배포 전 기능은 빠르게**: 아직 배포되지 않은 기능(AI 서비스에 붙어 BE가 부르기 전)은 기능 문서를 자유롭게 고칩니다. FE · BE와 주고받는 값이 바뀌면 그 팀 담당에게 알립니다. 알릴 때는 관련 Issue 댓글이나 PR에서 @멘션해 기록이 남게 합니다. 단, `architecture.md`는 배포 전이어도 AI 팀 회의를 거칩니다.

- **다른 팀이 고칠 곳을 찾으면** 문서를 직접 고치지 않고 Issue로 남깁니다(기능 이름과 문서 절 번호를 적습니다). 그 기능 담당이 위 절차대로 반영합니다.
- **연구 폴더 README**(`ai/research/<기능>/README.md`)는 실행법 · 실험 결과만 담으므로 그 기능 담당이 고칩니다(일반 PR 리뷰).
- 기능 문서가 `architecture.md`와 다르면 `architecture.md`가 기준입니다. 기능 문서에서 공통 계약을 바꿔야 하면 `architecture.md`를 먼저 바꿉니다.
- 같은 내용은 한 곳에만 씁니다. 공통 계약은 `architecture.md`, 기능 계약은 기능 문서 5절, 실행법은 연구 폴더 README이고, 다른 곳에서는 절 번호로 가리킵니다.
