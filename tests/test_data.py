"""Tests dữ liệu: 46 kana, romaji + alias, confusion target (spec §23)."""

from kana_rush.data import BASIC_HIRAGANA_COUNT, DataError, KanaDataset
from kana_rush.models import Kana


def test_load_46_kana_all_have_mnemonics(dataset: KanaDataset) -> None:
    assert len(dataset.kana_list) == BASIC_HIRAGANA_COUNT == 46
    assert len(dataset.by_kana) == 46
    for kana in dataset.by_kana:
        assert dataset.mnemonics.get(kana), f"Thiếu mnemonic {kana}"
        assert dataset.stroke_orders.get(kana), f"Thiếu stroke {kana}"
    assert len(dataset.confusion_pairs) >= 8
    assert len(dataset.words) >= 40
    for word in dataset.words:
        assert word["decomposition"], f"Word thiếu decomposition: {word}"


def test_romaji_ok_accepts_canonical_and_aliases(dataset: KanaDataset) -> None:
    assert dataset.romaji_ok("し", "shi") and dataset.romaji_ok("し", "si")
    assert dataset.romaji_ok("ち", "chi") and dataset.romaji_ok("ち", "ti")
    assert dataset.romaji_ok("つ", "tsu") and dataset.romaji_ok("つ", "tu")
    assert dataset.romaji_ok("ふ", "fu") and dataset.romaji_ok("ふ", "hu")
    assert dataset.romaji_ok("を", "wo") and dataset.romaji_ok("を", "o")
    assert dataset.romaji_ok("ん", "n") and dataset.romaji_ok("ん", "nn")
    assert not dataset.romaji_ok("あ", "ka")
    assert not dataset.romaji_ok("あ", "")


def test_confusion_target_maps_answer_to_other_kana(dataset: KanaDataset) -> None:
    assert dataset.confusion_target("si") == "し"
    assert dataset.confusion_target("tu") == "つ"
    assert dataset.confusion_target("a") == "あ"
    assert dataset.confusion_target("zzzz") is None


def test_alias_unique_and_not_equal_canonical(dataset: KanaDataset) -> None:
    seen: set[str] = set()
    for kana_obj in dataset.kana_list:
        assert isinstance(kana_obj, Kana)
        for alias in kana_obj.aliases:
            assert alias != kana_obj.romaji
            assert alias not in seen
            seen.add(alias)


def test_duplicate_kana_raises_dataerror(tmp_path) -> None:
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    kana_entry = [{"kana": "あ", "romaji": "a", "row": 1, "col": 1}, {"kana": "あ", "romaji": "i", "row": 1, "col": 2}]
    full = {"kana": kana_entry}
    for name, payload in [
        ("hiragana.json", full),
        ("mnemonics_vi.json", {"あ": {"mnemonic": "x", "distinguish": "", "stroke": "1,2"}}),
        ("confusion_pairs.json", {"pairs": [{"a": "ぬ", "b": "め"}]}),
        ("words.json", {"words": []}),
    ]:
        (data_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        KanaDataset(data_dir)
    except DataError:
        return
    raise AssertionError("Phải raise DataError khi trùng kana")
