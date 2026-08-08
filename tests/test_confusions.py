"""Tests confusion pairs: seed 8 cặp + ma trận nhầm lẫn khi trả lời sai (spec §23)."""

from kana_rush.models import AnswerSource, KanaState
from kana_rush.scheduler import Scheduler, confusion_count

from conftest import NOW


def test_seed_confusion_pairs_load(dataset) -> None:
    pairs = {frozenset(p) for p in dataset.confusion_pairs}
    expected = {
        frozenset(("ぬ", "め")),
        frozenset(("ね", "れ")),
        frozenset(("る", "ろ")),
        frozenset(("さ", "ち")),
        frozenset(("き", "さ")),
        frozenset(("は", "ほ")),
        frozenset(("わ", "れ")),
        frozenset(("あ", "お")),
    }
    assert len(dataset.confusion_pairs) == 8
    assert pairs == expected


def test_confusion_matrix_recorded(fresh_save, scheduler: Scheduler) -> None:
    scheduler.promote_to_review(fresh_save, "さ", stage=2, now=NOW)
    for _ in range(2):
        scheduler.record_result(
            fresh_save, "さ", correct=False, hinted=False, rt_ms=800,
            session_id="s1", source=AnswerSource.REVIEW, confusion="き", now=NOW,
        )
    assert fresh_save.confusion_matrix["さ"]["き"] == 2
    assert fresh_save.card("さ").confused_with["き"] == 2
    assert confusion_count(fresh_save, "さ") >= 2
    assert confusion_count(fresh_save, "き") >= 1


def test_confusion_flow_via_dataset(dataset, fresh_save, scheduler: Scheduler) -> None:
    scheduler.promote_to_review(fresh_save, "き", stage=1, now=NOW)
    target = dataset.confusion_target("sa")
    assert target == "さ"
    scheduler.record_result(
        fresh_save, "き", correct=False, hinted=False, rt_ms=1000,
        session_id="s1", source=AnswerSource.REVIEW, confusion=target, now=NOW,
    )
    assert fresh_save.confusion_matrix["き"]["さ"] == 1
    assert fresh_save.card("き").state is KanaState.RELEARNING
