"""App: luồng chính, menu, cài đặt, thống kê, quản lý session và save."""

from __future__ import annotations

import datetime
import random
import secrets
import threading

from kana_rush.cloud import (
    PUSH_QUIT_TIMEOUT_S,
    PUSH_RETRY_ATTEMPTS,
    PUSH_RETRY_TIMEOUT_S,
    retry_pending_push,
    sync_pull,
    sync_push,
)
from kana_rush.data import KanaDataset
from kana_rush.game import ConfusionDrill, DailySession, Diagnostic, SpeedRun
from kana_rush.learn import LearnSession
from kana_rush.models import KanaState, SaveData
from kana_rush.review import ReviewSession
from kana_rush.scheduler import Scheduler, adaptive_new_count
from kana_rush.scoring import ACHIEVEMENT_LABELS, check_achievements
from kana_rush.storage import SaveCorrupted, Storage
from kana_rush.timeutil import local_date_str, monotonic, utcnow
from kana_rush.ui import UI
from kana_rush.words import NoWordsAvailable, pick_words, record_word_result
import kana_rush.statistics as stats
from kana_rush.scoring import level_for_xp


class App:
    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        storage: Storage,
        seed: int | None = None,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.storage = storage
        self.seed = seed
        self.save: SaveData | None = None
        self.rng = random.Random(seed)
        self.scheduler = Scheduler(dataset)
        self._session_start = monotonic()

    # ------------------------------------------------------------ lifecycle
    def _load_save(self) -> None:
        try:
            self.save = self.storage.load()
        except SaveCorrupted as exc:
            self.ui.say(f"[bold red]{exc}[/bold red]")
            if self.ui.confirm("Không phục hồi được save. Tạo tiến độ mới?"):
                self.save = SaveData()
            else:
                raise QuitToExit()
        except Exception as exc:  # noqa: BLE001 - lỗi bất ngờ phải báo rõ
            self.ui.say(f"[bold red]Lỗi khi đọc save: {exc}[/bold red]")
            if self.ui.confirm("Tạo tiến độ mới?"):
                self.save = SaveData()
            else:
                raise QuitToExit()

    def _begin_session(self) -> None:
        if not self.save:
            return
        self.save.session_count += 1
        self.save.session_id = (
            f"{utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        )
        if self.seed is not None:
            self.rng = random.Random(self.seed + self.save.session_count)
        else:
            self.rng = random.Random()

    def _update_streak(self) -> None:
        if not self.save:
            return
        today = local_date_str()
        yesterday = (
            datetime.date.today() - datetime.timedelta(days=1)
        ).isoformat()
        if self.save.last_active_date == today:
            # Save cũ chưa có day_streak (migration): coi hôm nay là ngày 1
            if self.save.day_streak < 1:
                self.save.day_streak = 1
        elif self.save.last_active_date == yesterday:
            self.save.day_streak += 1
        else:
            self.save.day_streak = 1
        self.save.last_active_date = today

    def save_now(self) -> None:
        if not self.save:
            return
        self._update_streak()
        self.save.total_study_seconds += monotonic() - self._session_start
        self._session_start = monotonic()
        self.save.updated_at = utcnow()
        try:
            self.storage.save(self.save)
        except Exception as exc:  # noqa: BLE001 - không được bỏ qua lỗi save
            self.ui.say(f"[bold red]LƯU LỖI: {exc}[/bold red]")
            self.ui.say("Tiến độ chưa được ghi an toàn. Hãy kiểm tra thư mục saves/.")

    def _sync_now(self, *, push: bool) -> None:
        try:
            if push:
                # Thoát game: giới hạn thời gian ngắn, phần nợ sẽ tự đẩy lại ở mở sau.
                warning = sync_push(self.storage.save_dir, timeout=PUSH_QUIT_TIMEOUT_S)
            else:
                warning = sync_pull(self.storage.save_dir)
        except Exception as exc:  # noqa: BLE001 - cloud không bao giờ chặn game
            warning = f"Cloud: lỗi bất ngờ ({exc})."
        if warning:
            self.ui.say(f"[bold yellow]{warning}[/bold yellow]")

    def _pull_worker(self, pending: dict[str, object]) -> None:
        """Chạy nền: kéo save từ git + đẩy lại commit nợ; không chặn việc mở game."""
        try:
            warning = sync_pull(self.storage.save_dir)
            retry_warning = retry_pending_push(
                self.storage.save_dir,
                timeout=PUSH_RETRY_TIMEOUT_S,
                attempts=PUSH_RETRY_ATTEMPTS,
            )
            if retry_warning:
                warning = f"{warning}\n{retry_warning}" if warning else retry_warning
            pending["warning"] = warning
        except Exception as exc:  # noqa: BLE001
            pending["warning"] = f"Cloud: lỗi bất ngờ ({exc})."
        finally:
            pending["done"] = True

    def _apply_pull_if_ready(self, pending: dict[str, object]) -> None:
        """Áp dụng kết quả pull khi người chơi đang ở menu (an toàn để nạp lại save)."""
        if pending.get("applied") or not pending.get("done"):
            return
        pending["applied"] = True
        warning = pending.get("warning")
        if not warning:
            return
        self.ui.say(f"[bold yellow]{warning}[/bold yellow]")
        if warning.startswith("Đã đồng bộ") or warning.startswith("Đã tải"):
            try:
                self.save = self.storage.load()
            except Exception as exc:  # noqa: BLE001
                self.ui.say(f"[bold red]Lỗi khi nạp save sau đồng bộ: {exc}[/bold red]")

    def _now(self) -> datetime.datetime:
        return utcnow()

    # ------------------------------------------------------------ sessions
    def _make_session(self):
        return {
            "session_id": self.save.session_id,
            "now": self._now(),
        }

    def _run_review(self, mode: str) -> None:
        session = ReviewSession(
            self.ui,
            self.dataset,
            self.save,
            self.scheduler,
            self.rng,
            self._now(),
            self.save.session_id,
            mode,
            self.save.settings,
        )
        session.run()
        self.save_now()

    def _run_learn(self, count: int | None = None) -> None:
        new_ids = [k for k in self.dataset.by_kana if self.save.card(k).state is KanaState.NEW]
        if not new_ids:
            self.ui.say("Đã học hết 46 kana! Vào Review hoặc Speed Run để duy trì.", style="dim")
            self.ui.delay()
            return
        if count is None:
            count = adaptive_new_count(self.save)
        count = min(count, len(new_ids), 7)
        # Học theo thứ tự bảng chữ cái (a i u e o, ka ki ku...); Review mới random.
        session = LearnSession(
            self.ui,
            self.dataset,
            self.save,
            self.scheduler,
            self.rng,
            self._now(),
            self.save.session_id,
            new_ids[:count],
        )
        session.run()
        self.save_now()

    def _run_word_bridge(self, count: int = 5) -> None:
        try:
            words = pick_words(self.save, self.dataset, count, self.rng)
        except NoWordsAvailable as exc:
            self.ui.say(str(exc), style="dim")
            self.ui.delay()
            return
        for word in words:
            self.ui.show_kana(word["kana"], sub="Đọc cả từ")
            answer = self.ui.read_answer("Từ này đọc là > ")
            if answer.kind in ("quit", "eof"):
                break
            if answer.kind == "hint":
                self.ui.feedback_hint(f"Đọc là: {word['romaji']}")
                self.ui.say(f"Nghĩa: {word['meaning']}", style="dim")
                answer = self.ui.read_answer("Từ này đọc là > ")
                if answer.kind in ("quit", "eof"):
                    break
                correct = answer.text == word["romaji"]
            else:
                correct = answer.text == word["romaji"]
                if correct:
                    self.ui.feedback_correct(10, self.save.streak, extra=f"Nghĩa: {word['meaning']}")
                    self.save.xp += 10
                    self.save.streak += 1
                    self.save.best_streak = max(self.save.best_streak, self.save.streak)
                else:
                    self.save.streak = 0
                    self.ui.feedback_wrong(
                        correct_kana=word["kana"],
                        correct_romaji=word["romaji"],
                        confused_kana=None,
                        confused_romaji=None,
                        lesson_context=False,
                    )
                    self.ui.say(f"Nghĩa: {word['meaning']}", style="dim")
            record_word_result(
                self.save, word, correct=correct, rt_ms=900, session_id=self.save.session_id
            )
        self.save_now()

    # ------------------------------------------------------------ screens
    def _show_stats(self) -> None:
        now = self._now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - datetime.timedelta(days=6)
        counts = stats.state_counts(self.save)
        learned_today = sum(
            1
            for c in self.save.cards.values()
            if c.introduced_at and c.introduced_at >= today_start
        )
        acc_today = stats.accuracy_between(self.save, today_start)
        acc_week = stats.accuracy_between(self.save, week_start)
        ret24 = stats.estimated_retention(self.save, 24.0)
        ret7d = stats.estimated_retention(self.save, 24.0 * 7)
        median = stats.median_recall_time(self.save)
        minutes = int(self.save.total_study_seconds // 60)
        lines = [
            f"Kana học hôm nay: {learned_today}",
            f"Đang REVIEW: {counts[KanaState.REVIEW]} | MASTERED: {counts[KanaState.MASTERED]} | Học lại: {counts[KanaState.RELEARNING]}",
            f"Đến hạn: {stats.due_count(self.save, now)}",
            f"Accuracy hôm nay: {f'{acc_today:.0%}' if acc_today is not None else 'chưa đủ dữ liệu'}",
            f"Accuracy 7 ngày: {f'{acc_week:.0%}' if acc_week is not None else 'chưa đủ dữ liệu'}",
            f"Retention 24h (est.): {f'{ret24:.0%}' if ret24 is not None else 'chưa đủ dữ liệu'}",
            f"Retention 7 ngày (est.): {f'{ret7d:.0%}' if ret7d is not None else 'chưa đủ dữ liệu'}",
            f"Median thời gian recall: {f'{median:.0f} ms' if median is not None else 'chưa đủ dữ liệu'}",
        ]
        weakest = stats.weakest_kanas(self.save, 3)
        if weakest:
            lines.append(
                "Kana yếu nhất: "
                + ", ".join(f"{k} ({a:.0%})" for k, a in weakest)
            )
        slowest = stats.slowest_kanas(self.save, 3)
        if slowest:
            lines.append(
                "Kana chậm nhất: "
                + ", ".join(f"{k} ({rt:.0f}ms)" for k, rt in slowest)
            )
        confusions = stats.top_confusions(self.save, 3)
        if confusions:
            lines.append(
                "Nhầm nhiều nhất: "
                + ", ".join(f"{a}->{b} ({n})" for a, b, n in confusions)
            )
        lines += [
            f"Recall đúng không hint: {stats.total_unaided_corrects(self.save)}",
            f"Lần dùng gợi ý: {stats.total_hints_used(self.save)}",
            f"Chuỗi ngày học: {self.save.day_streak} | Tổng thời gian: {minutes} phút | Số session: {self.save.session_count}",
            f"XP: {self.save.xp} (Level {level_for_xp(self.save.xp)})",
        ]
        achievements = [
            ACHIEVEMENT_LABELS.get(a, a) for a in self.save.achievements
        ]
        lines.append("Achievements: " + (", ".join(achievements) if achievements else "chưa có"))
        self.ui.show_summary("THỐNG KÊ", lines)
        self.ui.press_enter()

    def _show_settings(self) -> None:
        while True:
            settings = self.save.settings
            delay = settings.get("delay_ms", 600)
            color = not settings.get("no_color", False)
            ascii_mode = settings.get("ascii_mode", False)
            reverse = settings.get("reverse_mode", False)
            quick_size = settings.get("quick_review_size", 10)
            self.ui.panel(
                "CÀI ĐẶT",
                [
                    f"1. Delay giữa các câu: {'BẬT' if delay > 0 else 'TẮT'}",
                    f"2. Màu sắc: {'BẬT' if color else 'TẮT'}",
                    f"3. Chế độ ASCII (không box-drawing): {'BẬT' if ascii_mode else 'TẮT'}",
                    f"4. Reverse Mode (romaji -> kana, cần IME): {'BẬT' if reverse else 'TẮT'}",
                    f"5. Quick Review size: {quick_size} câu",
                    "6. Đặt lại toàn bộ tiến độ",
                    "0. Quay lại",
                ],
            )
            choice = self.ui.read_menu_choice()
            if choice is None or choice == "0":
                break
            if choice == "1":
                settings["delay_ms"] = 0 if delay > 0 else 600
            elif choice == "2":
                settings["no_color"] = not settings.get("no_color", False)
            elif choice == "3":
                settings["ascii_mode"] = not ascii_mode
            elif choice == "4":
                settings["reverse_mode"] = not settings.get("reverse_mode", False)
            elif choice == "5":
                size = int(settings.get("quick_review_size", 10))
                settings["quick_review_size"] = 15 if size == 10 else (20 if size == 15 else 10)
            elif choice == "6":
                if self.ui.confirm("Xóa toàn bộ tiến độ (backup vẫn giữ lại)?"):
                    self.save = SaveData()
                    self.save.settings = settings
                    self.storage.save(self.save)
                    self.ui.say("Đã tạo tiến độ mới.", style="green")
            self.ui.apply_settings(settings)
            self.save_now()

    # ------------------------------------------------------------ run
    def run(self) -> None:
        pending: dict[str, object] = {}
        threading.Thread(
            target=self._pull_worker, args=(pending,), daemon=True, name="cloud-pull"
        ).start()
        self._load_save()
        if not self.save:
            return
        self._begin_session()
        first_run = (
            self.save.session_count <= 1
            and not any(c.introduced_at for c in self.save.cards.values())
        )
        if first_run:
            self._first_run()

        while True:
            now = self._now()
            self._apply_pull_if_ready(pending)
            choice = self.ui.main_menu(self.save, now)
            try:
                if choice in (None, "0"):
                    self.save_now()
                    self.ui.say("Đã lưu. Hẹn gặp lại!", style="green")
                    self._sync_now(push=True)
                    return
                elif choice == "1":
                    DailySession(
                        self.ui, self.dataset, self.save, self.scheduler,
                        self.rng, now, self.save.session_id,
                    ).run()
                    self.save_now()
                elif choice == "2":
                    self._run_learn()
                elif choice == "3":
                    mode = self.ui.select_mode(
                        "REVIEW",
                        [("1", "Quick Review (~10 câu)"), ("2", "Full Review (toàn bộ đến hạn)"), ("0", "Quay lại")],
                    )
                    if mode in ("1", "2"):
                        self._run_review("quick" if mode == "1" else "full")
                elif choice == "4":
                    ConfusionDrill(
                        self.ui, self.dataset, self.save, self.scheduler,
                        self.rng, now, self.save.session_id,
                    ).run()
                    self.save_now()
                elif choice == "5":
                    self._run_word_bridge()
                elif choice == "6":
                    unlocked, reason = SpeedRun.unlock_status(self.save)
                    if not unlocked:
                        self.ui.say(f"Speed Run chưa mở khóa: {reason}", style="yellow")
                        self.ui.delay()
                    else:
                        SpeedRun(
                            self.ui, self.dataset, self.save, self.scheduler,
                            self.rng, now, self.save.session_id,
                        ).run()
                        self.save_now()
                elif choice == "7":
                    self.ui.chart(self.save, self.dataset)
                elif choice == "8":
                    self._show_stats()
                elif choice == "9":
                    self._show_settings()
                else:
                    self.ui.say("Lựa chọn không hợp lệ.", style="dim")
            except KeyboardInterrupt:
                self.save_now()
                self.ui.say("\nĐã lưu (Ctrl+C). Hẹn gặp lại!", style="green")
                self._sync_now(push=True)
                return

    def _first_run(self) -> None:
        self.ui.panel(
            "CHÀO MỪNG ĐẾN KANA RUSH",
            [
                "Game giúp bạn thuộc 46 Hiragana bằng active recall và spaced repetition.",
                "Mỗi phiên 5-10 phút, toàn bộ tiến độ lưu tại saves/progress.json.",
                "",
                "Bạn đã biết một phần Hiragana chưa?",
                "1. Bắt đầu từ số 0 (không kiểm tra)",
                "2. Kiểm tra nhanh trình độ (khuyến nghị nếu đã biết)",
            ],
        )
        choice = self.ui.read_menu_choice("Chọn 1 hoặc 2 > ")
        if choice == "2":
            Diagnostic(
                self.ui, self.dataset, self.save, self.scheduler,
                self.rng, self._now(), self.save.session_id,
            ).run()
        self.save_now()


class QuitToExit(Exception):
    """Người dùng chọn thoát khi save bị hỏng."""
