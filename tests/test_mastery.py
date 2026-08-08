"""Tests điều kiện MASTERED: ≥6 recall tự do, ≥3 session, delayed recall 24h,
accuracy ≥85%, median RT ≤2s, kết quả cuối đúng, không lỗi chưa phục hồi (spec §23)."""

import datetime

from kana_rush.models import AnswerSource, KanaCard, KanaState, MAX_REVIEW_STAGE, REVIEW_STAGE_INTERVALS
from kana_rush.scheduler import Scheduler, mastery_eligible

from conftest import NOW, UTC, push_result

DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)


def build_eligible_card() -> tuple[KanaCard, datetime.datetime]:
    introduced = NOW - datetime.timedelta(days=3)
    card = KanaCard(state=KanaState.REVIEW, review_stage=2)
    card.introduced_at = introduced
    for i in range(6):
        push_result(
            card, correct=True, hinted=False, rt_ms=800,
            session_id=f"sid{i % 3}", now=introduced + 25 * HOUR + i * datetime.timedelta(minutes=10),
        )
    for i in range(4):
        push_result(card, correct=True, hinted=False, rt_ms=900, session_id="sid0", now=NOW - i * HOUR)
    return card, NOW


def test_mastery_eligible_all_conditions() -> None:
    card, now = build_eligible_card()
    assert card.correct_unaided >= 6
    assert len(card.session_ids_correct) >= 3
    assert card.recent_accuracy(10) >= 0.85
    assert mastery_eligible(card, now)


def test_mastery_eligible_fails_on_low_recent_accuracy() -> None:
    card, now = build_eligible_card()
    push_result(card, correct=False, hinted=False, rt_ms=800, now=now - datetime.timedelta(minutes=2))
    push_result(card, correct=False, hinted=False, rt_ms=800, now=now)
    assert card.recent_accuracy(10) < 0.85
    assert not mastery_eligible(card, now)


def test_mastery_eligible_fails_without_delayed_recall() -> None:
    introduced = NOW - datetime.timedelta(days=3)
    card = KanaCard(state=KanaState.REVIEW, review_stage=2)
    card.introduced_at = introduced
    for i in range(8):
        push_result(
            card, correct=True, hinted=False, rt_ms=800,
            session_id=f"sid{i % 3}", now=introduced + 2 * HOUR + i * datetime.timedelta(minutes=5),
        )
    assert not mastery_eligible(card, NOW)


def test_mastery_eligible_fails_on_unrecovered_error() -> None:
    card, now = build_eligible_card()
    push_result(card, correct=False, hinted=False, rt_ms=800, now=now - datetime.timedelta(minutes=1))
    assert not mastery_eligible(card, now)


def test_review_correct_becomes_mastered(fresh_save, scheduler: Scheduler) -> None:
    card, now = build_eligible_card()
    fresh_save.cards["け"] = card
    out = scheduler.record_result(
        fresh_save, "け", correct=True, hinted=False, rt_ms=800,
        session_id="sidX", source=AnswerSource.REVIEW, now=now,
    )
    assert out.became_mastered
    assert card.state is KanaState.MASTERED
    assert out.next_review_at == now + REVIEW_STAGE_INTERVALS[MAX_REVIEW_STAGE]
