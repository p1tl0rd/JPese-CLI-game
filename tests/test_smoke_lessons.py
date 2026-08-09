"""Smoke tests cấp App cho hệ thống lesson: toàn trình 8 lesson, resume
Lesson 7 giữa chừng, các luồng menu review mới, và kịch bản CLI subprocess.

Tái dùng SmartUI/run_app/save_with_review_cards từ test_smoke.py.
"""

import io
import os
import subprocess
import sys

from kana_rush.app import App
from kana_rush.data import KanaDataset
from kana_rush.lessons import LessonDataset
from kana_rush.models import KanaState, LessonProgress, SaveData
from kana_rush.storage import Storage
from kana_rush.ui import Answer, UIOptions

from test_smoke import SmartUI, advance_to_lesson, run_app, save_with_review_cards

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CaptureUI(SmartUI):
    """SmartUI ghi lại mọi dòng say() để kiểm tra nội dung menu."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lines: list[str] = []

    def say(self, text: str = "", style: str | None = None) -> None:
        self.lines.append(str(text))


class PerfectUI(SmartUI):
    """SmartUI nhưng trả lời ĐÚNG mọi thứ, kể cả câu so sánh (1/2) và reverse.

    Mô phỏng người chơi hoàn hảo: dùng cho kịch bản toàn trình xác định.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._comparison_pair: list[str] = []

    def say(self, text: str = "", style: str | None = None) -> None:
        super().say(text, style)
        if isinstance(text, str) and "đọc là" in text and "|" in text:
            kana_chars = [ch for ch in text if ch in self.dataset.by_kana][:2]
            if len(kana_chars) == 2:
                self._comparison_pair = kana_chars

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        if prompt.startswith("Chọn 1") and len(self._comparison_pair) == 2:
            left, _ = self._comparison_pair
            if self.dataset.kana(left).romaji == self.comparison_romaji:
                return Answer(kind="answer", text="1")
            return Answer(kind="answer", text="2")
        return super().read_answer(prompt)

    def _expected(self) -> str:
        shown = self.last_shown.strip()
        if shown and all(ch not in self.dataset.by_kana for ch in shown):
            for kana_obj in self.dataset.kana_list:
                if kana_obj.romaji == shown:
                    return kana_obj.kana
        return super()._expected()


def run_app_with(ui_cls, dataset: KanaDataset, storage: Storage, scripted, *, seed: int = 1, **ui_kwargs) -> App:
    ui = ui_cls(dataset, UIOptions(delay_ms=0, scripted_input=list(scripted)), **ui_kwargs)
    app = App(ui, dataset, storage, seed=seed)
    app.run()
    return app


