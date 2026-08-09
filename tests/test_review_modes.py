"""Tests các chế độ Review mới: lesson / multi / random / smart / all / srs
(deck-based) - pool, số câu, nguồn cập nhật SRS, tiến độ lesson."""

import datetime
import random
from collections import Counter

import pytest

from kana_rush.data import KanaDataset
from kana_rush.lessons import LessonDataset, load_lessons
from kana_rush.models import AnswerSource, KanaCard, KanaState, LessonProgress, SaveData
from kana_rush.review import ReviewSession
from kana_rush.scheduler import Scheduler
from kana_rush.ui import Answer, UI, UIOptions

from conftest import NOW

UTC = datetime.timezone.utc
DAY = datetime.timedelta(days=1)


class RecordingUI(UI):
    """UI trả lời đúng và ghi lại kana được hiển thị."""

    def __init__(self, dataset: KanaDataset, options: UIOptions | None = None) -> None:
        super().__init__(options)
        self.dataset = dataset
        self.last_shown = ""
        self.shown: list[str] = []
        self.comparison_romaji = None

    def show_kana(self, kana: str, sub: str = "") -> None:
        self.last_shown = kana
        self.shown.append(kana)

    def say(self, text: str = "", style: str | None = None) -> None:
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
        if prompt.startswith("Chọn 1"):
            return Answer(kind="answer", text="1")
        if "Gõ lại romaji đúng" in prompt:
            return Answer(kind="answer", text=self.comparison_romaji or self._expected())
        if "Gõ lại" in prompt:
            return Answer(kind="answer", text=self._expected())
        if prompt.startswith(("Romaji", "Kana", "Chuỗi", "Từ này")):
            return Answer(kind="answer", text=self._expected())
        return Answer(kind="answer", text=self._expected())

    def _expected(self) -> str:
        shown = self.last_shown.strip()
        kana_chars = [ch for ch in shown if ch in self.dataset.by_kana]
        if not kana_chars:
            return "a"
        return " ".join(self.dataset.by_kana[ch].romaji for ch in kana_chars)


class QuitAfterUI(RecordingUI):
    """UI trả lời đúng nhưng thoát sau N câu hỏi."""

    def __init__(self, dataset: KanaDataset, options: UIOptions | None = None, *, n: int = 3) -> None:
        super().__init__(dataset, options)
        self.n = n
        self.count = 0

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        if prompt.startswith(("Romaji", "Kana", "Chuỗi", "Từ này")):
            self.count += 1
            if self.count >= self.n:
                return Answer(kind="quit", text="quit")
        return super().read_answer(prompt)


def review_cards_for(save: SaveData, kana_list: str, *, due: bool = True, stage: int = 0) -> None:
    for kana in kana_list:
        card = KanaCard(state=KanaState.REVIEW, review_stage=stage)
        card.next_review_at = NOW - datetime.timedelta(hours=1) if due else NOW + datetime.timedelta(days=10)
        card.introduced_at = NOW - DAY
        save.cards[kana] = card


def run_review(ui, dataset, save, scheduler, rng, mode: str, settings: dict) -> "ReviewReport":
    return ReviewSession(ui, dataset, save, scheduler, rng, NOW, "s1", mode, settings).run()


def test_review_lesson_mode_asks_only_lesson_kana(dataset, fresh_save, scheduler, seeded_rng) -> None:
    lessons = load_lessons(dataset)
    review_cards_for(fresh_save, "あいうえお")
    review_cards_for(fresh_save, "かき")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "lesson",
                        {"pool": lesson_pool(fresh_save, lessons.by_id[1]), "lesson_id": 1, "total": 20})
    asked = {k for k in ui.shown if k in dataset.by_kana}
    assert asked == {"あ", "い", "う", "え", "お"}
    assert report.total == 20
    assert report.correct == 20


def test_review_multi_mode_union_pool(dataset, fresh_save, scheduler, seeded_rng) -> None:
    lessons = load_lessons(dataset)
    review_cards_for(fresh_save, "あいうえおかき")
    unlock_lesson_progress(fresh_save, lessons, [1])
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "multi",
                        {"pool": ["あ", "い", "か", "き"], "lesson_ids": [1, 2], "full": True})
    asked = {k for k in ui.shown if k in dataset.by_kana}
    assert asked == {"あ", "い", "か", "き"}
    assert report.total == 4


def test_review_random_mode_rounds_without_repeat(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいうえお")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "random",
                        {"pool": ["あ", "い", "う", "え", "お"], "total": 20})
    asked = [k for k in ui.shown if k in dataset.by_kana]
    assert len(asked) == 20
    assert all(asked[i] != asked[i + 1] for i in range(len(asked) - 1))
    counts = Counter(asked)
    assert all(count == 4 for count in counts.values())  # 20 câu = đúng 4 vòng
    assert report.total == 20
    assert report.correct == 20


