"""Chạy một câu hỏi kana: đo response time (monotonic, không tính lúc hint),
hint ladder, corrective typing, cập nhật XP/streak và scheduler."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from kana_rush.data import KanaDataset
from kana_rush.models import AnswerSource, SaveData
from kana_rush.scheduler import Scheduler
from kana_rush.scoring import score_answer
from kana_rush.timeutil import monotonic
from kana_rush.ui import Answer, UI


@dataclass
class QuestionResult:
    kana_id: str
    correct: bool = False
    hinted: bool = False
    hint_level: int = 0
    rt_ms: int = 0
    confusion: str | None = None
    xp: int = 0
    corrective_typed: bool = False
    quit: bool = False
    answer_text: str = ""


class QuestionRunner:
    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        session_id: str,
        now: datetime.datetime,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.session_id = session_id
        self.now = now

    # ------------------------------------------------------------ helpers
    def _hint_text(self, kana_id: str, level: int, reverse: bool) -> str:
        kana_obj = self.dataset.kana(kana_id)
        if reverse:
            if level == 1:
                strokes = self.dataset.stroke_orders.get(kana_id, "")
                return f"Kana gồm {len(strokes.split(',')) if strokes else '?'.strip()} nét" if strokes else "Tra cứu thứ tự nét trong Kana Chart."
            if level == 2:
                strokes = self.dataset.stroke_orders.get(kana_id, "")
                return strokes if strokes else "Xem Kana Chart để biết thứ tự nét."
            if level == 3:
                return f"Mnemonic: {self.dataset.mnemonics.get(kana_id, '')}"
            return f"Đáp án là: {kana_id}"
        romaji = kana_obj.romaji
        if level == 1:
            return f"Âm bắt đầu bằng '{romaji[0]}'"
        if level == 2:
            mask = romaji[0] + "_" * max(0, len(romaji) - 1)
            return f"{mask}"
        if level == 3:
            return f"Mnemonic: {self.dataset.mnemonics.get(kana_id, '')}"
        return f"Đáp án là: {romaji}"

    def _corrective_typing(self, kana_id: str, reverse: bool) -> bool:
        """Bắt người chơi gõ lại đáp án đúng. Trả False nếu người chơi thoát."""
        expected = self.dataset.kana(kana_id).romaji
        while True:
            answer = self.ui.read_answer("Gõ lại đáp án đúng > ")
            if answer.kind in ("quit", "eof"):
                return False
            if answer.text == expected:
                self.ui.say("Đã ghi nhận. (Không tính là recall đúng)", style="dim")
                return True
            self.ui.say("Chưa khớp, thử lại.")

    # ------------------------------------------------------------ main
    def run_question(
        self,
        kana_id: str,
        *,
        source: AnswerSource,
        allow_hint: bool = True,
        reverse: bool = False,
        show_command_help: bool = True,
    ) -> QuestionResult:
        kana_obj = self.dataset.kana(kana_id)
        expected = kana_id if reverse else kana_obj.romaji
        if reverse:
            self.ui.show_kana(kana_obj.romaji, sub="Hãy nhập kana (dùng IME hoặc paste)")
            prompt = "Kana > "
        else:
            self.ui.show_kana(kana_id, sub="Nhập romaji, '?' xin gợi ý, 'quit' để thoát")
            prompt = "Romaji > "
        if show_command_help:
            self.ui.say("[dim]Lệnh: '?' = gợi ý | 'quit' = thoát an toàn[/dim]")

        hinted = False
        hint_level = 0
        rt_ms = 0
        answer_text = ""
        while True:
            start = monotonic()
            answer = self.ui.read_answer(prompt)
            rt_ms = max(0, round((monotonic() - start) * 1000))
            if answer.kind == "eof":
                return QuestionResult(kana_id=kana_id, quit=True)
            if answer.kind == "quit":
                return QuestionResult(kana_id=kana_id, quit=True)
            if answer.kind == "hint":
                if not allow_hint:
                    self.ui.say("Ở chế độ này không dùng gợi ý.", style="dim")
                    continue
                hint_level += 1
                hinted = True
                self.ui.feedback_hint(self._hint_text(kana_id, hint_level, reverse))
                if hint_level >= 4:
                    continue
                start = monotonic()
                answer = self.ui.read_answer(prompt)
                rt_ms = max(0, round((monotonic() - start) * 1000))
                if answer.kind == "eof":
                    return QuestionResult(kana_id=kana_id, quit=True)
                if answer.kind == "quit":
                    return QuestionResult(kana_id=kana_id, quit=True)
                if answer.kind == "hint":
                    continue
            answer_text = answer.text
            break

        correct = answer_text == expected
        confusion: str | None = None
        if not correct and not reverse:
            confusion = self.dataset.confusion_target(answer_text)

        is_measurement = source in (AnswerSource.DIAGNOSTIC, AnswerSource.SPEEDRUN)
        corrective_typed = False
        if correct:
            if not is_measurement:
                xp, new_streak = score_answer(True, rt_ms, hinted, self.save.streak)
                self.save.xp += xp
                self.save.streak = new_streak
                self.save.best_streak = max(self.save.best_streak, new_streak)
            else:
                xp = 0
            extra = ""
            if hinted:
                extra = "(dùng gợi ý - không tính là recall tự do)"
            self.ui.feedback_correct(xp, self.save.streak, extra)
        else:
            if not is_measurement:
                self.save.streak = 0
            xp = 0
            if reverse:
                self.ui.feedback_wrong(
                    correct_kana=kana_id,
                    correct_romaji=kana_obj.romaji,
                    lesson_context=source is AnswerSource.LESSON,
                )
            else:
                confused_kana = confusion
                confused_romaji = (
                    self.dataset.kana(confused_kana).romaji if confused_kana else None
                )
                self.ui.feedback_wrong(
                    correct_kana=kana_id,
                    correct_romaji=kana_obj.romaji,
                    confused_kana=confused_kana,
                    confused_romaji=confused_romaji,
                    lesson_context=source is AnswerSource.LESSON,
                )
            if source is AnswerSource.LESSON:
                corrective_typed = self._corrective_typing(kana_id, reverse)

        outcome = self.scheduler.record_result(
            self.save,
            kana_id,
            correct=correct,
            hinted=hinted or corrective_typed,
            rt_ms=rt_ms,
            session_id=self.session_id,
            source=source,
            confusion=confusion,
            now=self.now,
        )
        if outcome.became_mastered:
            self.ui.say("Chúc mừng! Kana này đã đạt MASTERED.", style="bold cyan")
            self.ui.delay()

        return QuestionResult(
            kana_id=kana_id,
            correct=correct,
            hinted=hinted,
            hint_level=hint_level,
            rt_ms=rt_ms,
            confusion=confusion,
            xp=xp,
            corrective_typed=corrective_typed,
            answer_text=answer_text,
        )
