"""Learn Mode: lesson 6 giai đoạn (encode, first retrieval, corrective recall,
mixed recall, confusion battle, boss round) + điều kiện hoàn thành.

Hỗ trợ lesson cố định với subgroup (Lesson 7: 5+3, Lesson 8: 5+3):
mỗi subgroup học tuần tự, xong subgroup nào mới sang subgroup sau,
kết thúc bằng Boss Round trộn toàn bộ kana của lesson.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field

from kana_rush.data import KanaDataset
from kana_rush.lessons import Lesson
from kana_rush.models import AnswerSource, KanaState, LessonProgress, SaveData
from kana_rush.scheduler import QuestionPicker, RevisitQueue, Scheduler
from kana_rush.scoring import level_for_xp
from kana_rush.session import QuestionRunner
from kana_rush.ui import UI

COMPLETION_ACCURACY = 0.85
MIN_CORRECTS_PER_NEW = 2
BOSS_HP_BASE = 50
BOSS_HP_PER_LEVEL = 10
BOSS_HP_PER_MASTERED = 1
BOSS_MAX_QUESTIONS_BASE = 10
BOSS_MAX_QUESTIONS_PER_LEVEL = 2
BOSS_DAMAGE_FAST = 15
BOSS_DAMAGE_MEDIUM = 10
BOSS_DAMAGE_SLOW = 6


@dataclass(frozen=True)
class BossStats:
    hp: int
    damage_bonus: int
    max_questions: int
    level: int
    mastered: int


def boss_stats(save: SaveData) -> BossStats:
    """Độ khó boss theo level XP và số kana MASTERED."""
    level = level_for_xp(save.xp)
    mastered = sum(1 for c in save.cards.values() if c.state is KanaState.MASTERED)
    hp = BOSS_HP_BASE + (level - 1) * BOSS_HP_PER_LEVEL + mastered * BOSS_HP_PER_MASTERED
    damage_bonus = (level - 1) // 2 + mastered // 8
    max_questions = BOSS_MAX_QUESTIONS_BASE + (level - 1) * BOSS_MAX_QUESTIONS_PER_LEVEL
    return BossStats(hp, damage_bonus, max_questions, level, mastered)


@dataclass
class LearnReport:
    new_ids: list[str]
    completed: bool
    questions_asked: int
    unaided_corrects: int
    unaided_total: int
    xp_gained: int
    mastered_new: list[str] = field(default_factory=list)
    lesson_id: int | None = None
    completed_subgroups: list[int] = field(default_factory=list)


class LearnSession:
    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
        lesson_or_ids,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        if isinstance(lesson_or_ids, Lesson):
            self.lesson: Lesson | None = lesson_or_ids
            self.new_ids = list(lesson_or_ids.kana)
            self.subgroups = [list(g) for g in lesson_or_ids.subgroups]
        else:
            self.lesson = None
            self.new_ids = list(lesson_or_ids)
            self.subgroups = [list(lesson_or_ids)]
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)
        self.revisits = RevisitQueue()
        self.picker = QuestionPicker(save, rng, now)
        self.position = 0
        self.unaided_corrects = 0
        self.unaided_total = 0
        self.xp_gained = 0
        self.completed_subgroups: list[int] = []
        self._group_kana: list[str] = []
        self._group_corrects: dict[str, int] = {}
        self._group_wrongs: dict[str, int] = {}
        self._group_recovered: dict[str, bool] = {}

    # ------------------------------------------------------------ helpers
    def _old_pool(self, target_count: int) -> list[str]:
        """20-30% kana cũ (đã giới thiệu, không nằm trong group hiện tại)."""
        old = [
            k
            for k, card in self.save.cards.items()
            if card.state is not KanaState.NEW and k not in self._group_kana
        ]
        self.rng.shuffle(old)
        return old[:target_count]

    def _record_answer(self, result, is_lesson_kana: bool) -> None:
        self.position += 1
        self.xp_gained += result.xp
        if not result.hinted:
            self.unaided_total += 1
            if result.correct:
                self.unaided_corrects += 1
        if is_lesson_kana:
            if result.correct:
                self._group_corrects[result.kana_id] = (
                    self._group_corrects.get(result.kana_id, 0) + 1
                )
                self._group_recovered[result.kana_id] = True
                self.revisits.mark_correct(result.kana_id)
            else:
                self._group_wrongs[result.kana_id] = (
                    self._group_wrongs.get(result.kana_id, 0) + 1
                )
                self._group_recovered[result.kana_id] = False
                self.revisits.mark_wrong(result.kana_id, self.position)

    def _group_complete(self) -> bool:
        if self.unaided_total == 0:
            return False
        accuracy = self.unaided_corrects / self.unaided_total
        if accuracy < COMPLETION_ACCURACY:
            return False
        if any(self._group_corrects[k] < MIN_CORRECTS_PER_NEW for k in self._group_kana):
            return False
        if any(not self._group_recovered[k] for k in self._group_kana if self._group_wrongs[k] > 0):
            return False
        return True

    def _set_group(self, group: list[str]) -> None:
        self._group_kana = list(group)
        self._group_corrects = {k: 0 for k in group}
        self._group_wrongs = {k: 0 for k in group}
        self._group_recovered = {k: True for k in group}

    def _promote_group(self, group: list[str]) -> None:
        for kana_id in group:
            card = self.save.card(kana_id)
            if card.state in (KanaState.NEW, KanaState.LEARNING):
                self.scheduler.promote_to_review(self.save, kana_id, stage=0, now=self.now)

    def _todo_groups(self) -> list[tuple[int, list[str]]]:
        return [
            (index, list(group))
            for index, group in enumerate(self.subgroups)
            if any(
                self.save.card(k).state in (KanaState.NEW, KanaState.LEARNING)
                for k in group
            )
        ]

    def _update_lesson_progress(self, report: LearnReport) -> None:
        if self.lesson is None:
            return
        progress = self.save.lesson_progress.setdefault(
            self.lesson.id, LessonProgress(lesson_id=self.lesson.id)
        )
        if progress.started_at is None:
            progress.started_at = self.now
        progress.last_practiced_at = self.now
        progress.introduced_kana = list(
            dict.fromkeys(
                [*progress.introduced_kana, *lesson_introduced(self.save, self.lesson)]
            )
        )
        progress.completed_subgroups = sorted(
            set([*progress.completed_subgroups, *self.completed_subgroups])
        )
        progress.total_attempts += self.unaided_total
        if self.unaided_total:
            progress.accuracy = self.unaided_corrects / self.unaided_total
        if report.completed:
            progress.learn_completed = True
            progress.completed_at = self.now
            progress.completed_subgroups = list(range(self.lesson.group_count))

    # ------------------------------------------------------------ phases
    def _phase_encode(self) -> None:
        self.ui.say("[bold cyan]GIAI ĐOẠN 1: GIỚI THIỆU (Encode)[/bold cyan]")
        for kana_id in self._group_kana:
            kana_obj = self.dataset.kana(kana_id)
            lines = [
                f"Kana: {kana_id}   Romaji: {kana_obj.romaji}",
                f"Mnemonic: {self.dataset.mnemonics.get(kana_id, '')}",
            ]
            if self.dataset.distinguish.get(kana_id):
                lines.append(f"Phân biệt: {self.dataset.distinguish[kana_id]}")
            if self.dataset.stroke_orders.get(kana_id):
                lines.append(f"Thứ tự nét: {self.dataset.stroke_orders[kana_id]}")
            self.ui.panel("Kana mới", lines)
            self.ui.press_enter()
            self.scheduler.introduce(self.save, kana_id, self.now)

    def _phase_first_retrieval(self) -> bool:
        self.ui.say("[bold cyan]GIAI ĐOẠN 2: NHỚ LẠI LẦN ĐẦU[/bold cyan]")
        for kana_id in self._group_kana:
            result = self.runner.run_question(
                kana_id, source=AnswerSource.LESSON, allow_hint=True
            )
            if result.quit:
                return False
            self._record_answer(result, is_lesson_kana=True)
        return True

    def _phase_mixed_recall(self, cap: int) -> bool:
        self.ui.say("[bold cyan]GIAI ĐOẠN 3: TRỘN VÀ NHẮC LẠI (Mixed Recall)[/bold cyan]")
        old_pool = self._old_pool(max(1, round(len(self._group_kana) * 0.3)))
        pool = list(dict.fromkeys([*self._group_kana, *old_pool]))
        last_asked: str | None = None
        while self.position <= cap:
            blocked = self.revisits.blocked(self.position)
            picked = self.picker.pick(pool, last_asked=last_asked, blocked=blocked)
            if picked is None:
                if self.revisits.has_pending():
                    self.position += 1
                    continue
                break
            last_asked = picked
            result = self.runner.run_question(
                picked, source=AnswerSource.LESSON, allow_hint=True
            )
            if result.quit:
                return False
            self._record_answer(result, is_lesson_kana=picked in self._group_kana)
            if self._group_complete():
                return True
        return False

    def _phase_confusion_battle(self) -> None:
        self.ui.say("[bold cyan]GIAI ĐOẠN 4: TRẬN ĐẤU NHẦM LẪN[/bold cyan]")
        pairs = self._confusion_pairs_for_lesson()
        if not pairs:
            self.ui.say("Không có cặp dễ nhầm nào khả dụng, bỏ qua.", style="dim")
            return
        for a, b in self.rng.sample(pairs, min(4, len(pairs))):
            if not self._ask_comparison(a, b):
                return

    def _confusion_pairs_for_lesson(self) -> list[tuple[str, str]]:
        valid: set[str] = {k for k, c in self.save.cards.items() if c.state is not KanaState.NEW}
        pairs: list[tuple[str, str]] = []
        for a, b in self.dataset.confusion_pairs:
            if a in valid and b in valid and (a in self._group_kana or b in self._group_kana):
                pairs.append((a, b))
        for given, row in self.save.confusion_matrix.items():
            for mistyped in row:
                if (
                    given in valid
                    and mistyped in valid
                    and (given in self._group_kana or mistyped in self._group_kana)
                ):
                    pairs.append((given, mistyped))
        seen: set[tuple[str, str]] = set()
        unique = []
        for a, b in pairs:
            if (a, b) not in seen and (b, a) not in seen:
                seen.add((a, b))
                unique.append((a, b))
        return unique

    def _ask_comparison(self, kana_a: str, kana_b: str) -> bool:
        """Hỏi: trong 2 kana, kana nào đọc là X? Đáp án 1 hoặc 2."""
        if self.rng.random() < 0.5:
            kana_a, kana_b = kana_b, kana_a
        target = self.rng.choice([kana_a, kana_b])
        romaji = self.dataset.kana(target).romaji
        self.ui.say(
            f"[bold]{kana_a}[/bold]  [dim]|[/dim]  [bold]{kana_b}[/bold]  —  "
            f"Kana nào đọc là [bold yellow]'{romaji}'[/bold yellow]? (1 hoặc 2, 'quit' để thoát)"
        )
        while True:
            answer = self.ui.read_answer("Chọn 1 hoặc 2 > ")
            if answer.kind in ("quit", "eof"):
                return False
            text = answer.text
            if text not in ("1", "2"):
                self.ui.say("Chỉ nhập 1 hoặc 2.")
                continue
            chosen = kana_a if text == "1" else kana_b
            correct = chosen == target
            if correct:
                self.ui.feedback_correct(0, self.save.streak, extra="So sánh đúng!")
            else:
                other = kana_b if chosen == kana_a else kana_a
                self.ui.feedback_wrong(
                    correct_kana=target,
                    correct_romaji=romaji,
                    confused_kana=other,
                    confused_romaji=self.dataset.kana(other).romaji,
                    lesson_context=True,
                )
                self._corrective_typing_comparison(kana_a, kana_b, romaji)
            self.scheduler.record_result(
                self.save,
                target,
                correct=correct,
                hinted=True,
                rt_ms=800,
                session_id=self.session_id,
                source=AnswerSource.CONFUSION,
                confusion=None,
                now=self.now,
            )
            return True

    def _corrective_typing_comparison(self, kana_a: str, kana_b: str, romaji: str) -> None:
        while True:
            answer = self.ui.read_answer("Gõ lại romaji đúng > ")
            if answer.kind in ("quit", "eof"):
                return
            if answer.text == romaji:
                self.ui.say("Đã ghi nhận.", style="dim")
                return
            self.ui.say("Chưa khớp, thử lại.")

    def _phase_boss_round(self) -> None:
        self.ui.say("[bold magenta]GIAI ĐOẠN 5: BOSS ROUND[/bold magenta]")
        if self.lesson is not None:
            pool_base = list(self.lesson.kana)
        else:
            pool_base = list(self._group_kana)
        old_pool = self._old_pool(max(1, round(len(pool_base) * 0.5)))
        pool = list(dict.fromkeys([*pool_base, *old_pool]))
        stats = boss_stats(self.save)
        boss_hp_max = stats.hp
        boss_hp = boss_hp_max
        last_asked: str | None = None
        for _ in range(stats.max_questions):
            picked = self.picker.pick(pool, last_asked=last_asked)
            if picked is None:
                break
            last_asked = picked
            self.ui.panel(
                "BOSS",
                [
                    f"HP: {self.ui.progress_bar(boss_hp, boss_hp_max)} {boss_hp}/{boss_hp_max}",
                    f"Level {stats.level} - Mastered {stats.mastered}/46. Không có gợi ý. "
                    "Đúng càng nhanh, damage càng lớn.",
                ],
            )
            result = self.runner.run_question(
                picked, source=AnswerSource.LESSON, allow_hint=False
            )
            if result.quit:
                return
            if result.correct:
                if result.rt_ms < 2000:
                    damage = BOSS_DAMAGE_FAST + stats.damage_bonus
                elif result.rt_ms <= 5000:
                    damage = BOSS_DAMAGE_MEDIUM + stats.damage_bonus
                else:
                    damage = BOSS_DAMAGE_SLOW + stats.damage_bonus
                boss_hp = max(0, boss_hp - damage)
                self.ui.say(f"Boss mất {damage} HP.", style="bold green")
            else:
                self.ui.say("Boss không mất máu.", style="dim")
            self._record_answer(result, is_lesson_kana=picked in pool_base)
            if boss_hp <= 0:
                self.ui.say("BOSS ĐÃ BỊ ĐÁNH BẠI!", style="bold magenta")
                return

    # ------------------------------------------------------------ run
    def run(self) -> LearnReport:
        report = LearnReport(
            new_ids=list(self.new_ids),
            completed=False,
            questions_asked=0,
            unaided_corrects=0,
            unaided_total=0,
            xp_gained=0,
            lesson_id=self.lesson.id if self.lesson else None,
        )
        todo_groups = self._todo_groups()
        if not todo_groups:
            # Mọi kana đã vào REVIEW (vd: save cũ học theo hệ thống cũ):
            # lesson coi như đã xong Learn; cho chơi lại Boss Round.
            if self.lesson is None:
                self.ui.say("Không có kana mới để học.", style="dim")
                return report
            report.completed = True
            self.ui.say(
                f"Lesson {self.lesson.id} đã học xong. Chạy lại Boss Round để ôn!",
                style="dim",
            )
            self._phase_boss_round()
            self._finalize(report)
            return report

        for index, group in todo_groups:
            if self.lesson is not None and len(self.subgroups) > 1:
                self.ui.say(
                    f"[bold]Phần {index + 1}/{len(self.subgroups)}: {' '.join(group)}[/bold]"
                )
            self._set_group(group)
            self._phase_encode()
            if not self._phase_first_retrieval():
                self._finalize(report)
                return report
            cap = max(20, len(group) * 8)
            group_done = self._phase_mixed_recall(cap)
            if group_done and self._group_complete():
                self._phase_confusion_battle()
                self._promote_group(group)
                self.completed_subgroups.append(index)
                self.ui.say(f"[bold green]Hoàn thành phần {index + 1}/{len(self.subgroups)}![/bold green]")
            else:
                self.ui.say(
                    "Chưa đạt đủ điều kiện hoàn thành phần này. "
                    "Các kana mới sẽ được ôn trong phiên sau.",
                    style="yellow",
                )
                self._finalize(report)
                return report

        self._phase_boss_round()
        # Boss Round là phần cuối của Learn: kana vừa học xong phải ở
        # REVIEW stage 0 (boss không được đẩy lịch SRS của lesson này).
        for kana_id in self.new_ids:
            card = self.save.card(kana_id)
            if card.state in (KanaState.REVIEW, KanaState.RELEARNING):
                card.state = KanaState.REVIEW
                card.review_stage = 0
                card.next_review_at = self.now
        report.completed = True
        self.ui.say("[bold green]Lesson hoàn thành! Các kana mới đã chuyển sang REVIEW.[/bold green]")
        self._finalize(report)
        return report

    def _finalize(self, report: LearnReport) -> None:
        report.questions_asked = self.position
        report.unaided_corrects = self.unaided_corrects
        report.unaided_total = self.unaided_total
        report.xp_gained = self.xp_gained
        report.completed_subgroups = list(self.completed_subgroups)
        self._update_lesson_progress(report)


def lesson_introduced(save: SaveData, lesson: Lesson) -> list[str]:
    """Kana của lesson đã giới thiệu (dùng cho progress.introduced_kana)."""
    return [k for k in lesson.kana if save.card(k).state is not KanaState.NEW]
