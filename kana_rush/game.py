"""Game: Diagnostic first-run, Daily Session, Speed Run, Confusion Drill."""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass

from kana_rush.data import KanaDataset
from kana_rush.models import AnswerSource, KanaState, SaveData
from kana_rush.scheduler import QuestionPicker, Scheduler
from kana_rush.scoring import check_achievements
from kana_rush.session import QuestionRunner
from kana_rush.statistics import median_recall_time, overall_accuracy
from kana_rush.timeutil import monotonic
from kana_rush.ui import UI
from kana_rush.words import NoWordsAvailable, pick_words, record_word_result

DIAGNOSTIC_BLOCK_SIZE = 10
DIAGNOSTIC_REVIEW_STAGE = 1
SPEEDRUN_SECONDS = 60
SPEEDRUN_MIN_ELIGIBLE = 10
DAILY_REVIEW_CAP = 25


@dataclass
class DiagnosticReport:
    asked: int
    correct: int
    promoted_to_review: list[str]
    skipped: bool = False


class Diagnostic:
    """First-run diagnostic: 46 kana theo block nhỏ, không feedback trong block."""

    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)

    def run(self) -> DiagnosticReport:
        self.ui.say("[bold cyan]KIỂM TRA NHANH TRÌNH ĐỘ[/bold cyan]")
        self.ui.say(
            "Bạn sẽ thấy từng kana và gõ romaji. Không có đáp án trong block; "
            "kết quả chỉ để xếp trình độ ban đầu.",
            style="dim",
        )
        order = list(self.dataset.by_kana.keys())
        self.rng.shuffle(order)
        blocks = [
            order[i : i + DIAGNOSTIC_BLOCK_SIZE]
            for i in range(0, len(order), DIAGNOSTIC_BLOCK_SIZE)
        ]
        report = DiagnosticReport(asked=0, correct=0, promoted_to_review=[])
        fast_correct: dict[str, int] = {}
        block_correct: list[bool] = []
        good_blocks: set[int] = set()
        for block_index, block in enumerate(blocks, start=1):
            if report.skipped:
                break
            self.ui.say(f"--- Block {block_index}/{len(blocks)} ({len(block)} kana) ---", style="bold")
            block_ok: list[bool] = []
            for kana_id in block:
                result = self.runner.run_question(
                    kana_id,
                    source=AnswerSource.DIAGNOSTIC,
                    allow_hint=False,
                    show_command_help=False,
                )
                if result.quit:
                    self.ui.say("Bỏ qua phần còn lại của diagnostic.", style="dim")
                    report.skipped = True
                    break
                report.asked += 1
                block_ok.append(result.correct)
                block_correct.append(result.correct)
                if result.correct:
                    report.correct += 1
                    if result.rt_ms < 3000:
                        fast_correct[kana_id] = block_index
            if block_ok and not report.skipped:
                accuracy = sum(block_ok) / len(block_ok)
                self.ui.show_summary(
                    f"Block {block_index}",
                    [f"Đúng {sum(block_ok)}/{len(block_ok)} ({accuracy:.0%})"],
                )
                if accuracy >= 0.7:
                    good_blocks.add(block_index)
            if report.skipped:
                break
        # Kana đúng nhanh trong block đạt >=70% -> REVIEW; sai/chậm -> NEW.
        for kana_id, block_index in fast_correct.items():
            if block_index in good_blocks:
                report.promoted_to_review.append(kana_id)
                self.scheduler.promote_to_review(
                    self.save, kana_id, stage=DIAGNOSTIC_REVIEW_STAGE, now=self.now
                )
        self.ui.show_summary(
            "Kết quả diagnostic",
            [
                f"Đúng: {report.correct}/{report.asked}",
                f"{len(report.promoted_to_review)} kana đã vào REVIEW (sẽ ôn cuối ngày).",
                "Chưa ai được tính MASTERED - phải nhớ lại qua nhiều phiên.",
            ],
        )
        self.save.diagnostic_done = True
        self.ui.press_enter()
        return report


