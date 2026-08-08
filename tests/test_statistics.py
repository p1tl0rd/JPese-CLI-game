"""Tests thống kê: state counts, accuracy, retention ước tính, kana yếu nhất (spec §23)."""

import datetime

from kana_rush.models import KanaCard, KanaState, SaveData
from kana_rush import statistics as stats

from conftest import NOW, push_result


def test_state_counts_and_due() -> None:
    save = SaveData()
    save.cards["あ"] = KanaCard(state=KanaState.REVIEW, review_stage=0, next_review_at=NOW - datetime.timedelta(hours=1))
    save.cards["い"] = KanaCard(state=KanaState.REVIEW, review_stage=2, next_review_at=NOW + datetime.timedelta(days=1))
    save.cards["う"] = KanaCard(state=KanaState.MASTERED, review_stage=6, next_review_at=NOW)
    save.cards["え"] = KanaCard(state=KanaState.RELEARNING, next_review_at=NOW)
    save.cards["お"] = KanaCard(state=KanaState.NEW)
    counts = stats.state_counts(save)
    assert counts[KanaState.REVIEW] == 2
    assert counts[KanaState.MASTERED] == 1
    assert counts[KanaState.RELEARNING] == 1
    assert stats.due_count(save, NOW) == 3


def test_estimated_retention_needs_min_samples() -> None:
    save = SaveData()
    card = save.card("あ")
    card.introduced_at = NOW - datetime.timedelta(days=10)
    for i in range(5):
        push_result(card, correct=True, hinted=False, rt_ms=800, now=NOW - datetime.timedelta(days=9 - i))
    assert stats.estimated_retention(save, 24.0) is None
    for i in range(3):
        push_result(card, correct=True, hinted=False, rt_ms=800, now=NOW - datetime.timedelta(days=3 - i))
    value = stats.estimated_retention(save, 24.0)
    assert value is not None
    assert 0.0 <= value <= 1.0


def test_weakest_and_top_confusions() -> None:
    save = SaveData()
    weak = save.card("あ")
    weak.state = KanaState.REVIEW
    for _ in range(3):
        push_result(weak, correct=False, rt_ms=900)
    strong = save.card("い")
    strong.state = KanaState.REVIEW
    for _ in range(3):
        push_result(strong, correct=True, rt_ms=600)
    save.confusion_matrix["ぬ"] = {"め": 3}
    save.confusion_matrix["ね"] = {"れ": 1}

    weakest = stats.weakest_kanas(save, 2)
    assert weakest[0][0] == "あ"
    top = stats.top_confusions(save, 2)
    assert top[0] == ("ぬ", "め", 3)
    assert stats.total_unaided_corrects(save) == 3
    assert stats.total_hints_used(save) == 0


def test_accuracy_between() -> None:
    save = SaveData()
    card = save.card("あ")
    push_result(card, correct=True, rt_ms=800, now=NOW - datetime.timedelta(days=2))
    push_result(card, correct=False, rt_ms=800, now=NOW)
    acc = stats.accuracy_between(save, NOW - datetime.timedelta(days=1))
    assert acc == 0.0
    acc2 = stats.accuracy_between(save, NOW - datetime.timedelta(days=3))
    assert acc2 == 0.5