def unlock_lesson_one(storage: Storage, dataset: KanaDataset, count: int = 5) -> SaveData:
    """Save có lesson 1 đã học xong (kana REVIEW) để mở khóa lesson 2."""
    import datetime

    from kana_rush.scheduler import Scheduler

    save = save_with_review_cards(storage, dataset, count=count)
    save.lesson_progress[1] = LessonProgress(
        lesson_id=1,
        introduced_kana=[k for k in "あいうえお" if save.card(k).state is not KanaState.NEW],
        completed_subgroups=[0],
        learn_completed=True,
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    scheduler = Scheduler(dataset)
    now = datetime.datetime.now(datetime.timezone.utc)
    for kana in "かき":
        scheduler.introduce(save, kana, now=now)
        scheduler.promote_to_review(save, kana, now=now)
    storage.save(save)
    return save


# ------------------------------------------------------------ toàn trình


def test_smoke_full_journey_all_8_lessons(tmp_path, dataset) -> None:
    """Tiêu chí nghiệm thu: học lần lượt cả 8 lesson qua menu, mở khóa tuần tự,
    Lesson 7/8 chia subgroup, toàn bộ 46 kana về REVIEW (người chơi hoàn hảo)."""
    storage = Storage(tmp_path)
    script = ["1", "2"]
    for lesson_id in range(1, 9):
        script += [str(lesson_id), "L", "0"]
    script += ["0", "0"]
    run_app_with(PerfectUI, dataset, storage, script)
    loaded = storage.load()
    assert loaded.session_count >= 1
    assert all(
        c.state is KanaState.REVIEW for c in loaded.cards.values()
    ), "Cả 46 kana phải vào REVIEW sau 8 lesson"
    for lesson_id in range(1, 9):
        progress = loaded.lesson_progress[lesson_id]
        assert progress.learn_completed, f"Lesson {lesson_id} chưa learn_completed"
        assert progress.completed_at is not None
    assert loaded.lesson_progress[7].completed_subgroups == [0, 1]
    assert loaded.lesson_progress[8].completed_subgroups == [0, 1]


def test_smoke_lesson7_quit_midway_then_resume(tmp_path, dataset) -> None:
    """Thoát giữa chừng Lesson 7 (trong subgroup 1), mở lại game và học tiếp
    tới xong - tiến độ subgroup không bị reset."""
    storage = Storage(tmp_path)
    save = SaveData(session_count=2)
    advance_to_lesson(save, dataset, 7)
    storage.save(save)

    # Phiên 1: bắt đầu Lesson 7 nhưng thoát sau 5 câu (giữa giai đoạn retrieval).
    run_app(dataset, storage, ["2", "7", "L", "0", "0", "0"], quit_after=5)
    loaded = storage.load()
    assert not loaded.lesson_progress[7].learn_completed
    assert loaded.lesson_progress[7].completed_subgroups == []
    assert loaded.lesson_progress[7].started_at is not None
    assert any(c.state is KanaState.LEARNING for c in loaded.cards.values())

    # Phiên 2: mở lại lesson 7, học tiếp tới xong.
    run_app(dataset, storage, ["2", "7", "L", "0", "0", "0"])
    loaded = storage.load()
    assert loaded.lesson_progress[7].learn_completed is True
    assert loaded.lesson_progress[7].completed_subgroups == [0, 1]
    lesson7 = LessonDataset(dataset=dataset).by_id[7]
    assert all(loaded.card(k).state is KanaState.REVIEW for k in lesson7.kana)


# ------------------------------------------------------------ review menu


def test_smoke_review_empty_pool_messages(tmp_path, dataset) -> None:
    """Review khi chưa có gì đến hạn / chưa học gì: chỉ hiện thông báo, không crash."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=0)
    run_app(dataset, storage, ["3", "1", "4", "0", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3


def test_smoke_multi_lesson_invalid_then_valid(tmp_path, dataset) -> None:
    """Nhập '9' (không tồn tại) -> báo lỗi và cho nhập lại; sau đó '1-2' chạy được."""
    storage = Storage(tmp_path)
    unlock_lesson_one(storage, dataset)
    run_app(dataset, storage, ["3", "3", "9", "1-2", "", "1", "0", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3
    assert loaded.lesson_progress[1].total_attempts >= 10


def test_smoke_review_multi_double_size(tmp_path, dataset) -> None:
    """Mỗi kana hai lần: pool 3 kana -> đúng 6 câu hỏi."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    run_app(dataset, storage, ["3", "3", "1", "", "5", "0", "0"])
    loaded = storage.load()
    assert loaded.lesson_progress[1].total_attempts == 6


def test_smoke_review_endless_quit_midway(tmp_path, dataset) -> None:
    """Endless review: dừng khi người chơi thoát giữa chừng (sau 7 lần hỏi)."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    run_app(dataset, storage, ["3", "2", "1", "4", "0", "0"], quit_after=7)
    loaded = storage.load()
    assert loaded.session_count == 3
    assert loaded.lesson_progress[1].total_attempts == 6


def test_smoke_random_reverse_direction(tmp_path, dataset) -> None:
    """Random Review hướng Romaji -> Kana: người chơi hoàn hảo trả lời đúng."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    run_app_with(PerfectUI, dataset, storage, ["3", "4", "2", "1", "0", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3
    assert loaded.xp > 120


def test_smoke_confusion_drill_via_review_menu(tmp_path, dataset) -> None:
    """Confusion Drill truy cập qua menu Review (option 7)."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=2)
    save = storage.load()
    save.confusion_matrix["ぬ"] = {"め": 2}
    storage.save(save)
    run_app(dataset, storage, ["3", "7", "0", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3


def test_smoke_srs_breakdown_shown(tmp_path, dataset) -> None:
    """SRS Recommended hiển thị phân tích theo lesson trước khi bắt đầu."""
    import rich.console

    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["3", "1", "0", "0"]))
    buffer = io.StringIO()
    ui.console = rich.console.Console(file=buffer, no_color=True, highlight=False)
    App(ui, dataset, storage, seed=1).run()
    content = buffer.getvalue()
    assert "3 kana đang đến hạn" in content
    assert "Lesson 1: 3" in content
    assert "Thời gian dự kiến" in content


def test_smoke_multi_lesson_all_keyword(tmp_path, dataset) -> None:
    """Chọn 'all' trong multi-lesson: review toàn bộ 46 kana, mỗi kana một lần."""
    import datetime

    storage = Storage(tmp_path)
    save = SaveData(session_count=2)
    lessons = LessonDataset(dataset=dataset)
    now = datetime.datetime.now(datetime.timezone.utc)
    for lesson in lessons.lessons:
        for k in lesson.kana:
            card = save.card(k)
            card.state = KanaState.REVIEW
            card.introduced_at = now - datetime.timedelta(days=1)
        save.lesson_progress[lesson.id] = LessonProgress(
            lesson_id=lesson.id,
            introduced_kana=list(lesson.kana),
            completed_subgroups=list(range(lesson.group_count)),
            learn_completed=True,
        )
    storage.save(save)
    run_app(dataset, storage, ["3", "3", "all", "", "4", "0", "0"])
    loaded = storage.load()
    assert loaded.session_count == 3
    assert loaded.lesson_progress[1].total_attempts >= 46


def test_smoke_daily_learns_incomplete_lesson(tmp_path, dataset) -> None:
    """Lesson 1 đang dở (お còn NEW): buổi học hôm nay học tiếp lesson 1 tới xong."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=4)
    run_app(dataset, storage, ["1", "0"])
    loaded = storage.load()
    assert loaded.card("お").state is KanaState.REVIEW
    assert loaded.lesson_progress[1].learn_completed is True


# ------------------------------------------------------------ lesson detail


def test_smoke_lesson_menu_shows_statuses(tmp_path, dataset) -> None:
    """Menu HỌC HIRAGANA hiển thị trạng thái: lesson 1 REVIEW_DUE, còn lại LOCKED."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=5)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["2", "0", "0"]))
    App(ui, dataset, storage, seed=1).run()
    assert any("[REVIEW_DUE]" in line and "1." in line for line in ui.lines)
    assert any("[LOCKED]" in line for line in ui.lines)


def test_smoke_lesson_detail_stats_screen(tmp_path, dataset) -> None:
    """Action [S] trong chi tiết lesson hiển thị thống kê từng kana."""
    import rich.console

    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=3)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["2", "1", "S", "0", "0", "0"]))
    buffer = io.StringIO()
    ui.console = rich.console.Console(file=buffer, no_color=True, highlight=False)
    App(ui, dataset, storage, seed=1).run()
    content = buffer.getvalue()
    assert "THỐNG KÊ LESSON" in content
    assert "あ  REVIEW" in content


def test_smoke_review_action_before_start_message(tmp_path, dataset) -> None:
    """Ấn [R] ở lesson chưa bắt đầu: cảnh báo, không crash."""
    storage = Storage(tmp_path)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["1", "2", "1", "R", "0", "0", "0"]))
    App(ui, dataset, storage, seed=1).run()
    assert any("chưa bắt đầu" in line for line in ui.lines)


def test_smoke_boss_replay_completed_lesson(tmp_path, dataset) -> None:
    """Mở lại lesson đã xong (kana toàn REVIEW từ save cũ): [L] chạy lại Boss
    Round và ghi nhận learn_completed."""
    storage = Storage(tmp_path)
    save_with_review_cards(storage, dataset, count=5)
    run_app(dataset, storage, ["2", "1", "L", "0", "0", "0"])
    loaded = storage.load()
    assert loaded.lesson_progress[1].learn_completed is True
    assert loaded.lesson_progress[1].completed_subgroups == [0]


def test_smoke_daily_session_when_all_done(tmp_path, dataset) -> None:
    """Buổi học hôm nay khi đã học xong 8 lesson: báo hết kana, vẫn review bình thường."""
    storage = Storage(tmp_path)
    save = SaveData(session_count=2)
    advance_to_lesson(save, dataset, 9)
    storage.save(save)
    ui = CaptureUI(dataset, UIOptions(delay_ms=0, scripted_input=["1", "0"]))
    App(ui, dataset, storage, seed=1).run()
    loaded = storage.load()
    assert loaded.session_count == 3
    assert any("Đã học xong các lesson" in line for line in ui.lines)


# ------------------------------------------------------------ CLI subprocess


def test_smoke_cli_subprocess_lesson_flow(tmp_path) -> None:
    """Chạy game thật qua CLI (stdin scripted): bắt đầu Lesson 1 rồi thoát bằng EOF,
    tiến độ lesson phải được lưu, chưa có kana nào vào REVIEW."""
    env = dict(os.environ)
    env["KANA_RUSH_NO_CLOUD"] = "1"
    script = "1\n2\n1\nL\n" + "0\n" * 5
    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "main.py"), "--no-delay", "--no-color",
         "--save-path", str(tmp_path)],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    loaded = Storage(tmp_path).load()
    assert loaded.lesson_progress[1].started_at is not None
    assert loaded.lesson_progress[1].introduced_kana
    assert all(c.state is not KanaState.REVIEW for c in loaded.cards.values())
