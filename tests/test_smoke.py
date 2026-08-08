"""Smoke tests cấp App + CLI: nhiều kịch bản chạy thật qua UI scripted (user yêu cầu nhiều)."""

import json
import os
import subprocess
import sys

import pytest

from kana_rush.app import App
from kana_rush.data import KanaDataset
from kana_rush.models import KanaState, SaveData
from kana_rush.scheduler import Scheduler
from kana_rush.storage import Storage
from kana_rush.ui import Answer, UI, UIOptions

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SmartUI(UI):
    """UI tự trả lời đúng câu hỏi kana; menu chạy bằng scripted input."""

    def __init__(self, dataset, options, *, hint_every=0, quit_after=0):
        super().__init__(options)
        self.dataset = dataset
        self.last_shown = ""
        self.question_count = 0
        self.hint_every = hint_every
        self.quit_after = quit_after
        self.comparison_romaji = None

    def show_kana(self, kana: str, sub: str = "") -> None:
        self.last_shown = kana

    def say(self, text: str = "", style: str | None = None) -> None:
        # Câu so sánh "Kana nào đọc là 'X'? (1 hoặc 2)" - nhớ X để trả lời
        # phần corrective typing ("Gõ lại romaji đúng") sau khi trả lời sai.
        # (Không dùng re.search: crash access violation trên Python 3.14.)
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
            self.question_count += 1
            if self.quit_after and self.question_count >= self.quit_after:
                return Answer(kind="quit", text="quit")
            if self.hint_every and self.question_count % self.hint_every == 0:
                return Answer(kind="hint", text="hint")
            return Answer(kind="answer", text=self._expected())
        return super().read_answer(prompt)

    def _expected(self) -> str:
        shown = self.last_shown.strip()
        kana_chars = [ch for ch in shown if ch in self.dataset.by_kana]
        if not kana_chars:
            return "a"
        return " ".join(self.dataset.by_kana[ch].romaji for ch in kana_chars)


def run_app(dataset: KanaDataset, storage: Storage, scripted, *, seed: int = 1, **ui_kwargs) -> App:
    ui = SmartUI(dataset, UIOptions(delay_ms=0, scripted_input=list(scripted)), **ui_kwargs)
    app = App(ui, dataset, storage, seed=seed)
    app.run()
    return app


def save_with_review_cards(storage: Storage, dataset: KanaDataset, count: int = 3) -> SaveData:
    import datetime

    save = SaveData(session_count=2, xp=120)
    scheduler = Scheduler(dataset)
    now = datetime.datetime.now(datetime.timezone.utc)
    for i, ch in enumerate("あいうえおかきくけこ"):
        if i >= count:
            break
        scheduler.introduce(save, ch, now=now)
        scheduler.promote_to_review(save, ch, stage=1, now=now - datetime.timedelta(days=1))
        save.cards[ch].next_review_at = now - datetime.timedelta(hours=2)
    storage.save(save)
    return save


# ------------------------------------------------------------ kịch bản smoke


