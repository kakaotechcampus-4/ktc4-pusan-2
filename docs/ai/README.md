# docs/ai

피치코치 AI 파트의 기술 문서입니다. 전체 구조와 기능 사이 계약은 `architecture.md`에, 모든 AI 기능이 올라가는 서비스는 `ai-service.md`에, 기능마다의 설계는 `features/`에 있습니다.

## 문서 지도

| 문서 | 담는 것 |
|---|---|
| [architecture.md](architecture.md) | 전체 기능 구성 · 호출 흐름, 기능 사이 입출력 계약, 공통 데이터 모양(1초 원자료 · 1초 판정 결과 · 에러 · `meta`), 시간 예산, DB 저장 지도, 환경변수, 담당 |
| [ai-service.md](ai-service.md) | AI 서비스(`ai/service/`) 하나. 폴더 구조, 기능 · 모델을 붙이는 순서, 공통 모듈(`common/llm.py` · `common/errors.py`), 이미지 · compose · 환경별 구성, CI · 배포, 운영과 문제 해결 |
| [features/](features/) | 기능마다 하나. 목적, 구성, 입력 · 출력 계약(필드 · 예시 · 오류), 처리 흐름, 실패 처리, 배포, DB 저장 상세, 품질 검증 |
| [`ai/research/<기능>/README.md`](../../ai/research/README.md) | 연구 코드의 폴더 · 파일, 실행법, 실험 결과 |

## 기능 지도

이 표가 기능의 담당·현재 상태·활성 코드 위치의 기준입니다. 상태는 활성 연구 폴더와 배포 서비스의 실제 코드로 확인합니다. 아카이브의 과거 실험 코드는 별도로 봅니다.

- **설계만**: 기능 문서는 있지만 활성 연구·서비스 경로에 해당 기능 코드가 없습니다.
- **연구 코드**: 활성 연구 폴더에 코드가 있습니다. 목표 계약 전체 구현이나 실제 발표 데이터 검증 완료를 뜻하지 않습니다.
- **서비스 탑재**: `ai/service/`의 실제 기능이 앱에 연결돼 있습니다. 배포·BE 호출 여부는 별도로 확인합니다.

'활성 코드'의 서비스 경로는 코어가 `ai/service/`에 있다는 뜻입니다. 앱에 연결됐는지는 상태 열로만 봅니다.

| 기능 | 문서 | 담당 | 상태 | 활성 코드 (연구 · 서비스) |
|---|---|---|---|---|
| 실시간 코치 (시간 판정 포함) | [coach-agent.md](features/coach-agent.md) | JWK0395 | 서비스 탑재 (코칭 계획만) | [coach-agent/](../../ai/research/coach-agent/README.md) · 서비스 [`features/coach/`](../../ai/service/src/pitch_coach_ai/features/coach/) |
| 시선 | [gaze-tracking.md](features/gaze-tracking.md) | JWK0395 | 연구 코드 | [gaze-tracking/](../../ai/research/gaze-tracking/README.md) · 서비스 [`features/gaze/`](../../ai/service/src/pitch_coach_ai/features/gaze/) |
| 군더더기 | [filler.md](features/filler.md) | JWK0395 | 연구 코드 | [filler-detection/](../../ai/research/filler-detection/README.md) · 서비스 [`features/filler/`](../../ai/service/src/pitch_coach_ai/features/filler/) |
| 대본 전달도 | [script-coverage-evaluation.md](features/script-coverage-evaluation.md) | JWK0395 | 서비스 탑재 | [script-coverage-evaluation/](../../ai/research/script-coverage-evaluation/README.md) · 서비스 [`features/script_coverage/`](../../ai/service/src/pitch_coach_ai/features/script_coverage/) |
| 대본 파싱 | [script-parser.md](features/script-parser.md) | jinn-ie | 서비스 탑재 | [script-parser/](../../ai/research/script-parser/README.md) · 서비스 [`features/script_parser/`](../../ai/service/src/pitch_coach_ai/features/script_parser/) |
| 평가 기준 분석 | [evaluation-criteria.md](features/evaluation-criteria.md) | jinn-ie | 서비스 탑재 | [evaluation-criteria/](../../ai/research/evaluation-criteria/README.md) · 서비스 [`features/evaluation_criteria/`](../../ai/service/src/pitch_coach_ai/features/evaluation_criteria/) |
| 말 속도 · 멈춤 | [pace.md](features/pace.md) | jinn-ie | 연구 코드 | [speech-pace/](../../ai/research/speech-pace/README.md) · 서비스 [`features/pace/`](../../ai/service/src/pitch_coach_ai/features/pace/) |
| 소리 크기 · 침묵 | [volume.md](features/volume.md) | jinn-ie | 연구 코드 | [volume-analysis/](../../ai/research/volume-analysis/README.md) · 서비스 [`features/volume/`](../../ai/service/src/pitch_coach_ai/features/volume/) |
| 리뷰 에이전트 | [review-agent.md](features/review-agent.md) | jinn-ie | 서비스 탑재 | [review-agent/](../../ai/research/review-agent/README.md) · 서비스 [`features/review/`](../../ai/service/src/pitch_coach_ai/features/review/) |

