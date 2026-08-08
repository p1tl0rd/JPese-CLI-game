"""Tests QuestionPicker (không lặp liên tiếp, deterministic theo seed) và ưu tiên (spec §23)."""

import datetime
import random

from kana_rush.models import KanaState
from kana_rush.scheduler import PickConfig, QuestionPicker, pick_priority

from conftest import NOW, review_card


def test_picker_excludes_last_asked(fresh_save) -> None:
    for k in "あいうえお":
        fresh_save.cards[k] = review_card(stage=0, due=True)
    picker = QuestionPicker(fresh_save, random.Random(1), NOW)
    pool = ["あ", "い", "う", "え", "お"]
    first = picker.pick(pool, last_asked=None)
    assert first in pool
    for _ in range(10):
        picked = picker.pick(pool, last_asked=first)
        assert picked is None or picked != first


def test_picker_all_blocked_returns_none(fresh_save) -> None:
    for k in "あいう":
        fresh_save.cards[k] = review_card(stage=0, due=True)
    picker = QuestionPicker(fresh_save, random.Random(1), NOW)
    pool = ["あ", "い", "う"]
    picked = picker.pick(pool, last_asked=None, blocked={"あ", "い", "う"})
    assert picked is None


def test_pick_priority_due_beats_quiet() -> None:
    from kana_rush.models import SaveData

    save = SaveData()
    due = save.card("あ")
    due.state = KanaState.REVIEW
    due.review_stage = 0
    due.next_review_at = NOW - datetime.timedelta(hours=5)
    quiet = save.card("い")
    quiet.state = KanaState.REVIEW
    quiet.review_stage = 3
    quiet.next_review_at = NOW + datetime.timedelta(days=1)
    assert pick_priority(due, save, "あ", NOW) > pick_priority(quiet, save, "い", NOW)


def test_picker_deterministic_with_same_seed(fresh_save) -> None:
    for k in "あいうえお":
        fresh_save.cards[k] = review_card(stage=0, due=True)
    config = PickConfig(jitter=20.0)

    def run() -> list[str]:
        picks = []
        picker = QuestionPicker(fresh_save, random.Random(42), NOW, config)
        last = None
        for _ in range(5):
            p = picker.pick(["あ", "い", "う", "え", "お"], last_asked=last)
            if p is None:
                break
            picks.append(p)
            last = p
        return picks

    assert run() == run()
