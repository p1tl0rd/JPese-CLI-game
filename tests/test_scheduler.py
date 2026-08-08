"""Tests SRS scheduler: state transitions, stages, measurement sources (spec §23)."""

import datetime

from kana_rush.models import AnswerSource, KanaState, MAX_REVIEW_STAGE, REVIEW_STAGE_INTERVALS
from kana_rush.scheduler import Scheduler

from conftest import NOW


def test_new_correct_to_learning(fresh_save, scheduler: Scheduler) -> None:
    out = scheduler.record_result(
        fresh_save, "あ", correct=True, hinted=False, rt_ms=900,
        session_id="s1", source=AnswerSource.LESSON, now=NOW,
    )
    card = fresh_save.card("あ")
    assert card.state is KanaState.LEARNING
    assert card.learning_step == 1
    assert card.correct_unaided == 1
    assert card.correct_with_hint == 0
    assert out.state_before is KanaState.NEW
    assert out.state_after is KanaState.LEARNING


def test_learning_correct_steps_up_to_cap(fresh_save, scheduler: Scheduler) -> None:
    scheduler.introduce(fresh_save, "い", now=NOW)
    card = fresh_save.card("い")
    assert card.learning_step == 1
    for _ in range(3):
        scheduler.record_result(
            fresh_save, "い", correct=True, hinted=False, rt_ms=900,
            session_id="s1", source=AnswerSource.LESSON, now=NOW,
        )
    assert card.learning_step == 3


def test_wrong_review_to_relearning_then_recover(fresh_save, scheduler: Scheduler) -> None:
    scheduler.introduce(fresh_save, "う", now=NOW)
    scheduler.promote_to_review(fresh_save, "う", stage=4, now=NOW)
    card = fresh_save.card("う")
    out = scheduler.record_result(
        fresh_save, "う", correct=False, hinted=False, rt_ms=1200,
        session_id="s1", source=AnswerSource.REVIEW, now=NOW,
    )
    assert card.state is KanaState.RELEARNING
    assert card.lapse_count == 1
    assert card.review_stage == 2
    assert card.next_review_at == NOW
    assert out.state_after is KanaState.RELEARNING

    out2 = scheduler.record_result(
        fresh_save, "う", correct=True, hinted=False, rt_ms=800,
        session_id="s1", source=AnswerSource.REVIEW, now=NOW,
    )
    assert card.state is KanaState.REVIEW
    assert card.review_stage == 0
    assert out2.state_after is KanaState.REVIEW


def test_measurement_sources_do_not_change_state(fresh_save, scheduler: Scheduler) -> None:
    scheduler.promote_to_review(fresh_save, "え", stage=3, now=NOW)
    out = scheduler.record_result(
        fresh_save, "え", correct=False, hinted=False, rt_ms=900,
        session_id="d1", source=AnswerSource.DIAGNOSTIC, now=NOW,
    )
    card = fresh_save.card("え")
    assert card.state is KanaState.REVIEW
    assert card.wrong_count == 1
    assert out.state_after is KanaState.REVIEW

    out2 = scheduler.record_result(
        fresh_save, "お", correct=True, hinted=False, rt_ms=500,
        session_id="r1", source=AnswerSource.SPEEDRUN, now=NOW,
    )
    assert fresh_save.card("お").state is KanaState.NEW
    assert out2.became_mastered is False


def test_stage_advance_by_speed(fresh_save, scheduler: Scheduler) -> None:
    scheduler.promote_to_review(fresh_save, "か", stage=0, now=NOW)
    card = fresh_save.card("か")
    scheduler.record_result(
        fresh_save, "か", correct=True, hinted=False, rt_ms=500,
        session_id="s1", source=AnswerSource.REVIEW, now=NOW,
    )
    assert card.review_stage == 2
    scheduler.record_result(
        fresh_save, "か", correct=True, hinted=True, rt_ms=3000,
        session_id="s1", source=AnswerSource.REVIEW, now=NOW,
    )
    assert card.review_stage == 2


def test_mastered_correct_keeps_30d_cadence(fresh_save, scheduler: Scheduler) -> None:
    scheduler.promote_to_review(fresh_save, "き", stage=6, now=NOW)
    card = fresh_save.card("き")
    card.state = KanaState.MASTERED
    out = scheduler.record_result(
        fresh_save, "き", correct=True, hinted=False, rt_ms=900,
        session_id="s1", source=AnswerSource.REVIEW, now=NOW,
    )
    assert card.state is KanaState.MASTERED
    assert out.next_review_at == NOW + REVIEW_STAGE_INTERVALS[MAX_REVIEW_STAGE]
