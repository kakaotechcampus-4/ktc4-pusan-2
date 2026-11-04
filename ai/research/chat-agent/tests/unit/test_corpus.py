"""자료 묶음 검사 · 키워드 검색 시험 (기능 문서 6-10, L1-21 · L4-28 ~ L4-30)."""

from __future__ import annotations

import copy
import json
from importlib import resources
from typing import Any

import pytest

from chat_agent.config import DEFAULT_CONFIG, load_config
from chat_agent.corpus import (
    CorpusInvalid,
    GuideCardData,
    build_corpus,
    manifest_hash,
    sentence_spans,
)


def _read(name: str) -> Any:
    return json.loads(
        resources.files("chat_agent").joinpath("guides", name).read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def raw() -> tuple[dict, dict[str, list[dict]]]:
    manifest = _read("manifest.json")
    return manifest, {name: _read(name) for name in manifest["files"]}


@pytest.fixture(scope="module")
def corpus(raw):
    manifest, files = raw
    return build_corpus(manifest, files, cfg=DEFAULT_CONFIG, allow_unreviewed=True)


def _rebuilt(raw, mutate, *, rehash=True, allow_unreviewed=True, cfg=DEFAULT_CONFIG):
    """카드를 고친 사본으로 묶음을 만든다(rehash 면 해시도 맞춰 다른 검사가 먼저 걸리게)."""
    manifest, files = copy.deepcopy(raw[0]), copy.deepcopy(raw[1])
    mutate(manifest, files)
    if rehash:
        manifest["sha256"] = manifest_hash(
            [c for cards in files.values() for c in cards], manifest.get("synonyms") or None
        )
        # 본문을 바꾼 사본은 새 판이다(같은 판에 다른 본문은 VERSION_REUSED)
        if (
            manifest["corpus_version"] == raw[0]["corpus_version"]
            and manifest["sha256"] != raw[0]["sha256"]
        ):
            manifest["corpus_version"] = "guides-test"
    if allow_unreviewed:
        return build_corpus(manifest, files, cfg=cfg, allow_unreviewed=True)
    # 서비스 모드는 고정표에 등록한 판만 받는다: 시험용 판을 잠시 등록한다
    from chat_agent import corpus as corpus_mod

    added = manifest["corpus_version"] not in corpus_mod.CORPUS_VERSION_HASHES
    if added:
        corpus_mod.CORPUS_VERSION_HASHES[manifest["corpus_version"]] = manifest["sha256"]
    try:
        return build_corpus(manifest, files, cfg=cfg, allow_unreviewed=False)
    finally:
        if added:
            del corpus_mod.CORPUS_VERSION_HASHES[manifest["corpus_version"]]


# ── 실제 파일 ─────────────────────────────────────────────────────


def test_real_files_load_and_counts(corpus):
    kinds = [c.kind for c in corpus.cards]
    assert kinds.count("method") == 20
    assert kinds.count("usage") == 25
    assert corpus.version == "guides-4"
    assert all(c.license == "team" for c in corpus.cards)


def test_manifest_hash_matches_stored(raw):
    manifest, files = raw
    assert manifest["sha256"] == manifest_hash(
        [c for cards in files.values() for c in cards], manifest.get("synonyms") or None
    )


def test_method_cards_are_short_and_quotable(corpus):
    for c in corpus.cards:
        spans = sentence_spans(c.text)
        assert all(e - s <= 300 for s, e in spans)
        if c.kind == "method":
            assert 200 <= len(c.text) <= 600, c.source_id
            assert 3 <= len(spans) <= 6, c.source_id
        else:
            assert c.screen


def test_cause_and_myth_cards_exist(corpus, raw):
    # 검수 전(PENDING)에는 원인 근거로 인정하지 않고, 검수 완료(APPROVED)면 인정한다
    assert not corpus.is_approved_cause_source("g-19")

    def approve(_m, files):
        for cards in files.values():
            for c in cards:
                c["review"] = "APPROVED"

    assert _rebuilt(raw, approve, allow_unreviewed=False).is_approved_cause_source("g-19")
    assert not corpus.is_approved_cause_source("g-01")
    assert not corpus.is_approved_cause_source("g-99")
    assert "단정" in corpus.get("g-19").title
    assert "빠를수록" in corpus.get("g-18").title


def test_get_and_text_of(corpus):
    assert corpus.get("g-01").source_id == "g-01"
    assert corpus.get("zzz") is None
    assert corpus.text_of("g-01") == corpus.get("g-01").text
    assert corpus.text_of("g-fake") is None


# ── 검사 실패 (L1-21 · L4-29 · L4-30) ─────────────────────────────


def test_fm_L1_21_duplicate_source_id(raw):
    def mut(_m, files):
        files["usage.json"][0]["source_id"] = "g-01"
        files["usage.json"][0]["kind"] = "method"
        files["usage.json"][0]["screen"] = None

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut)
    assert e.value.reason_code == "DUPLICATE_ID"


