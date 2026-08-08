"""Tests Word Bridge: mở khóa theo kana đã học, pick, ghi nhận kết quả (spec §23)."""

import random

import pytest

from kana_rush.models import KanaCard, KanaState
from kana_rush.scheduler import Scheduler
from kana_rush.words import (
    NoWordsAvailable,
    available_words,
    pick_words,
    record_word_result,
    word_accuracy,
    word_unlocked,
)

from conftest import NOW


def word_kana(dataset, index: int) -> str:
    return dataset.words[index]["kana"]


def test_word_unlocked_requires_all_kana_introduced(dataset, fresh_save, scheduler: Scheduler) -> None:
    word = dataset.words[0]
    assert word_unlocked(fresh_save, word) is False
    for ch in word["decomposition"]:
        scheduler.introduce(fresh_save, ch, now=NOW)
    assert word_unlocked(fresh_save, word) is True


def test_available_words_and_pick_raises_with_nothing(dataset, fresh_save, scheduler: Scheduler) -> None:
    assert available_words(fresh_save, dataset) == []
    with pytest.raises(NoWordsAvailable):
        pick_words(fresh_save, dataset, 3, random.Random(1))
    word = dataset.words[0]
    for ch in word["decomposition"]:
        scheduler.introduce(fresh_save, ch, now=NOW)
    picked = pick_words(fresh_save, dataset, 2, random.Random(1))
    assert 0 < len(picked) <= 2
    assert word in picked
    assert set(w["kana"] for w in picked) <= set(w["kana"] for w in available_words(fresh_save, dataset))


def test_word_result_and_accuracy(dataset, fresh_save) -> None:
    word = dataset.words[1]
    record_word_result(fresh_save, word, correct=True, rt_ms=800, session_id="s1")
    record_word_result(fresh_save, word, correct=True, rt_ms=900, session_id="s1")
    record_word_result(fresh_save, word, correct=False, rt_ms=700, session_id="s1")
    record_word_result(fresh_save, word, correct=True, rt_ms=850, session_id="s1")
    assert word_accuracy(fresh_save, word["kana"]) == pytest.approx(0.75)
    assert len(fresh_save.word_progress[word["kana"]]) == 4