현재 서비스에는 `/health` · `/ready`와 대본 파싱(`/scripts/parse`) · 대본 전달도(`/scripts/rubric` · `/takes/analyze` · `/takes/rescore`) · 코치(`/coach/evaluate` · `/coach/finalize` · `/coach/plan`) · 평가 기준 분석(`/evaluation-criteria/parse`) · 리뷰(`/takes/review`)가 연결되어 있고, 나머지 기능은 아직 서비스에 탑재되지 않았습니다(BE 호출 · 배포 여부는 별도). 시선 · 군더더기 · 말 속도 · 소리 크기 판정 모듈은 서비스(`ai/service/src/pitch_coach_ai/features/gaze/` · `filler/` · `pace/` · `volume/`)에 있고, 자기 엔드포인트 없이 코치 `evaluate` · `finalize`가 묶어 씁니다(`coach-agent.md` 11절). 공통 서버·LLM 모듈 PR #223·#224는 기능 탑재와 구분합니다. 서비스 담당은 JWK0395입니다. 코치 연구 폴더의 `coach_lab/judges/`는 시험용 대역이므로 실제 판정 모듈로 세지 않습니다.

기능 코드 PR이 merge되면 담당자가 활성 폴더·앱 연결을 확인하고 이 표를 갱신합니다. 나중에 merge되는 README 수정은 먼저 반영된 기능을 보존합니다.

**읽는 순서**: `architecture.md` 0 · 1절(전체 흐름) → 맡은 기능 문서 0 · 3 · 5절(한눈에 보기 · 흐름 · 계약) → 필요한 절. AI 서비스를 띄우거나, 기능을 붙여 배포하거나, AI 서비스를 부르고 운영할 때(BE · 인프라 포함)는 `ai-service.md` 0절의 역할별 표에서 볼 절을 찾습니다.

## 기능 문서의 절

`features/`의 모든 기능 문서는 같은 절 번호를 씁니다. 다른 문서에서 "`coach-agent.md` 5-2"처럼 절 번호로 가리킵니다.

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

이 틀은 기능 문서에 적용합니다. `ai-service.md`는 기능 문서가 아니지만, 다른 문서가 "`ai-service.md` 8절"처럼 절 번호로 가리키므로 지금의 절 번호(0–16)를 바꾸지 않습니다.

문서에 아직 없는 내용은 `> **작성 필요** — <무엇> (#Issue)`로 표시합니다.

## 수정 규칙

수정할 문장·필드·값·호출 시점과 담당을 확인하고 아래 표로 영향 범위를 고른다. 같은 변경이 여러 범위에 걸리면 필요한 절차를 모두 따른다. 합의가 필요한 변경은 사람이 승인한 게시 범위에서 Issue에 바꾸기 전·후와 영향 받는 쪽을 적는다. 결정 댓글과 사람이 확정한 최종 계약 문장을 확인하고 승인된 작업에 반영한다. PR 본문에 그 Issue를 링크해 변경 이유와 합의를 따라갈 수 있게 한다.