def test_fm_L1_21_broken_card_schema(raw):
    def mut(_m, files):
        del files["methods.json"][0]["text"]

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut)
    assert e.value.reason_code == "SCHEMA"


def test_fm_L1_21_sentence_over_300_chars_rejected(raw):
    def mut(_m, files):
        files["methods.json"][0]["text"] = "가" * 301 + "."

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut)
    assert e.value.reason_code == "SCHEMA"


def test_fm_L4_29_forbidden_license(raw):
    def mut(_m, files):
        files["methods.json"][0]["license"] = "CC BY-NC-SA 4.0"

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut)
    assert e.value.reason_code == "LICENSE_FORBIDDEN"


def test_fm_L4_29_unreviewed_rejected_for_service(raw):
    manifest, files = raw
    with pytest.raises(CorpusInvalid) as e:
        build_corpus(manifest, files, cfg=DEFAULT_CONFIG, allow_unreviewed=False)
    assert e.value.reason_code == "UNREVIEWED"


def test_fm_L4_29_approved_cards_pass_for_service(raw):
    def mut(_m, files):
        for cards in files.values():
            for c in cards:
                c["review"] = "APPROVED"

    corpus = _rebuilt(raw, mut, allow_unreviewed=False)
    assert len(corpus) == 45


def test_fm_L4_29_license_follows_config(raw):
    cfg = load_config(allowed_licenses=("team", "CC BY 4.0"))

    def mut(_m, files):
        files["methods.json"][0]["license"] = "CC BY 4.0"

    assert len(_rebuilt(raw, mut, cfg=cfg)) == 45


def test_fm_L4_30_missing_file(raw):
    manifest, files = raw
    with pytest.raises(CorpusInvalid) as e:
        build_corpus(manifest, {"methods.json": files["methods.json"]}, allow_unreviewed=True)
    assert e.value.reason_code == "FILE_MISSING"


def test_fm_L4_30_unlisted_file(raw):
    manifest, files = raw
    with pytest.raises(CorpusInvalid) as e:
        build_corpus(manifest, {**files, "extra.json": []}, allow_unreviewed=True)
    assert e.value.reason_code == "FILE_UNLISTED"


def test_fm_L4_30_body_changed_hash_mismatch(raw):
    def mut(_m, files):
        files["methods.json"][0]["text"] += " 한 문장이 몰래 더해졌습니다."

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut, rehash=False)
    assert e.value.reason_code == "HASH_MISMATCH"


def test_fm_L4_30_bad_manifest(raw):
    manifest, files = raw
    with pytest.raises(CorpusInvalid) as e:
        build_corpus({**manifest, "sha256": "nothex"}, files, allow_unreviewed=True)
    assert e.value.reason_code == "MANIFEST_INVALID"


def test_card_model_forbids_extra_and_checks_kind():
    base = {
        "source_id": "g-90",
        "kind": "method",
        "title": "제목",
        "text": "문장 하나입니다.",
        "source": "팀 작성",
        "license": "team",
        "review": "PENDING",
        "topics": ["주제"],
    }
    assert GuideCardData.model_validate(base)
    with pytest.raises(ValueError):
        GuideCardData.model_validate({**base, "extra": 1})
    with pytest.raises(ValueError):
        GuideCardData.model_validate({**base, "kind": "usage"})  # screen 없음 · 접두사 불일치


