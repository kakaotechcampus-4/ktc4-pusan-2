# Pitch / Take 데이터 계약 정리 (#570)

기준: `origin/develop-next` e6c9b365. FE 변경만 포함합니다.

| 이슈 | FE 처리 |
| --- | --- |
| P1~P3 | 정리·저장 응답의 서버 `version`과 `except_standard`를 사용합니다. 저장은 BE 현재 동작대로 새 버전입니다. 기존 저장 버전은 유지합니다. 팀 논의에서 저장 정책이 바뀌면 BE와 함께 변경해야 합니다. |
| P4~P5 | 원문 입력을 2,000자로 제한하고 `STANDARD_INPUT_REJECTED`에 입력 수정 안내를 붙입니다. |
| P6 | `GET /api/pitches/{id}`의 `title`, `time_limit_sec`, `presentation_date`, `lower_deviation`, `upper_deviation`을 읽어 수정 화면에 보존합니다. |
| T1·T3·T4 | 모르는 영역 상태는 판단 불가, 모르는 영역 이름은 원문으로 표시하고 null 미션 사유는 생략합니다. |
| T5 | 기록의 `end_reason`을 홈과 같은 상태 표시 함수에 넘깁니다. 이전 응답에서 누락되면 기존 실패 표시를 유지합니다. |
| T6 | 비교 가능한 완료 Take 중 엔진이 있는 가장 최근 Take를 기준으로 씁니다. 엔진이 없는 Take 자체의 시선은 비교하지 않습니다. |
| T7 | 장치 점검 세션 생성 시각 대신 고정한 종료 시각에서 실제 발표 길이를 빼서 시작 시각을 보냅니다. 늦게 재시도해도 같은 값을 보냅니다. 서버 첫 WS 시각과 완전히 같지는 않으며, 서버 시각 보존 정책은 BE 담당 검토가 필요합니다. |
| T8 | 대본 확인 화면과 GET·POST confirmations를 연결합니다. 재조회·연결 실패·만료 중에도 선택과 전송 상태를 보존합니다. |

## BE 선행 조건과 확인 요청

- P6: BE `feat/be-each-pitch`의 dd6329a8에 정의된 단건 조회 API가 먼저 반영되어야 합니다. 아직 없는 서버에서는 수정 화면 조회가 실패합니다.
- T5: BE `fix/be-add-end-reason`의 기록 응답 변경이 반영되어야 중단을 구분할 수 있습니다.
- T6: 보정 전송 실패 때 개별 Take의 `gaze_engine`이 null인 문제는 남습니다. 종료 시 기록된 엔진 버전을 기록 응답에 제공할 수 있는지 BE 담당 확인이 필요합니다. FE는 엔진이 없는 Take를 추측해 비교하지 않습니다.
- 버전 정책은 현재 서버의 '저장마다 새 버전'을 따릅니다. 정책 합의 없이 서버 동작을 변경하지 않습니다.

## 검증

실행 위치: `output/issue-570/frontend`.

- `npm run typecheck`: 통과.
- `npm run test`: 62개 파일, 621개 테스트 통과.
- `npm run build`: 통과. 기존 큰 번들 경고가 있습니다.
- 변경 파일 `oxlint --max-warnings=0` 및 Prettier 검사: 통과.
- `npm run lint`: 기존 DatePicker.tsx:51, WelcomePage.tsx:493, RehearsalPage.tsx:658의 render 중 Date 호출 경고로 실패합니다.
- 실서버 연결·실기기 카메라·마이크와 BE 선행 변경 배포 이후 동작은 검증하지 않았습니다.
