# AI 문서 검사

절 참조·저장소 안의 상대 링크·GitHub 형식의 Markdown 앵커와 PR 본문의 Issue 관계 줄을 검사합니다. Python 3.10 이상과 Git이 필요하며 표준 라이브러리만 사용합니다. 패키지 설치·LLM 호출·네트워크 요청 없이 커밋된 blob을 읽고 checkout·index·브랜치를 변경하지 않습니다. partial clone의 누락 객체도 자동 fetch하지 않고 실행 오류로 처리합니다.

## 실행

저장소 루트에서 해당 실행 환경의 Python을 사용합니다.

```powershell
python ai/tools/docs-check/check.py --base origin/develop --head HEAD --pr-body-file pr-body.md
python -m unittest discover -s ai/tools/docs-check -p "test_*.py"
```

`pr-body.md`는 실행자가 PR 본문을 담아 지정한 로컬 입력입니다. 이 도구가 생성하거나 저장소에 커밋하는 파일은 아닙니다. 실제 PR base를 `--base`에 지정합니다. 스택 PR이면 앞 PR의 브랜치·커밋을 사용합니다.

| 옵션 | 뜻 |
|---|---|
| `--repo` | 검사할 Git 저장소. 기본값은 현재 폴더 |
| `--base` | 현재 PR base의 ref·커밋 (필수) |
| `--head` | PR head의 ref·커밋. 기본값 `HEAD` |
| `--merge-head` | 실제 base와 head를 모두 포함하는 병합 커밋. base가 head의 조상이 아니면 필수 |
| `--pr-body-file` | UTF-8 PR 본문 파일 |
| `--pr-body-env` | PR 본문이 든 환경변수 이름. file 옵션과 둘 중 하나 필수 |

CI에서는 PR 본문을 셸 코드로 보간하지 않고 환경변수로 전달합니다.

```yaml
env:
  PR_BODY: ${{ github.event.pull_request.body }}
  PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}
  PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
  PR_MERGE_SHA: ${{ github.sha }}
```

```bash
python ai/tools/docs-check/check.py --base "$PR_BASE_SHA" --head "$PR_HEAD_SHA" --merge-head "$PR_MERGE_SHA" --pr-body-env PR_BODY
```

워크플로는 BE 담당이 구현합니다. PR의 기본 merge 커밋을 checkout하고 전체 Git 이력을 가져와야 세 커밋을 비교할 수 있습니다. 권장 이벤트는 `opened`·`edited`·`synchronize`·`reopened`이며, 본문 수정에도 다시 검사합니다. 경로 필터와 required check의 건너뛰기 처리는 BE가 함께 정합니다.

## 무엇을 검사하나

- 시작 문서는 `docs/ai/**/*.md`, 루트와 AI의 AGENTS·CLAUDE, AI·research README, 이 검사 도구의 Markdown입니다. 링크 대상의 존재는 저장소 전체에서 확인합니다.
- ATX 제목에서 번호를 찾습니다. `architecture.md`의 1-5처럼 `n-m`은 하위 절이고, `0·1절`은 목록, `0~2절`·`0–2절`은 범위입니다. 파일명 뒤의 번호와 현재 문서의 `n절`을 검사합니다. 기능 문서에서 짧게 부르는 architecture·ai-service와 해당 연구 README도 찾습니다.
- 일반적인 Markdown 링크·링크 정의·이미지 링크를 검사합니다. URL 인코딩된 앵커, 중복 제목의 `-1` 접미사와 명시적인 HTML 앵커를 지원합니다. 폴더 링크는 해당 폴더의 README나 실제 디렉터리를 확인합니다.
- 참조 검색에서는 fenced 코드·들여쓴 코드·인라인 코드 예시·HTML 주석·인용을 제외합니다. 파일명만 있는 인라인 코드와 그 뒤 절 번호는 참조로 확인합니다. 외부 URL은 접속하거나 확인하지 않습니다.
- architecture의 변경 또는 기능 문서 5·12절의 내용 변경에는 PR 본문의 독립적인 `Part of #N`, `Closes #N`, `Fixes #N`, `관련 Issue: #N` 줄이 필요합니다. 코드·주석·인용 속 예시나 일반 문장 속 번호는 인정하지 않습니다. 계약 절의 JSON·코드 예시 변경도 내용 변경입니다.

## 비교와 결과

계약 변경은 **base와 head의 merge-base→head**로 확인합니다. 복수 merge-base가 있는 이력은 기준을 임의로 고르지 않고 실행 오류로 처리합니다. base에만 새로 반영된 변경을 이 PR의 계약 변경으로 세지 않습니다. 참조 오류는 **현재 base→실제 병합 결과**로 확인합니다. head가 base를 포함하면 head를 병합 결과로 사용하고, 포함하지 않으면 호출자가 실제 병합 커밋을 지정해야 합니다. head 외의 병합 사본은 직접 부모가 현재 base·head인 2부모 병합 커밋이어야 하며, 병합 뒤의 수정 커밋으로 대신할 수 없습니다.

기존 오류는 보고하고 신규·증가 오류는 실패로 처리합니다. 바뀌지 않은 문서가 삭제된 대상·앵커·절을 가리키는 경우도 새 오류입니다. 기준본에서 같은 줄에 실제로 보고된 오류인지 확인하므로 코드 예시의 펜스 삭제로 생긴 오류를 다른 기존 오류 삭제로 상계할 수 없습니다. 기존 오류를 이동해 줄 대응이 끊긴 경우에도 보수적으로 신규 오류로 보고할 수 있습니다. 미커밋 파일은 검사 사본에 포함되지 않습니다.

| 종료 코드 | 뜻 |
|---|---|
| 0 | 신규 오류와 필요한 Issue 관계 줄 누락 없음. 기존 오류는 있을 수 있음 |
| 1 | 신규 참조 오류 또는 필요한 Issue 관계 줄 누락 |
| 2 | 입력·Git·파일 읽기 문제로 검사 실행 불가 |

## 사람이 확인할 범위

자유로운 한국어 문장 전체, 복잡한 Markdown 문법·setext 제목·외부 URL은 해석하지 않습니다. 앵커는 Markdown 대상과 명시적인 `<a id="...">`·`<a name="...">`를 확인하며 코드 파일의 GitHub 줄 앵커는 검사하지 않습니다. 검사 통과는 합의 댓글·승인 리뷰·문서와 코드의 일치를 증명하지 않습니다. 기능 상태·담당·변경 절차는 [AI 문서 README](../../../docs/ai/README.md), 공통 계약은 [architecture.md](../../../docs/ai/architecture.md)를 읽고 확인합니다.