# ── 검색 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query,expected",
    [
        ("긴장하면 말이 빨라져", "g-07"),
        ("코칭 모드랑 실전 모드 차이", "u-16"),
        ("평가 기준 어떻게 써?", "u-06"),
        ("마이크가 안 잡혀", "u-12"),
        ("대본 의존 줄이기", "g-02"),
    ],
)
def test_fm_L1_21_korean_queries_return_expected_card_first(corpus, query, expected):
    hits = corpus.search(query)
    assert hits, query
    assert hits[0].card.source_id == expected, [h.card.source_id for h in hits]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("발표는 빠를수록 좋은가요", "g-18"),
        ("말이 왜 빨라졌는지 원인이 뭐야", "g-19"),
        ("군더더기 말을 줄이는 법", "g-05"),
        ("질의응답 대비는 어떻게 해", "g-13"),
        ("시선 기준이 안 잡혀요", "u-13"),
        ("Take 비교는 어디서 봐", "u-23"),
        ("대화 코치가 점수를 바꿔 줄 수 있어?", "u-25"),
    ],
)
def test_fm_L4_28_more_queries(corpus, query, expected):
    hits = corpus.search(query)
    assert hits and hits[0].card.source_id == expected, [h.card.source_id for h in hits]


def test_fm_L4_28_no_result_for_unrelated_query(corpus):
    assert corpus.search("오늘 저녁 메뉴 추천해 줘") == []
    assert corpus.search("zzzzqqq") == []


def test_fm_L4_28_limit_and_order_deterministic(corpus):
    a = corpus.search("발표 연습 말 속도 시선 대본")
    b = corpus.search("발표 연습 말 속도 시선 대본")
    assert a == b
    assert len(a) <= DEFAULT_CONFIG.max_search_results
    keys = [(-h.score, h.card.source_id) for h in a]
    assert keys == sorted(keys)
    assert len(corpus.search("발표 연습 말 속도 시선 대본", limit=2)) <= 2
    assert len(corpus.search("발표 연습 말 속도 시선 대본", limit=50)) <= 5


def test_screen_boost_prefers_matching_screen(corpus):
    base = [h.card.source_id for h in corpus.search("대본 형식")]
    boosted = corpus.search("대본 형식", screen="pitch_create:script")
    assert boosted[0].card.source_id.startswith("u-")
    assert "u-03" in [h.card.source_id for h in boosted]
    assert base  # 화면 없이도 걸린다


def test_query_boundaries(corpus):
    ok = ("발표 " * 100)[:200]
    assert len(ok) == 200
    corpus.search(ok)
    with pytest.raises(ValueError):
        corpus.search(ok + "가")
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            corpus.search(bad)


def test_quote_is_verbatim_substring_and_fits(corpus):
    for query in (
        "긴장하면 말이 빨라져",
        "마이크가 안 잡혀",
        "코칭 모드랑 실전 모드 차이",
        "원인이 뭐야",
    ):
        for hit in corpus.search(query):
            text = corpus.text_of(hit.card.source_id)
            assert hit.card.quote in text
            assert 0 < len(hit.card.quote) <= 300
            assert hit.card.license == "team"
            assert hit.card.source


def test_quote_picks_best_matching_sentence(corpus):
    hit = corpus.search("긴장하면 말이 빨라져")[0]
    assert "빨라" in hit.card.quote


def test_fm_L4_30_same_version_with_new_text_is_rejected(raw):
    """판 → 해시 고정표(version.CORPUS_VERSION_HASHES)에 있는 판에 다른 본문이 들어오면 거절한다."""

    def mut(_m, files):
        files["methods.json"][0]["text"] = files["methods.json"][0]["text"] + " 덧붙인 문장입니다."

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt_same_version(raw, mut)
    assert e.value.reason_code == "VERSION_REUSED"


def _rebuilt_same_version(raw, mutate):
    manifest, files = copy.deepcopy(raw[0]), copy.deepcopy(raw[1])
    mutate(manifest, files)
    manifest["sha256"] = manifest_hash(
        [c for cards in files.values() for c in cards], manifest.get("synonyms") or None
    )
    return build_corpus(manifest, files, allow_unreviewed=True)