def test_review_random_practice_does_not_move_not_due(dataset, fresh_save, scheduler, seeded_rng) -> None:
    """Random review (practice) không đẩy next_review của kana chưa đến hạn."""
    review_cards_for(fresh_save, "あいう", due=False, stage=2)
    next_before = {k: fresh_save.card(k).next_review_at for k in "あいう"}
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "random",
               {"pool": ["あ", "い", "う"], "total": 10})
    for k in "あいう":
        assert fresh_save.card(k).next_review_at == next_before[k]
        assert fresh_save.card(k).review_stage == 2


def test_review_random_due_item_updates_srs(dataset, fresh_save, scheduler, seeded_rng) -> None:
    """Kana đến hạn trong Random review vẫn cập nhật SRS bình thường."""
    review_cards_for(fresh_save, "あ", due=True, stage=0)
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "random",
               {"pool": ["あ"], "total": 3})
    card = fresh_save.card("あ")
    assert card.review_stage > 0
    assert card.next_review_at > NOW


def test_review_smart_mode_runs_and_updates(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいう")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "smart",
                        {"pool": ["あ", "い", "う"], "total": 15})
    assert report.total == 15
    assert report.correct == 15


def test_review_all_mode_runs(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいうえお")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "all",
                        {"pool": ["あ", "い", "う", "え", "お"], "full": True})
    assert report.total == 5
    assert report.correct == 5


def test_review_srs_mode_legacy_path(dataset, fresh_save, scheduler, seeded_rng) -> None:
    """SRS Recommended chạy qua luồng legacy: hỏi đủ kana đến hạn một lần."""
    review_cards_for(fresh_save, "あいう")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "srs",
                        {"pool": ["あ", "い", "う"]})
    assert report.total == 3
    assert report.correct == 3
    for k in "あいう":
        assert fresh_save.card(k).review_stage > 0  # review thật -> đẩy lịch SRS


def test_review_double_size_asks_each_twice(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あい")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "lesson",
                        {"pool": ["あ", "い"], "lesson_id": 1, "double": True})
    counts = Counter(k for k in ui.shown if k in dataset.by_kana)
    assert counts == {"あ": 2, "い": 2}
    assert report.total == 4


def test_review_endless_quit_early(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいうえお")
    ui = QuitAfterUI(dataset, UIOptions(delay_ms=0), n=4)
    report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "lesson",
                        {"pool": ["あ", "い", "う", "え", "お"], "lesson_id": 1, "endless": True})
    assert report.quit_early is True
    assert report.total == 3


def test_review_deck_empty_pool_returns_early(dataset, fresh_save, scheduler, seeded_rng) -> None:
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    for mode in ("lesson", "multi", "random", "smart", "all"):
        report = run_review(ui, dataset, fresh_save, scheduler, seeded_rng, mode, {})
        assert report.total == 0, f"mode {mode} phải trả về ngay khi pool rỗng"


def test_review_lesson_updates_lesson_progress(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいう")
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "lesson",
               {"pool": ["あ", "い", "う"], "lesson_id": 1, "total": 10})
    progress = fresh_save.lesson_progress[1]
    assert progress.last_practiced_at == NOW
    assert progress.total_attempts == 10
    assert progress.accuracy == 1.0


def test_review_multi_updates_all_lessons_progress(dataset, fresh_save, scheduler, seeded_rng) -> None:
    review_cards_for(fresh_save, "あいうえおか")
    fresh_save.lesson_progress[1] = LessonProgress(lesson_id=1, learn_completed=True)
    fresh_save.lesson_progress[2] = LessonProgress(lesson_id=2)
    ui = RecordingUI(dataset, UIOptions(delay_ms=0))
    run_review(ui, dataset, fresh_save, scheduler, seeded_rng, "multi",
               {"pool": ["あ", "か"], "lesson_ids": [1, 2], "total": 2})
    assert fresh_save.lesson_progress[1].last_practiced_at == NOW
    assert fresh_save.lesson_progress[2].last_practiced_at == NOW
    assert fresh_save.lesson_progress[2].total_attempts == 2


def test_review_deck_deterministic_with_same_seed(dataset, fresh_save, scheduler) -> None:
    review_cards_for(fresh_save, "あいうえお")

    def run(seed: int) -> list[str]:
        rng = random.Random(seed)
        ui = RecordingUI(dataset, UIOptions(delay_ms=0))
        run_review(ui, dataset, fresh_save, scheduler, rng, "random",
                   {"pool": ["あ", "い", "う", "え", "お"], "total": 15})
        return [k for k in ui.shown if k in dataset.by_kana]

    assert run(42) == run(42)
    assert run(42) != run(43)


def unlock_lesson_progress(save: SaveData, lessons: LessonDataset, ids: list[int]) -> None:
    for lesson_id in ids:
        lesson = lessons.by_id[lesson_id]
        save.lesson_progress[lesson_id] = LessonProgress(
            lesson_id=lesson_id,
            introduced_kana=list(lesson.kana),
            completed_subgroups=list(range(lesson.group_count)),
            learn_completed=True,
        )


def lesson_pool(save: SaveData, lesson) -> list[str]:
    from kana_rush.lessons import lesson_pool as lp

    return lp(save, lesson)
