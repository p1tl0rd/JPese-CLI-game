"""Tests storage: round-trip, backup + phục hồi khi hỏng, SaveCorrupted (spec §23)."""

import json

import pytest

from kana_rush.models import KanaCard, KanaState, SaveData
from kana_rush.storage import SaveCorrupted, Storage, StorageError

from conftest import NOW


def test_round_trip_preserves_fields(tmp_path) -> None:
    storage = Storage(tmp_path)
    save = SaveData(session_id="s1", session_count=3, xp=1200, streak=5, best_streak=9)
    save.day_streak = 4
    save.last_active_date = "2026-08-08"
    card = save.card("あ")
    card.state = KanaState.REVIEW
    card.review_stage = 2
    card.correct_unaided = 7
    card.next_review_at = NOW
    save.confusion_matrix["ぬ"] = {"め": 2}
    save.word_progress["こんにちは"] = [{"correct": True, "rt_ms": 500}]
    save.settings["reverse_mode"] = True
    save.achievements.append("streak_5")
    storage.save(save)

    loaded = storage.load()
    assert loaded.session_id == "s1"
    assert loaded.session_count == 3
    assert loaded.xp == 1200
    assert loaded.streak == 5
    assert loaded.best_streak == 9
    assert loaded.day_streak == 4
    assert loaded.last_active_date == "2026-08-08"
    card2 = loaded.card("あ")
    assert card2.state is KanaState.REVIEW
    assert card2.review_stage == 2
    assert card2.correct_unaided == 7
    assert card2.next_review_at == NOW
    assert loaded.confusion_matrix["ぬ"]["め"] == 2
    assert loaded.word_progress["こんにちは"][0]["correct"] is True
    assert loaded.settings["reverse_mode"] is True
    assert loaded.achievements == ["streak_5"]


def test_backup_created_and_recovery_from_corrupt_main(tmp_path) -> None:
    storage = Storage(tmp_path)
    storage.save(SaveData(session_count=1, xp=100))
    storage.save(SaveData(session_count=1, xp=200))
    assert storage.backup_path().exists()
    with open(storage.progress_path(), "w", encoding="utf-8") as fh:
        fh.write("{không phải json")
    loaded = storage.load()
    assert loaded.xp == 100  # backup giữ phiên bản trước khi ghi save cuối
    assert storage.exists()


def test_both_corrupted_raises_savecorrupted(tmp_path) -> None:
    storage = Storage(tmp_path)
    storage.save(SaveData(session_count=1))
    for path in (storage.progress_path(), storage.backup_path()):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("garbage")
    with pytest.raises(SaveCorrupted):
        storage.load()


def test_load_missing_file_returns_fresh_save(tmp_path) -> None:
    storage = Storage(tmp_path)
    save = storage.load()
    assert save.session_count == 0


def test_save_creates_directories(tmp_path) -> None:
    nested = tmp_path / "a" / "b"
    storage = Storage(nested)
    storage.save(SaveData())
    assert storage.progress_path().exists()


def test_migration_loads_legacy_save_with_missing_keys(tmp_path) -> None:
    storage = Storage(tmp_path)
    storage.save(SaveData(xp=55))
    with open(storage.progress_path(), encoding="utf-8") as fh:
        raw = json.load(fh)
    del raw["schema_version"]
    del raw["best_speedrun_score"]
    del raw["achievements"]
    del raw["day_streak"]
    with open(storage.progress_path(), "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    loaded = storage.load()
    assert loaded.xp == 55
    assert loaded.schema_version == 1
    assert loaded.best_speedrun_score == 0
    assert loaded.achievements == []
    assert loaded.day_streak == 0