def test_smoke_fresh_learn_with_hints(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    run_app(dataset, storage, ["1", "2", "0"], hint_every=6)
    save = storage.load()
    assert save.session_count >= 1
    assert save.xp > 0
    reviewed = [k for k, c in save.cards.items() if c.state is KanaState.REVIEW]
    assert len(reviewed) >= 4


def test_smoke_first_run_diagnostic(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    app = run_app(dataset, storage, ["2", "0"])
    save = storage.load()
    assert save.diagnostic_done is True
    assert any(c.state is KanaState.REVIEW for c in save.cards.values())
    assert all(c.state is not KanaState.MASTERED for c in save.cards.values())


def test_smoke_daily_session(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    run_app(dataset, storage, ["1", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3
    assert loaded.xp >= 120


def test_smoke_quick_review(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    run_app(dataset, storage, ["3", "1", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3


def test_smoke_quit_mid_learn(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    run_app(dataset, storage, ["1", "2", "0"], quit_after=1)
    save = storage.load()
    assert save.session_count >= 1
    assert all(c.state is not KanaState.REVIEW for c in save.cards.values())


def test_smoke_quit_mid_diagnostic(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    run_app(dataset, storage, ["2", "0"], quit_after=3)
    save = storage.load()
    assert save.session_count >= 1
    assert all(c.state is KanaState.NEW for c in save.cards.values())


def test_smoke_word_bridge(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save = save_with_review_cards(storage, dataset, count=10)
    scheduler = Scheduler(dataset)
    word = dataset.words[0]
    for ch in word["decomposition"]:
        scheduler.introduce(save, ch)
        scheduler.promote_to_review(save, ch)
    storage.save(save)
    run_app(dataset, storage, ["5", "0"])
    loaded = storage.load()
    assert any(loaded.word_progress.get(w["kana"]) for w in dataset.words[:5])


def test_smoke_settings_toggles_persist(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    run_app(dataset, storage, ["9", "2", "3", "0", "0"])
    loaded = storage.load()
    assert loaded.settings.get("no_color") is True
    assert loaded.settings.get("ascii_mode") is True


def test_smoke_chart_and_stats(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    run_app(dataset, storage, ["7", "", "8", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3


def test_smoke_speedrun_locked_message(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    run_app(dataset, storage, ["6", "0"])
    loaded = storage.load()
    assert loaded.best_speedrun_score == 0


def test_smoke_confusion_drill(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    save = storage.load()
    save.confusion_matrix["ぬ"] = {"め": 2}
    storage.save(save)
    run_app(dataset, storage, ["4", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3


def test_smoke_corrupted_save_recovered_from_backup(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    storage.save(SaveData(session_count=5, xp=777))
    with open(storage.progress_path(), "w", encoding="utf-8") as fh:
        fh.write("garbage{{{")
    run_app(dataset, storage, ["0"])
    loaded = storage.load()
    assert loaded.xp == 120
    assert loaded.session_count == 3


def test_smoke_no_color_and_ascii_ui(tmp_path, dataset) -> None:
    storage = Storage(tmp_path)
    ui = SmartUI(dataset, UIOptions(delay_ms=0, no_color=True, ascii_mode=True, scripted_input=["2", "0"]))
    App(ui, dataset, storage, seed=3).run()
    save = storage.load()
    assert save.diagnostic_done is True


def test_smoke_cli_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "main.py"), "--smoke-test", "--no-delay"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SMOKE TEST: OK" in result.stdout


def test_smoke_cli_subprocess_ascii_seed() -> None:
    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "main.py"), "--smoke-test", "--no-delay", "--ascii", "--no-color", "--seed", "7"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SMOKE TEST: OK" in result.stdout


def test_smoke_menu_counts_learning_not_mastered(tmp_path, dataset) -> None:
    """Bug: 'Đang học' chỉ đếm LEARNING, sau lesson toàn REVIEW nên luôn 0."""
    from kana_rush.timeutil import local_date_str

    class CaptureUI(SmartUI):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lines = []

        def say(self, text: str = "", style: str | None = None) -> None:
            self.lines.append(str(text))

    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["0"]))
    App(ui, dataset, storage, seed=1).run()
    assert any("Đang học: 3" in line for line in ui.lines)


def test_smoke_legacy_save_day_streak_starts_at_one(tmp_path, dataset) -> None:
    """Bug: save cũ thiếu day_streak, last_active_date = hôm nay -> phải thành 1,
    không được dùng save.streak (answer streak) làm chuỗi ngày."""
    from kana_rush.timeutil import local_date_str

    storage = Storage(tmp_path)
    save = SaveData(session_count=1, xp=50, streak=21, best_streak=21)
    save.last_active_date = local_date_str()
    storage.save(save)
    run_app(dataset, storage, ["0"])
    loaded = storage.load()
    assert loaded.day_streak == 1
    assert loaded.streak == 21