| 변경 | 확인할 행동과 대신 할 일 | 이유 |
|---|---|---|
| 기능 안에서 끝남 | FE·BE·다른 AI 기능과 주고받는 필드·값·의미·호출 시점이 같은지 확인한다. 같으면 그 기능 담당이 문서와 필요한 코드를 함께 고치고 일반 PR 리뷰를 받는다 | 내부 수정 때문에 불필요한 연동 합의를 늘리지 않는다 |
| FE·BE와 주고받는 값이 바뀜 | 선택 필드 추가도 포함한다. Issue에 전·후 계약을 적고 그 팀 담당을 @멘션해 동의를 받은 뒤 고친다. PR 리뷰어에 그 팀 담당을 넣는다. 배포 전 부분은 아래 예외를 따른다 | 호환되는 추가도 연동 담당이 알아야 한다 |
| 다른 AI 기능과 주고받는 값이 바뀜 | 공통 계약에 걸리면 architecture 절차를 먼저 따른다. 두 기능 사이만의 계약이면 Issue에 전·후를 적고 변경 작성자 외에 영향을 받는 송신·수신 담당의 동의 뒤 고치며 그 담당을 PR 리뷰어로 넣는다. 두 기능 담당이 같으면 작업 계획에서 전·후를 확인받는다. 배포 전 부분은 아래 예외를 따른다 | 서로 주고받는 처리와 문서를 함께 맞춘다 |
| `architecture.md`가 바뀜 | 필드·값·저장 모양·의미·호출 시점이 바뀌면 크기·배포 여부와 상관없이 AI 팀 회의에서 정한다. JWK0395·jinn-ie의 합의가 Issue 결정 댓글에 있는지 확인하고 고친다. PR 본문에 그 Issue를 링크하고 작성자가 아닌 쪽의 승인 리뷰 뒤 merge한다. FE·BE와 주고받는 값에 걸리면 그 팀과도 합의한다. 이것들을 바꾸지 않는 오탈자·설명 보완은 회의·결정 댓글 없이 일반 PR 리뷰로 고치고, PR 본문의 Issue 링크는 그대로 둔다 | 기능들이 같은 공통 계약을 사용하게 한다 |
| 서비스 문서(`ai-service.md`)가 바뀜 | 폴더·실행법·문제 해결처럼 서비스 안에서 끝나면 서비스 담당 JWK0395가 고친다. 기능 탑재 순서·공통 모듈 사용법처럼 모든 기능 담당이 따르는 것이 바뀌면 다른 기능 담당을 PR 본문에서 @멘션해 알린다. 이 알림 때문에 그 담당을 리뷰어로 넣거나 승인을 기다리지는 않는다. BE·인프라가 하는 일(compose·워크플로·시크릿·헬스체크·AI 주소)이 바뀌면 Issue에서 그 팀과 전·후를 합의하고 그 담당을 PR 리뷰어로 넣는다. 공통 계약도 바뀌면 architecture 절차를 함께 따른다 | 알림과 필수 승인을 구분하고 운영 변경은 담당과 맞춘다 |

**배포 전 부분**: BE가 부르는 부분은 AI 서비스에 붙어 BE가 부르기 전, FE에서 도는 부분은 FE가 화면에 붙이기 전까지다. 변경이 양쪽에 걸치면 각각 확인한다. 배포 전 부분에서 FE·BE 또는 다른 AI 기능과 주고받는 값은 그 기능 담당이 고치고 관련 Issue 댓글이나 PR에서 상대 담당을 @멘션해 알린다. 이미 붙은 부분은 표의 합의 절차를 따른다. `architecture.md`의 필드·값·저장 모양·의미·호출 시점이 바뀌면 표의 AI 팀 회의·결정 댓글·승인 리뷰와 관련 팀 합의를 배포 전에도 생략하지 않는다. 서비스에서 BE·인프라가 해야 할 일에는 이 예외를 적용하지 않는다. `architecture.md` 12절에서 별도로 정한 필수 절차도 유지한다.

- 담당이 다른 문서·코드에서 수정할 곳을 찾으면 직접 고치지 않고 기능 이름·문서 절 번호·전→후·영향을 담은 Issue 초안을 사람에게 넘긴다. 그 담당이 위 절차로 반영하게 하고, 게시가 승인된 범위에서만 Issue를 올린다. 초안 내용 확정과 게시 승인은 구분한다.
- 연구 폴더 README는 해당 기능 담당이 파일 지도·실행법·실험 결과를 고치고 일반 PR 리뷰를 받는다. 담당은 위 기능 표에서 확인한다.
- 기능·서비스 문서와 architecture가 다르면 공통 계약은 architecture를 따른다. 다른 공통 계약을 제안하려면 먼저 회의에서 정하고 결정 댓글을 남긴다. 기능 문서만 고쳐 공통 계약을 바꾸지 않는다.
- 문서 지도·담당·현재 상태는 이 README, 공통 계약은 architecture, 서비스 구성·운영은 ai-service, 기능 계약은 기능 문서 5절, 연구 코드 실행법은 연구 폴더 README에만 둔다. 다른 곳은 경로와 절 번호로 가리켜 한쪽만 갱신되는 일을 막는다.
- 변경 보고·PR 본문에는 전→후, 이유, 영향, 검증 명령·결과와 미확인 범위, 되돌리는 방법, 사람이 판단할 사항(없으면 '없음')을 적는다. architecture 또는 기능 문서 5·12절을 바꾸면 관련 Issue를 링크한다. 검사가 통과해도 필요한 합의 댓글·승인 리뷰는 별도로 확인한다.
