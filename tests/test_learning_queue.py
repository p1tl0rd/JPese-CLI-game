"""Tests review pool buckets 60/20/10/10, adaptive_new_count, RevisitQueue (spec §23)."""

import datetime
import random

from kana_rush.models import KanaCard, KanaState
from kana_rush.scheduler import RevisitQueue, adaptive_new_count, compose_review_pool

from conftest import NOW, push_result


def test_compose_review_pool_buckets() -> None:
    save = type("S", (), {})()
    from kana_rush.models import SaveData

    save = SaveData()
    due_ids = ["あ", "い", "う", "え", "お", "か"]
    for k in due_ids:
        save.cards[k] = KanaCard(state=KanaState.REVIEW, review_stage=0, next_review_at=NOW)

    weak_ids = ["き", "く"]
    for k in weak_ids:
        card = KanaCard(state=KanaState.REVIEW, review_stage=2, next_review_at=NOW + datetime.timedelta(days=10))
        push_result(card, correct=False, rt_ms=900)
        save.cards[k] = card

    save.confusion_matrix["ぬ"] = {"け": 1}
    save.cards["け"] = KanaCard(state=KanaState.REVIEW, review_stage=1, next_review_at=NOW + datetime.timedelta(days=1))

    for k in ["こ", "さ"]:
        save.cards[k] = KanaCard(state=KanaState.MASTERED, review_stage=6, next_review_at=NOW + datetime.timedelta(days=30))

    rng = random.Random(7)
    pool = compose_review_pool(save, NOW, 10, rng)
    assert len(pool) == 10
    assert set(pool) <= {"あ", "い", "う", "え", "お", "か", "き", "く", "け", "こ", "さ"}
    for k in due_ids:
        assert k in pool
    assert "き" in pool and "く" in pool
    assert "け" in pool


def test_compose_review_pool_excludes_new() -> None:
    from kana_rush.models import SaveData

    save = SaveData()
    save.cards["あ"] = KanaCard(state=KanaState.REVIEW, review_stage=0, next_review_at=NOW)
    save.cards["い"] = KanaCard(state=KanaState.NEW)
    save.cards["う"] = KanaCard(state=KanaState.LEARNING)
    pool = compose_review_pool(save, NOW, 5, random.Random(1))
    assert "い" not in pool
    assert "あ" in pool


def test_adaptive_new_count_tiers() -> None:
    from kana_rush.models import SaveData

    assert adaptive_new_count(SaveData()) == 5

    high = SaveData()
    for _ in range(5):
        push_result(high.card("あ"), correct=True, rt_ms=500)
    assert adaptive_new_count(high) == 7

    low = SaveData()
    for _ in range(4):
        push_result(low.card("あ"), correct=False, rt_ms=500)
    push_result(low.card("あ"), correct=True, rt_ms=500)
    assert adaptive_new_count(low) == 3


def test_revisit_queue_spacing() -> None:
    q = RevisitQueue()
    assert not q.has_pending()
    q.mark_wrong("あ", 0)
    assert q.has_pending()
    assert "あ" in q.blocked(1)
    assert "あ" not in q.blocked(2)
    q.mark_correct("あ")
    assert not q.has_pending()

    q.mark_wrong("い", 0)
    q.mark_wrong("い", 2)
    assert "い" in q.blocked(6)
    assert "い" not in q.blocked(7)

    q.mark_wrong("う", 0)
    q.mark_wrong("う", 2)
    q.mark_wrong("う", 7)
    q.mark_wrong("う", 19)
    assert "う" in q.blocked(999)
    q.mark_correct("う")
    assert "う" not in q.blocked(0)
