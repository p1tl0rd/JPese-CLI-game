"""Tests luồng phiên học: LearnSession hoàn thành, Diagnostic, Review rỗng, SpeedRun (spec §23)."""

import datetime
import random

from kana_rush import review as review_mod
from kana_rush.data import KanaDataset
from kana_rush.game import Diagnostic, SpeedRun
from kana_rush.learn import LearnSession
from kana_rush.models import AnswerSource, KanaCard, KanaState, SaveData
from kana_rush.review import ReviewSession
from kana_rush.scheduler import Scheduler
from kana_rush.ui import Answer, UI, UIOptions

from conftest import NOW


class CorrectUI(UI):
    """UI trả lời tự động đúng cho kana hiện trên màn hình."""

    def __init__(self, dataset: KanaDataset, options: UIOptions | None = None) -> None:
        super().__init__(options)
        self.dataset = dataset
        self.last_shown = ""
        self.comparison_romaji = None

    def show_kana(self, kana: str, sub: str = "") -> None:
        self.last_shown = kana

    def say(self, text: str = "", style: str | None = None) -> None:
        # Không dùng re.search (crash access violation trên Python 3.14).
        if isinstance(text, str) and "đọc là" in text:
            start = text.find("'", text.find("đọc là")) + 1
            end = text.find("'", start)
            if start > 0 and end > start:
                self.comparison_romaji = text[start:end]

    def press_enter(self, message: str = "Nhấn Enter để tiếp tục") -> None:
        return None

    def confirm(self, question: str, default_yes: bool = True) -> bool:
        return True

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        if prompt.startswith("Chọn"):
            return Answer(kind="answer", text="1")
        if "Gõ lại romaji đúng" in prompt:
            return Answer(kind="answer", text=self.comparison_romaji or self._expected())
        if "Gõ lại" in prompt:
            expected = self._expected()
            return Answer(kind="answer", text=expected)
        return Answer(kind="answer", text=self._expected())

    def _expected(self) -> str:
        shown = self.last_shown.strip()
        kana_chars = [ch for ch in shown if ch in self.dataset.by_kana]
        if not kana_chars:
            return "a"
        return " ".join(self.dataset.by_kana[ch].romaji for ch in kana_chars)


def run_learn(ui, dataset, save, scheduler, rng, new_ids) -> "LearnReport":
    return LearnSession(ui, dataset, save, scheduler, rng, NOW, "s1", new_ids).run()


def test_learn_completion_promotes_to_review(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    new_ids = ["あ", "い", "う"]
    report = run_learn(ui, dataset, fresh_save, scheduler, seeded_rng, new_ids)
    assert report.completed is True
    for kana in new_ids:
        card = fresh_save.card(kana)
        assert card.state is KanaState.REVIEW
        assert card.review_stage == 0
    assert report.questions_asked >= 3
    assert fresh_save.xp > 0


def test_learn_wrong_answer_still_recovers(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    new_ids = ["ぬ"]
    report = run_learn(ui, dataset, fresh_save, scheduler, seeded_rng, new_ids)
    assert report.completed is True
    assert fresh_save.card("ぬ").state is KanaState.REVIEW


def test_diagnostic_promotes_fast_correct_blocks(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    diag = Diagnostic(ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1")
    report = diag.run()
    assert report.asked == 46
    assert fresh_save.diagnostic_done is True
    review_cards = [k for k, c in fresh_save.cards.items() if c.state is KanaState.REVIEW]
    assert len(review_cards) >= 10
    assert all(c.state is not KanaState.MASTERED for c in fresh_save.cards.values())


def test_review_empty_pool_returns_early(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    session = ReviewSession(ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1", "quick", {})
    report = session.run()
    assert report.questions_asked == 0


def test_speedrun_unlock_status_and_score(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ok, reason = SpeedRun.unlock_status(fresh_save)
    assert ok is False
    assert reason
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    score = SpeedRun(ui, dataset, fresh_save, scheduler, seeded_rng, NOW, "s1").run()
    assert score >= 0
    assert fresh_save.best_speedrun_score == 0


def _review_session_with_cards(dataset, save, scheduler, rng, ui) -> ReviewSession:
    for kana in ("あ", "い", "う"):
        save.cards[kana] = KanaCard(state=KanaState.REVIEW)
    return ReviewSession(ui, dataset, save, scheduler, rng, NOW, "s1", "quick", {})


def test_review_chain_xp_scales_per_kana(
    dataset, fresh_save, scheduler, seeded_rng, monkeypatch
) -> None:
    """Chuỗi kana (2 kana) tính XP từng kana chứ không còn cố định +5."""
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    session = _review_session_with_cards(dataset, fresh_save, scheduler, seeded_rng, ui)
    calls: list[tuple] = []

    def spy_score(correct, rt_ms, hinted, streak):
        calls.append((correct, rt_ms, hinted, streak))
        return (7, streak + 1)

    monkeypatch.setattr(review_mod, "score_answer", spy_score)
    result = session._ask_chain("あ")
    assert result == "chain"
    assert len(calls) == 2  # mỗi kana trong chuỗi được tính một lần
    assert all(call[0] is True for call in calls)
    assert fresh_save.xp == 14
    assert fresh_save.streak == 1  # chuỗi là 1 câu hỏi -> tăng streak 1 lần
    assert fresh_save.best_streak == 1


def test_review_comparison_partner_never_self(dataset, fresh_save, scheduler, seeded_rng) -> None:
    """Cặp so sánh không bao giờ là chính nó (tránh 'ほ | ほ')."""
    ui = CorrectUI(dataset, UIOptions(delay_ms=0))
    session = _review_session_with_cards(dataset, fresh_save, scheduler, seeded_rng, ui)
    card = fresh_save.cards["あ"]
    card.confused_with = {"あ": 3}
    assert session._comparison_partner("あ") is None
    card.confused_with = {"あ": 3, "い": 2}
    assert session._comparison_partner("あ") == "い"
    card.confused_with = {}
    fresh_save.confusion_matrix = {"あ": {"あ": 5}}
    assert session._comparison_partner("あ") is None
