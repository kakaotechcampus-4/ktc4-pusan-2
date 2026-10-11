"""가상 데이터를 읽는다: 대본 2개, 연습 18번, 정답 라벨 18개."""

from coverage_lab.datasets import load_script_json, load_take, script_files, take_files
from coverage_lab.paths import LABEL_DIR


def test_two_scripts_sorted_by_slide_number():
    files = script_files()
    assert [p.stem for p in files] == ["가상대본1", "가상대본2"]
    slides = {p.stem: load_script_json(p) for p in files}
    assert [len(v) for v in slides.values()] == [9, 11]
    for items in slides.values():
        numbers = [s.slide_number for s in items]
        assert numbers == sorted(numbers) == list(range(1, len(numbers) + 1))
        assert all(s.script for s in items)


def test_eighteen_takes_with_matching_labels():
    files = take_files()
    assert len(files) == 18
    takes = [load_take(p) for p in files]
    assert {t.script_name for t in takes} == {"가상대본1", "가상대본2"}
    assert [t.take_id for t in takes] == [p.stem for p in files]
    assert all(t.scenario and t.slides for t in takes)
    labels = sorted(p.stem for p in LABEL_DIR.glob("*.json"))
    assert labels == [t.take_id for t in takes]
