"""Learn Mode: lesson 6 giai đoạn (encode, first retrieval, corrective recall,
mixed recall, confusion battle, boss round) + điều kiện hoàn thành."""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field

from kana_rush.data import KanaDataset
from kana_rush.models import AnswerSource, KanaState, SaveData
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
        new_ids: list[str],
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        self.new_ids = new_ids
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)
        self.revisits = RevisitQueue()
        self.picker = QuestionPicker(save, rng, now)
        self.position = 0
        self.unaided_corrects = 0
        self.unaided_total = 0
        self.xp_gained = 0
        self.lesson_corrects: dict[str, int] = {k: 0 for k in new_ids}
        self.lesson_wrongs: dict[str, int] = {k: 0 for k in new_ids}
        self.recovered: dict[str, bool] = {k: True for k in new_ids}

    # ------------------------------------------------------------ helpers
    def _old_pool(self, target_count: int) -> list[str]:
        """20-30% kana cũ (đã giới thiệu, không nằm trong lesson)."""
        old = [
            k
            for k, card in self.save.cards.items()
            if card.state is not KanaState.NEW and k not in self.new_ids
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
                self.lesson_corrects[result.kana_id] += 1
                self.recovered[result.kana_id] = True
                self.revisits.mark_correct(result.kana_id)
            else:
                self.lesson_wrongs[result.kana_id] += 1
                self.recovered[result.kana_id] = False
                self.revisits.mark_wrong(result.kana_id, self.position)

    def _lesson_complete(self) -> bool:
        if self.unaided_total == 0:
            return False
        accuracy = self.unaided_corrects / self.unaided_total
        if accuracy < COMPLETION_ACCURACY:
            return False
        if any(self.lesson_corrects[k] < MIN_CORRECTS_PER_NEW for k in self.new_ids):
            return False
        if any(not self.recovered[k] for k in self.new_ids if self.lesson_wrongs[k] > 0):
            return False
        return True

    # ------------------------------------------------------------ phases
    def _phase_encode(self) -> None:
        self.ui.say("[bold cyan]GIAI ĐOẠN 1: GIỚI THIỆU (Encode)[/bold cyan]")
        for kana_id in self.new_ids:
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

    def _phase_first_retrieval(self) -> None:
        self.ui.say("[bold cyan]GIAI ĐOẠN 2: NHỚ LẠI LẦN ĐẦU[/bold cyan]")
        for kana_id in self.new_ids:
            result = self.runner.run_question(
                kana_id, source=AnswerSource.LESSON, allow_hint=True
            )
            if result.quit:
                return
            self._record_answer(result, is_lesson_kana=True)

    def _phase_mixed_recall(self, cap: int) -> bool:
        self.ui.say("[bold cyan]GIAI ĐOẠN 3: TRỘN VÀ NHẮC LẠI (Mixed Recall)[/bold cyan]")
        old_pool = self._old_pool(max(1, round(len(self.new_ids) * 0.3)))
        pool = list(dict.fromkeys([*self.new_ids, *old_pool]))
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
            self._record_answer(result, is_lesson_kana=picked in self.new_ids)
            if self._lesson_complete():
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
            if a in valid and b in valid and (a in self.new_ids or b in self.new_ids):
                pairs.append((a, b))
        for given, row in self.save.confusion_matrix.items():
            for mistyped in row:
                if (
                    given in valid
                    and mistyped in valid
                    and (given in self.new_ids or mistyped in self.new_ids)
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
        old_pool = self._old_pool(max(1, round(len(self.new_ids) * 0.5)))
        pool = list(dict.fromkeys([*self.new_ids, *old_pool]))
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
            self._record_answer(result, is_lesson_kana=picked in self.new_ids)
            if boss_hp <= 0:
                self.ui.say("BOSS ĐÃ BỊ ĐÁNH BẠI!", style="bold magenta")
                return

    # ------------------------------------------------------------ run
    def run(self) -> LearnReport:
        report = LearnReport(new_ids=list(self.new_ids), completed=False, questions_asked=0, unaided_corrects=0, unaided_total=0, xp_gained=0)
        if not self.new_ids:
            self.ui.say("Không có kana mới để học.", style="dim")
            return report
        self._phase_encode()
        self._phase_first_retrieval()
        cap = max(20, len(self.new_ids) * 8)
        completed = self._phase_mixed_recall(cap)
        if completed:
            self._phase_confusion_battle()
            self._phase_boss_round()
        if completed and self._lesson_complete():
            for kana_id in self.new_ids:
                self.scheduler.promote_to_review(self.save, kana_id, stage=0, now=self.now)
            report.completed = True
            self.ui.say("[bold green]Lesson hoàn thành! Các kana mới đã chuyển sang REVIEW.[/bold green]")
        else:
            self.ui.say(
                "Chưa đạt đủ điều kiện hoàn thành lesson. Các kana mới sẽ được ôn trong phiên sau.",
                style="yellow",
            )
        report.questions_asked = self.position
        report.unaided_corrects = self.unaided_corrects
        report.unaided_total = self.unaided_total
        report.xp_gained = self.xp_gained
        return report
