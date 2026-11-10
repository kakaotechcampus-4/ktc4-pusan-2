"""기준표 대응 시험 목차 시험 — README '기준표 대응 시험 목차'의 시험이 실제로 있고 기준표 항목이 빠지지 않았는지 본다.

- 표: 행마다 기준 · 보이는 것 · 시험. 시험 칸은 pytest 시험(`경로::이름`, 연구 `tests/…` · 서비스 `ai/service/tests/…`)과
  화면 시험(`e2e/tests/<파일>.e2e.ts::<시험 제목>`)을 ` · `로 잇는다.
- pytest 시험은 실패 행 대응표 시험(test_failure_map.py)과 같은 방법(ast 로 모은 시험 함수 이름)으로 대조하고, 화면 시험은 그 파일이
  있고 파일 안에 그 제목 글이 있는지 본다(Playwright 를 돌리지 않는다).
- 고칠 때: 기준표 항목을 더하거나 이름을 바꾸면 `REQUIRED`와 README 표를 같이 고친다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from .test_failure_map import _REF, MODULE, README, _cells, all_test_nodes

HEADER = "## 기준표 대응 시험 목차"
_E2E = re.compile(r"`(e2e/tests/[\w.-]+\.e2e\.ts)::([^`]+)`")
#: 기준표에서 빠지면 안 되는 항목(README 표 첫 칸 그대로)
REQUIRED = (
    "⑤ 단계 분기 · 계획",
    "⑤ 단계 분기 — 비교 불가 `NOT_COMPARABLE`",
    "⑤ 단계 분기 — 기본 자료에 없음 `NOT_LOADED`",
    "⑤ 단계 분기 — 리뷰 없음 `REVIEW_MISSING`",
    "⑤ 단계 분기 — 기록 없음 `NO_RECORD`",
    "⑤ 단계 분기 — 도구 실패 `TOOL_ERROR`",
    "⑤ 단계 분기 — 시간 · 호출 부족 `BUDGET_LOW`",
    "⑤ 단계 분기 — 사용자 이의 `USER_DISPUTE`",
    "⑤ 도구 연동",
    "⑤ 상태 관리",
    "⑤ 예외 처리",
    "⑦ 가드레일 — 한도",
    "⑦ 가드레일 — 도구 허용 목록 · 범위",
    "⑦ 가드레일 — 행동 · 승인",
    "⑦ 가드레일 — 허용 근거 · 주입",
    "⑦ 가드레일 — 답 검사",
    "⑦ 가드레일 — 로그 · 개인정보",
    "⑧ 통합 · E2E — 분기",
    "⑧ 통합 · E2E — 계획 변경",
    "⑧ 통합 · E2E — 실패",
    "⑧ 통합 · E2E — 재개",
    "계약 — 새 줄 · 행동 종류 없음",
)


@dataclass(frozen=True)
class Row:
    term: str
    shows: str
    cell: str

    @property
    def tests(self) -> list[str]:
        return [f"{p}::{n}" for p, n in _REF.findall(self.cell)]

    @property
    def e2e(self) -> list[tuple[str, str]]:
        return _E2E.findall(self.cell)


@cache
def rows() -> list[Row]:
    lines = README.read_text(encoding="utf-8").splitlines()
    start = lines.index(HEADER)
    out: list[Row] = []
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        if not ln.startswith("| ") or ln.startswith("| 기준 |"):
            continue
        c = _cells(ln)
        assert len(c) == 3, f"목차 표 칸 수가 3이 아니다: {ln[:40]}"
        out.append(Row(*c))
    return out


def test_every_rubric_term_is_in_the_table_once() -> None:
    terms = [r.term for r in rows()]
    assert len(terms) == len(set(terms)), "같은 기준이 두 번 있다"
    assert set(terms) == set(REQUIRED), {
        "빠진 기준": sorted(set(REQUIRED) - set(terms)),
        "목록에 없는 기준": sorted(set(terms) - set(REQUIRED)),
    }


def test_each_row_lists_two_to_five_tests_and_nothing_else() -> None:
    for r in rows():
        n = len(r.tests) + len(r.e2e)
        assert 2 <= n <= 5, f"{r.term}: 시험 {n}개"
        rest = _E2E.sub("", _REF.sub("", r.cell)).replace("·", "").strip()
        assert rest == "", f"{r.term}: 시험 이름 밖의 글이 있다({rest})"
        assert r.shows, f"{r.term}: '보이는 것'이 비었다"


def test_every_listed_pytest_test_exists() -> None:
    nodes = all_test_nodes()
    missing = [(r.term, t) for r in rows() for t in r.tests if t not in nodes]
    assert missing == []


def test_every_listed_e2e_test_exists_with_its_title() -> None:
    missing = []
    for r in rows():
        for path, title in r.e2e:
            f = MODULE / path
            if not f.is_file() or title not in f.read_text(encoding="utf-8"):
                missing.append((r.term, path, title))
    assert missing == []
    assert any(r.e2e for r in rows() if r.term.startswith("⑧"))