@pytest.mark.parametrize("bad", [None, "카드", 3])
def test_fm_L1_21_non_object_card_is_schema_error(raw, bad):
    def mut(_m, files):
        files["usage.json"].append(bad)

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut, rehash=False)
    assert e.value.reason_code == "SCHEMA"


def test_fm_L4_29_blank_source_is_rejected(raw):
    def mut(_m, files):
        files["methods.json"][0]["source"] = "   "

    with pytest.raises(CorpusInvalid) as e:
        _rebuilt(raw, mut)
    assert e.value.reason_code == "SCHEMA"


def test_screen_match_needs_colon_boundary():
    from chat_agent.corpus import _screen_match

    assert _screen_match("pitch_create", "pitch_create:slides")
    assert _screen_match("pitch_create:slides", "pitch_create")
    assert not _screen_match("report", "reports")
    assert not _screen_match("pitch_create:slides", "pitch_create:slide")


def test_fm_L4_30_service_mode_rejects_unregistered_version(raw):
    """서비스 모드(검수 완료 카드만)는 판 → 해시 고정표에 없는 판을 거절한다."""
    manifest, files = copy.deepcopy(raw[0]), copy.deepcopy(raw[1])
    for cards in files.values():
        for c in cards:
            c["review"] = "APPROVED"
    manifest["sha256"] = manifest_hash(
        [c for cards in files.values() for c in cards], manifest.get("synonyms") or None
    )
    manifest["corpus_version"] = "guides-unregistered"
    with pytest.raises(CorpusInvalid) as e:
        build_corpus(manifest, files, allow_unreviewed=False)
    assert e.value.reason_code == "UNKNOWN_VERSION"


# ── 동의어 사전 (6-10) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("원고만 보게 돼요", "g-02"),  # 원고 → 대표어 대본
        ("클로징은 어떻게 해", "g-10"),  # 클로징 → 마무리
        ("무대 공포가 심해요", "g-07"),  # 무대 공포 → 긴장
        ("필러가 많대요", "g-05"),  # 필러 → 군더더기
    ],
)
def test_synonym_dictionary_finds_cards_the_query_words_miss(raw, corpus, query, expected):
    """카드가 쓰지 않는 말로 물어도 대표어로 찾는다(사전을 빼면 상위 3장에 없다)."""
    plain = _rebuilt(raw, lambda m, f: m.pop("synonyms"))
    assert expected in [h.card.source_id for h in corpus.search(query)[:3]]
    assert expected not in [h.card.source_id for h in plain.search(query)[:3]]


def test_synonyms_expand_one_way_only(corpus):
    """대표어를 쓴 질문은 다른 말로 넓히지 않는다: "시선 기준" 질문에서 눈맞춤 카드가 시선 기준 카드를 앞지르지 않는다."""
    plain = [h.card.source_id for h in corpus.search("시선 기준이 안 잡혀요")]
    assert plain[0] == "u-13"
    with_variant = [h.card.source_id for h in corpus.search("눈맞춤이 어려워요")]
    assert with_variant[0] == "g-01" and "u-13" in with_variant  # 눈맞춤 → 대표어 시선


def test_synonyms_are_part_of_the_bundle_hash(raw):
    manifest, files = raw
    cards = [c for cs in files.values() for c in cs]
    assert manifest_hash(cards, manifest["synonyms"]) != manifest_hash(cards)
    changed = [*manifest["synonyms"], ["새말", "다른말"]]
    assert manifest_hash(cards, changed) != manifest["sha256"]


@pytest.mark.parametrize("groups", [[["외톨이"]], [["시선", " "]], [["쉼", "멈춤"]]])
def test_bad_synonym_group_is_rejected(raw, groups):
    """묶음은 빈 말 없이 2개 이상, 대표어(첫 말)는 찾을 수 있는 두 글자 이상 낱말이 있어야 한다."""
    with pytest.raises(CorpusInvalid):
        _rebuilt(raw, lambda m, f: m.update(synonyms=groups))
