from script_coverage.shared.text import compact, normalize_script, overlaps


def test_normalize_script_quotes_and_spaces():
    norm = normalize_script("‘재고 예보’입니다.\n\n  30퍼센트 남았다면")
    assert norm.text == "'재고 예보'입니다. 30퍼센트 남았다면"


def test_normalize_script_sentences_cover_text():
    norm = normalize_script("첫 문장입니다. 두 번째 문장입니다.")
    assert [s.index for s in norm.sentences] == [0, 1]
    assert all(norm.text[s.start : s.end] == s.text for s in norm.sentences)
    assert len(norm.content_hash) == 64


def test_compact_drops_spaces_and_punctuation():
    text, index = compact("A b,가!")
    assert text == "ab가"
    assert index == [0, 2, 4]


def test_overlaps():
    assert overlaps((0, 5), (4, 8))
    assert not overlaps((0, 5), (5, 8))