class SpeedRun:
    """Đo phản xạ 60 giây; kết quả không phá lịch SRS."""

    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)

    def pool(self) -> list[str]:
        return [
            k
            for k, c in self.save.cards.items()
            if c.state in (KanaState.REVIEW, KanaState.MASTERED)
        ]

    @staticmethod
    def unlock_status(save: SaveData) -> tuple[bool, str]:
        eligible = [
            k
            for k, c in save.cards.items()
            if c.state in (KanaState.REVIEW, KanaState.MASTERED)
        ]
        if len(eligible) < SPEEDRUN_MIN_ELIGIBLE:
            return False, f"Cần ít nhất {SPEEDRUN_MIN_ELIGIBLE} kana ở REVIEW/MASTERED (hiện có {len(eligible)})."
        accuracy = overall_accuracy(save)
        if accuracy is not None and accuracy < 0.85:
            return False, f"Accuracy gần đây {accuracy:.0%} < 85%."
        median = median_recall_time(save)
        if median is not None and median > 2500:
            return False, f"Thời gian trả lời trung bình {median:.0f} ms > 2.5s."
        return True, ""

    def run(self) -> int:
        pool = self.pool()
        self.ui.say("[bold magenta]SPEED RUN - 60 giây[/bold magenta]")
        self.ui.say(
            "Đọc càng nhanh càng nhiều điểm. Sai chỉ hiện đáp án đúng. "
            "Kết quả không ảnh hưởng mạnh tới lịch ôn tập.",
            style="dim",
        )
        score = 0
        streak = 0
        asked = 0
        last_asked: str | None = None
        picker = QuestionPicker(self.save, self.rng, self.now)
        start_time = monotonic()
        while True:
            elapsed = monotonic() - start_time
            if elapsed >= SPEEDRUN_SECONDS or asked >= 30:
                break
            picked = picker.pick(pool, last_asked=last_asked)
            if picked is None:
                break
            last_asked = picked
            asked += 1
            result = self.runner.run_question(
                picked, source=AnswerSource.SPEEDRUN, allow_hint=False
            )
            if result.quit:
                break
            if result.correct:
                streak += 1
                bonus = max(0, min(20, 20 - result.rt_ms // 250))
                gained = 10 + bonus + min(streak * 2, 10)
                score += gained
            else:
                streak = 0
        self.save.best_speedrun_score = max(self.save.best_speedrun_score, score)
        self.ui.show_summary(
            "Kết thúc Speed Run",
            [
                f"Điểm: {score}",
                f"Câu đã hỏi: {asked}",
                f"Chuỗi dài nhất trong phiên: {streak}",
                f"Kỷ lục: {self.save.best_speedrun_score}",
            ],
        )
        check_achievements(self.save)
        return score


class ConfusionDrill:
    """8 câu so sánh cặp kana hay nhầm (từ seed + ma trận lỗi thật)."""

    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id

    def pairs(self) -> list[tuple[str, str]]:
        valid = {k for k, c in self.save.cards.items() if c.state is not KanaState.NEW}
        pairs: list[tuple[str, str]] = []
        for a, b in self.dataset.confusion_pairs:
            if a in valid and b in valid:
                pairs.append((a, b))
        for given, row in self.save.confusion_matrix.items():
            for mistyped, count in row.items():
                if given in valid and mistyped in valid and count > 0:
                    pairs.append((given, mistyped))
        seen: set[tuple[str, str]] = set()
        unique = []
        for a, b in pairs:
            if (a, b) not in seen and (b, a) not in seen:
                seen.add((a, b))
                unique.append((a, b))
        return unique

    def run(self) -> int:
        pairs = self.pairs()
        if not pairs:
            self.ui.say("Chưa có cặp dễ nhầm nào: hãy học kana trước.", style="dim")
            return 0
        self.ui.say("[bold cyan]CONFUSION DRILL[/bold cyan] - phân biệt cặp dễ nhầm")
        correct = 0
        asked = 0
        for a, b in self.rng.sample(pairs, min(8, len(pairs))):
            asked += 1
            target = self.rng.choice([a, b])
            romaji = self.dataset.kana(target).romaji
            if self.rng.random() < 0.5:
                a, b = b, a
            self.ui.say(
                f"[bold]{a}[/bold]  [dim]|[/dim]  [bold]{b}[/bold]  —  "
                f"Kana nào đọc là [bold yellow]'{romaji}'[/bold yellow]? (1 hoặc 2)"
            )
            answer = self.ui.read_answer("Chọn 1 hoặc 2 > ")
            if answer.kind in ("quit", "eof"):
                break
            chosen = a if answer.text == "1" else b
            ok = answer.text in ("1", "2") and chosen == target
            if ok:
                correct += 1
                self.ui.feedback_correct(5, self.save.streak, extra="Phân biệt đúng!")
                self.save.xp += 5
            else:
                other = b if chosen == a else a
                self.ui.feedback_wrong(
                    correct_kana=target,
                    correct_romaji=romaji,
                    confused_kana=other,
                    confused_romaji=self.dataset.kana(other).romaji,
                    lesson_context=False,
                )
            self.scheduler.record_result(
                self.save,
                target,
                correct=ok,
                hinted=True,
                rt_ms=900,
                session_id=self.session_id,
                source=AnswerSource.CONFUSION,
                confusion=None,
                now=self.now,
            )
        self.ui.show_summary("Confusion Drill", [f"Đúng {correct}/{asked}"])
        return correct


class DailySession:
    """Review đến hạn -> Learn kana mới -> Word Bridge -> báo cáo."""

    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)

    def run(self) -> None:
        from kana_rush.learn import LearnSession
        from kana_rush.review import ReviewSession

        self.ui.say("[bold]BUỔI HỌC HÔM NAY[/bold]", style="bold yellow")
        total_xp = 0

        # 1) Review kana đến hạn.
        due = self.save.due_ids(self.now)
        if due:
            self.ui.say(f"Review {min(len(due), DAILY_REVIEW_CAP)} kana đến hạn...", style="cyan")
            session = ReviewSession(
                self.ui, self.dataset, self.save, self.scheduler, self.rng,
                self.now, self.session_id, "full", self.save.settings,
            )
            report = session.run()
            total_xp += report.xp_gained
        else:
            self.ui.say("Không có kana đến hạn. Tuyệt vời!", style="dim")

        # 2) Learn kana mới nếu còn và chưa học quá target hôm nay.
        new_ids = [k for k in self.dataset.by_kana if self.save.card(k).state is KanaState.NEW]
        if new_ids:
            from kana_rush.scheduler import adaptive_new_count

            target = int(self.save.settings.get("daily_learn_target", adaptive_new_count(self.save)))
            introduced_today = sum(
                1
                for c in self.save.cards.values()
                if c.introduced_at and c.introduced_at.date() == self.now.date()
            )
            if introduced_today >= target:
                self.ui.say(
                    f"Đã học đủ {introduced_today} kana hôm nay. Hẹn gặp lại!",
                    style="dim",
                )
            else:
                count = min(target - introduced_today, len(new_ids), 7)
                learn_session = LearnSession(
                    self.ui, self.dataset, self.save, self.scheduler, self.rng,
                    self.now, self.session_id, new_ids[:count],
                )
                report = learn_session.run()
                total_xp += report.xp_gained

        # 3) Word Bridge ngắn.
        self._word_bridge(3)

        self.ui.show_summary(
            "Báo cáo buổi học",
            [
                f"XP nhận được: {total_xp}",
                f"Tổng XP: {self.save.xp}",
                f"Chuỗi đúng hiện tại: {self.save.streak}",
                f"Mastered: {sum(1 for c in self.save.cards.values() if c.state is KanaState.MASTERED)}/46",
            ],
        )
        check_achievements(self.save)

    def _word_bridge(self, count: int) -> None:
        self.ui.say("[bold cyan]WORD BRIDGE[/bold cyan] - đọc từ tiếng Nhật ngắn")
        try:
            words = pick_words(self.save, self.dataset, count, self.rng)
        except NoWordsAvailable as exc:
            self.ui.say(str(exc), style="dim")
            return
        for word in words:
            self.ui.show_kana(word["kana"], sub="Đọc cả từ (không có romaji cạnh từ)")
            answer = self.ui.read_answer("Từ này đọc là > ")
            if answer.kind in ("quit", "eof"):
                return
            if answer.kind == "hint":
                self.ui.feedback_hint(f"Đọc là: {word['romaji']}")
                self.ui.say(f"Nghĩa: {word['meaning']}", style="dim")
                answer = self.ui.read_answer("Từ này đọc là > ")
                if answer.kind in ("quit", "eof"):
                    return
                correct = answer.text == word["romaji"]
            else:
                correct = answer.text == word["romaji"]
                if correct:
                    self.ui.feedback_correct(10, self.save.streak, extra=f"Nghĩa: {word['meaning']}")
                    self.save.xp += 10
                    self.save.streak += 1
                else:
                    self.ui.feedback_wrong(
                        correct_kana=word["kana"],
                        correct_romaji=word["romaji"],
                        confused_kana=None,
                        confused_romaji=None,
                        lesson_context=False,
                    )
                    self.save.streak = 0
                    self.ui.say(f"Nghĩa: {word['meaning']}", style="dim")
            record_word_result(
                self.save,
                word,
                correct=correct,
                rt_ms=900,
                session_id=self.session_id,
            )
        self.ui.say("[dim]Kết quả đọc từ được theo dõi riêng, không tăng mastery kana.[/dim]")
