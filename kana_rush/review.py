"""Review Mode: Quick/Full review với bucket 60/20/10/10, nhiều dạng câu hỏi,
kana sai quay lại sau 2/5/near-end."""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field

from kana_rush.data import KanaDataset
from kana_rush.models import AnswerSource, KanaState, SaveData
from kana_rush.scheduler import (
    QuestionPicker,
    RevisitQueue,
    Scheduler,
    compose_review_pool,
    confusion_count,
)
from kana_rush.scoring import score_answer
from kana_rush.session import QuestionRunner
from kana_rush.timeutil import monotonic
from kana_rush.ui import UI
from kana_rush.words import NoWordsAvailable, available_words, record_word_result

QUICK_SIZE_DEFAULT = 10
FULL_REVIEW_CAP = 50


@dataclass
class ReviewReport:
    mode: str
    questions_asked: int
    correct: int
    total: int
    xp_gained: int
    words_asked: int
    words_correct: int
    mastered_new: list[str] = field(default_factory=list)
    quit_early: bool = False


class ReviewSession:
    def __init__(
        self,
        ui: UI,
        dataset: KanaDataset,
        save: SaveData,
        scheduler: Scheduler,
        rng: random.Random,
        now: datetime.datetime,
        session_id: str,
        mode: str,
        settings: dict,
    ) -> None:
        self.ui = ui
        self.dataset = dataset
        self.save = save
        self.scheduler = scheduler
        self.rng = rng
        self.now = now
        self.session_id = session_id
        self.mode = mode
        self.settings = settings
        self.runner = QuestionRunner(ui, dataset, save, scheduler, session_id, now)
        self.revisits = RevisitQueue()
        self.picker = QuestionPicker(save, rng, now)
        self.position = 0
        self.report = ReviewReport(mode=mode, questions_asked=0, correct=0, total=0, xp_gained=0, words_asked=0, words_correct=0)
        self.last_word: str | None = None
        self._chain_partners: list[str] = []

    # ------------------------------------------------------------ helpers
    def _build_pool(self, size: int) -> list[str]:
        return compose_review_pool(self.save, self.now, size, self.rng)

    def _pick_question_type(self, kana_id: str) -> str:
        """Trả "kana" | "reverse" | "chain" | "comparison"."""
        reverse_enabled = bool(self.settings.get("reverse_mode", False))
        confused = confusion_count(self.save, kana_id) > 0
        if confused and self.rng.random() < 0.3:
            return "comparison"
        if reverse_enabled and self.rng.random() < 0.2:
            return "reverse"
        if self.rng.random() < 0.1:
            return "chain"
        return "kana"

    def _comparison_partner(self, kana_id: str) -> str | None:
        card = self.save.card(kana_id)
        candidates = [
            k
            for k in card.confused_with
            if k != kana_id and self.save.card(k).state is not KanaState.NEW
        ]
        if not candidates:
            for row in self.save.confusion_matrix.values():
                if kana_id in row:
                    candidates.extend([k for k, v in row.items() if v > 0 and k != kana_id])
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return None
        self.rng.shuffle(candidates)
        return candidates[0]

    def _ask_comparison(self, kana_id: str) -> str | None:
        partner = self._comparison_partner(kana_id)
        if partner is None or partner == kana_id:
            return self._ask_kana(kana_id)
        if self.rng.random() < 0.5:
            kana_a, kana_b = kana_id, partner
        else:
            kana_a, kana_b = partner, kana_id
        target = self.rng.choice([kana_a, kana_b])
        romaji = self.dataset.kana(target).romaji
        self.ui.say(
            f"[bold]{kana_a}[/bold]  [dim]|[/dim]  [bold]{kana_b}[/bold]  —  "
            f"Kana nào đọc là [bold yellow]'{romaji}'[/bold yellow]? (1 hoặc 2)"
        )
        start = monotonic()
        answer = self.ui.read_answer("Chọn 1 hoặc 2 > ")
        rt_ms = max(0, round((monotonic() - start) * 1000))
        if answer.kind in ("quit", "eof"):
            self.report.quit_early = True
            return None
        correct = False
        if answer.text == "1":
            correct = kana_a == target
        elif answer.text == "2":
            correct = kana_b == target
        else:
            correct = False
        if correct:
            self.ui.feedback_correct(5, self.save.streak, extra="So sánh đúng!")
            self.save.xp += 5
        else:
            other = kana_b if kana_a == target else kana_a
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
            correct=correct,
            hinted=True,
            rt_ms=rt_ms,
            session_id=self.session_id,
            source=AnswerSource.CONFUSION,
            confusion=None,
            now=self.now,
        )
        return "comparison"

    def _ask_chain(self, kana_id: str) -> str | None:
        partners = [k for k in self.save.cards if k != kana_id and self.save.card(k).state is not KanaState.NEW]
        if len(partners) < 2:
            return self._ask_kana(kana_id)
        partner = self.rng.choice(partners)
        chain = [kana_id, partner]
        self.rng.shuffle(chain)
        self._chain_partners = list(chain)
        romajis = [self.dataset.kana(k).romaji for k in chain]
        self.ui.show_kana(" ".join(chain), sub="Chuỗi kana - nhập cách đọc cả chuỗi")
        expected_forms = {" ".join(romajis), "".join(romajis)}
        start = monotonic()
        answer = self.ui.read_answer("Chuỗi > ")
        rt_ms = max(0, round((monotonic() - start) * 1000))
        if answer.kind in ("quit", "eof"):
            self.report.quit_early = True
            return None
        if answer.kind == "hint":
            self.ui.feedback_hint("Đáp án là: " + " ".join(romajis))
            answer = self.ui.read_answer("Chuỗi > ")
            rt_ms = max(0, round((monotonic() - start) * 1000))
            if answer.kind in ("quit", "eof"):
                self.report.quit_early = True
                return None
            if answer.kind == "hint":
                return self._ask_chain(kana_id)
            correct = answer.text in expected_forms
            hinted = True
        else:
            correct = answer.text in expected_forms
            hinted = False
        if correct:
            xp = 0
            for _ in chain:
                kana_xp, _ = score_answer(True, rt_ms, hinted, self.save.streak)
                xp += kana_xp
            self.save.xp += xp
            self.save.streak += 1
            self.save.best_streak = max(self.save.best_streak, self.save.streak)
            self.ui.feedback_correct(xp, self.save.streak)
        else:
            self.save.streak = 0
            self.ui.feedback_wrong(
                correct_kana=" ".join(chain),
                correct_romaji=" ".join(romajis),
                lesson_context=False,
            )
        for k in chain:
            self.scheduler.record_result(
                self.save,
                k,
                correct=correct,
                hinted=hinted,
                rt_ms=rt_ms,
                session_id=self.session_id,
                source=AnswerSource.REVIEW,
                confusion=None,
                now=self.now,
            )
        return "chain"

    def _ask_kana(self, kana_id: str, reverse: bool = False) -> str | None:
        result = self.runner.run_question(
            kana_id, source=AnswerSource.REVIEW, allow_hint=True, reverse=reverse
        )
        if result.quit:
            self.report.quit_early = True
            return None
        return "kana"

    def _ask_word(self) -> bool:
        try:
            words = available_words(self.save, self.dataset)
        except Exception:
            return False
        words = [w for w in words if w["kana"] != self.last_word]
        if not words:
            return False
        self.rng.shuffle(words)
        word = words[0]
        self.last_word = word["kana"]
        self.ui.show_kana(word["kana"], sub="Từ mới - đọc cả từ")
        start = monotonic()
        answer = self.ui.read_answer("Từ này đọc là > ")
        rt_ms = max(0, round((monotonic() - start) * 1000))
        if answer.kind in ("quit", "eof"):
            self.report.quit_early = True
            return True
        if answer.kind == "hint":
            self.ui.feedback_hint(f"Đọc là: {word['romaji']}")
            self.ui.say(f"Nghĩa: {word['meaning']}", style="dim")
            answer = self.ui.read_answer("Từ này đọc là > ")
            if answer.kind in ("quit", "eof"):
                self.report.quit_early = True
                return True
            correct = answer.text == word["romaji"]
        else:
            correct = answer.text == word["romaji"]
            if correct:
                self.save.streak += 1
                self.save.best_streak = max(self.save.best_streak, self.save.streak)
                self.ui.feedback_correct(10, self.save.streak, extra=f"Nghĩa: {word['meaning']}")
                self.save.xp += 10
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
            self.save, word, correct=correct, rt_ms=rt_ms, session_id=self.session_id
        )
        self.report.words_asked += 1
        if correct:
            self.report.words_correct += 1
        return True

    # ------------------------------------------------------------ run
    def run(self) -> ReviewReport:
        due = self.save.due_ids(self.now)
        if self.mode == "quick":
            size = int(self.settings.get("quick_review_size", QUICK_SIZE_DEFAULT))
            pool = self._build_pool(size)
        else:
            size = min(max(len(due), 5), FULL_REVIEW_CAP)
            pool = self._build_pool(size)
        if not pool:
            self.ui.say("Không có kana đến hạn hoặc cần ôn. Chọn [2] Learn để học mới.", style="dim")
            self.ui.delay()
            return self.report

        self.ui.say(
            f"[bold]REVIEW {self.mode.upper()}[/bold] - {len(pool)} câu cần ôn"
        )
        if self.mode == "quick":
            self.ui.say("[dim]Quick Review: khoảng 10 câu[/dim]")

        main_limit = size if self.mode == "quick" else size + 15
        last_asked: str | None = None
        pool_set = list(pool)
        asked_main = 0
        while pool_set or self.revisits.has_pending():
            if asked_main >= main_limit:
                break
            if self.position > 0 and self.position % 4 == 0:
                if self._ask_word():
                    self.position += 1
                if self.report.quit_early:
                    break
            blocked = self.revisits.blocked(self.position)
            picked = self.picker.pick(pool_set, last_asked=last_asked, blocked=blocked)
            if picked is None:
                if self.revisits.has_pending():
                    self.position += 1
                    continue
                break
            last_asked = picked
            qtype = self._pick_question_type(picked)
            self.position += 1
            asked_main += 1
            if qtype == "comparison":
                outcome = self._ask_comparison(picked)
            elif qtype == "reverse":
                outcome = self._ask_kana(picked, reverse=True)
            elif qtype == "chain":
                outcome = self._ask_chain(picked)
            else:
                outcome = self._ask_kana(picked)
            if self.report.quit_early:
                break
            if outcome is None:
                continue
            consumed = [picked]
            if qtype == "chain":
                consumed = [picked] + self._chain_partners
                self._chain_partners = []
            for k in consumed:
                if k in pool_set:
                    pool_set.remove(k)
            self.report.total += 1
            card = self.save.card(picked)
            if card.last_result() and card.last_result()["correct"]:
                self.report.correct += 1
            if qtype == "kana":
                if not card.last_result()["correct"]:
                    self.revisits.mark_wrong(picked, self.position)
                else:
                    self.revisits.mark_correct(picked)
            if card.state is KanaState.MASTERED and picked not in self.report.mastered_new:
                self.report.mastered_new.append(picked)

        self.ui.show_summary(
            "Kết thúc Review",
            [
                f"Đúng: {self.report.correct}/{self.report.total}",
                f"Từ đã đọc: {self.report.words_correct}/{self.report.words_asked}",
                f"XP nhận được: {self.report.xp_gained}",
            ]
            + (
                [f"Kana mới đạt MASTERED: {', '.join(self.report.mastered_new)}"]
                if self.report.mastered_new
                else []
            ),
        )
        return self.report
