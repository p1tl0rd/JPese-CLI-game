"""Scheduler: spaced repetition, state transitions, mastery, picker, revisits.

Minh bạch và deterministic (nhận rng/now tường minh, không dùng global).
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass

from kana_rush.data import KanaDataset
from kana_rush.models import (
    AnswerSource,
    KanaCard,
    KanaState,
    MAX_REVIEW_STAGE,
    Outcome,
    REVIEW_STAGE_INTERVALS,
    SaveData,
    utcnow,
)

FAST_MS = 2000
SLOW_MS = 5000
OVERDUE_FACTOR = 1.5
MASTERED_INTERVAL = REVIEW_STAGE_INTERVALS[MAX_REVIEW_STAGE]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_mastery_score(card: KanaCard) -> float:
    recent_acc = card.recent_accuracy(10) or 0.0
    score = 0.3 * min(1.0, card.correct_unaided / 10)
    score += 0.4 * recent_acc
    score += 0.2 * min(1.0, card.stability / 5)
    score -= 0.1 * card.difficulty
    score -= 0.05 * card.lapse_count
    return _clamp(score, 0.0, 1.0)


def is_overdue(card: KanaCard, now: datetime.datetime) -> bool:
    """Quá hạn nếu đã trễ hơn 1.5 lần interval đáng lẽ."""
    if card.next_review_at is None:
        return False
    interval = REVIEW_STAGE_INTERVALS[card.review_stage]
    if interval.total_seconds() <= 0:
        return now > card.next_review_at
    return now > card.next_review_at + interval * OVERDUE_FACTOR


def _has_delayed_recall(card: KanaCard, now: datetime.datetime) -> bool:
    """Có ít nhất một lần recall đúng (không hint) sau 24 giờ kể từ khi giới thiệu."""
    if card.introduced_at is None:
        return False
    cutoff = card.introduced_at + datetime.timedelta(days=1)
    return any(
        r["correct"] and not r["hinted"]
        for r in card.recent_results
        if _parse_ts(r["ts"]) >= cutoff
    )


def _parse_ts(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value)


def _unrecovered_error(card: KanaCard) -> bool:
    """Có lỗi gần đây chưa được phục hồi (sau lỗi cuối chưa có lần đúng nào)."""
    results = card.recent_results
    for index in range(len(results) - 1, -1, -1):
        if not results[index]["correct"]:
            return not any(r["correct"] for r in results[index + 1:])
    return False


def mastery_eligible(card: KanaCard, now: datetime.datetime) -> bool:
    """Toàn bộ điều kiện MASTERED (xem README / spec §6)."""
    if card.state is not KanaState.REVIEW:
        return False
    if card.correct_unaided < 6:
        return False
    if len(card.session_ids_correct) < 3:
        return False
    if not _has_delayed_recall(card, now):
        return False
    recent_acc = card.recent_accuracy(10)
    if recent_acc is None or recent_acc < 0.85:
        return False
    correct_rts = [
        r["rt_ms"] for r in card.recent_results if r["correct"] and not r["hinted"]
    ]
    if len(correct_rts) < 3:
        return False
    if sorted(correct_rts)[len(correct_rts) // 2] > 2000:
        return False
    last = card.last_result()
    if last is None or not last["correct"] or last["hinted"]:
        return False
    if _unrecovered_error(card):
        return False
    return True


def _advance_stage(card: KanaCard, hinted: bool, rt_ms: int, overdue: bool) -> int:
    if hinted:
        return card.review_stage
    stage = card.review_stage
    if rt_ms < FAST_MS:
        stage += 2
    elif rt_ms <= SLOW_MS:
        stage += 1
    if overdue and rt_ms <= SLOW_MS:
        stage += 1
    return min(stage, MAX_REVIEW_STAGE)


def _update_scores(card: KanaCard, correct: bool, hinted: bool, rt_ms: int, confusion: bool) -> None:
    if correct:
        if hinted:
            card.stability += 0.05
        elif rt_ms < FAST_MS:
            card.stability += 0.3
        else:
            card.stability += 0.2
        card.difficulty = max(0.05, card.difficulty - 0.03)
    else:
        card.stability *= 0.35 if confusion else 0.5
        card.difficulty = min(0.95, card.difficulty + (0.15 if confusion else 0.1))
    card.mastery_score = compute_mastery_score(card)


class Scheduler:
    def __init__(self, dataset: KanaDataset) -> None:
        self.dataset = dataset

    def record_result(
        self,
        save: SaveData,
        kana_id: str,
        *,
        correct: bool,
        hinted: bool,
        rt_ms: int,
        session_id: str,
        source: AnswerSource,
        confusion: str | None = None,
        now: datetime.datetime | None = None,
    ) -> Outcome:
        now = now or utcnow()
        card = save.card(kana_id)
        state_before = card.state

        card.append_result(
            correct=correct,
            hinted=hinted,
            rt_ms=rt_ms,
            session_id=session_id,
            source=source,
            confusion=confusion,
            now=now,
        )

        if not correct and confusion:
            self._record_confusion(save, kana_id, confusion)

        _update_scores(card, correct, hinted, rt_ms, confusion is not None)

        if source in (AnswerSource.DIAGNOSTIC, AnswerSource.SPEEDRUN):
            # Diagnostic/Speed Run chỉ đo lường, không thay đổi lịch SRS.
            return Outcome(
                state_before=state_before,
                state_after=state_before,
                review_stage=card.review_stage,
                next_review_at=card.next_review_at or now,
                correct=correct,
            )

        if source is AnswerSource.RANDOM:
            # Random Review: kết quả vẫn vào lịch sử/thống kê, nhưng chỉ
            # cập nhật lịch SRS khi kana thực sự đến hạn (tránh cày random
            # đẩy lịch review đi quá xa).
            due_now = card.next_review_at is not None and card.next_review_at <= now
            if not due_now:
                return Outcome(
                    state_before=state_before,
                    state_after=state_before,
                    review_stage=card.review_stage,
                    next_review_at=card.next_review_at or now,
                    correct=correct,
                )

        state_after, stage, next_review = self._transition(
            save, card, correct=correct, hinted=hinted, rt_ms=rt_ms, now=now
        )
        became_mastered = state_before is KanaState.REVIEW and state_after is KanaState.MASTERED
        return Outcome(
            state_before=state_before,
            state_after=state_after,
            review_stage=stage,
            next_review_at=next_review,
            became_mastered=became_mastered,
            correct=correct,
        )

    def _transition(
        self,
        save: SaveData,
        card: KanaCard,
        *,
        correct: bool,
        hinted: bool,
        rt_ms: int,
        now: datetime.datetime,
    ) -> tuple[KanaState, int, datetime.datetime]:
        overdue = is_overdue(card, now)
        if correct:
            if card.state is KanaState.NEW:
                card.state = KanaState.LEARNING
                card.learning_step = max(card.learning_step, 1)
            elif card.state is KanaState.LEARNING:
                card.learning_step = min(3, card.learning_step + 1)
            elif card.state is KanaState.RELEARNING:
                card.state = KanaState.REVIEW
                card.review_stage = 0
            elif card.state is KanaState.MASTERED:
                card.review_stage = MAX_REVIEW_STAGE
            elif card.state is KanaState.REVIEW:
                card.review_stage = _advance_stage(card, hinted, rt_ms, overdue)
                if mastery_eligible(card, now):
                    card.state = KanaState.MASTERED
        else:
            if card.state in (KanaState.REVIEW, KanaState.MASTERED):
                card.state = KanaState.RELEARNING
                card.lapse_count += 1
                card.review_stage = max(0, card.review_stage - 2)

        if card.state is KanaState.REVIEW or card.state is KanaState.MASTERED:
            interval = REVIEW_STAGE_INTERVALS[card.review_stage]
            next_review = now + interval
            if card.state is KanaState.MASTERED:
                next_review = now + MASTERED_INTERVAL
        elif card.state is KanaState.RELEARNING:
            next_review = now
        else:
            next_review = now  # LEARNING: lesson tự xử lý spacing; due ngay nếu cần

        card.next_review_at = next_review
        return card.state, card.review_stage, next_review

    @staticmethod
    def _record_confusion(save: SaveData, given: str, mistyped: str) -> None:
        row = save.confusion_matrix.setdefault(given, {})
        row[mistyped] = row.get(mistyped, 0) + 1
        card = save.card(given)
        card.confused_with[mistyped] = card.confused_with.get(mistyped, 0) + 1

    def introduce(self, save: SaveData, kana_id: str, now: datetime.datetime | None = None) -> None:
        """Chuyển NEW -> LEARNING, đánh dấu đã giới thiệu (encode xong)."""
        now = now or utcnow()
        card = save.card(kana_id)
        if card.state is KanaState.NEW:
            card.state = KanaState.LEARNING
            card.learning_step = max(card.learning_step, 1)
            card.introduced_at = now
            card.next_review_at = now

    def promote_to_review(
        self,
        save: SaveData,
        kana_id: str,
        stage: int = 0,
        now: datetime.datetime | None = None,
    ) -> None:
        """Đưa kana vào REVIEW (lesson hoàn thành hoặc diagnostic)."""
        now = now or utcnow()
        card = save.card(kana_id)
        card.state = KanaState.REVIEW
        card.review_stage = max(0, min(stage, MAX_REVIEW_STAGE))
        if card.introduced_at is None:
            card.introduced_at = now
        card.next_review_at = now + REVIEW_STAGE_INTERVALS[card.review_stage]


def confusion_count(save: SaveData, kana_id: str) -> int:
    given = sum(save.confusion_matrix.get(kana_id, {}).values())
    taken = sum(
        1 for row in save.confusion_matrix.values() for k in row if k == kana_id
    )
    return given + taken


def pick_priority(card: KanaCard, save: SaveData, kana_id: str, now: datetime.datetime) -> float:
    """Điểm ưu tiên càng cao càng được hỏi trước."""
    score = 0.0
    if card.next_review_at is not None and card.next_review_at <= now:
        overdue_hours = max(0.0, (now - card.next_review_at).total_seconds() / 3600)
        score += 500.0 + min(50.0, overdue_hours)
    last = card.last_result()
    if last is not None and not last["correct"]:
        score += 80.0
    elif last is not None and not last["hinted"] and last["correct"]:
        score += 10.0
    if len(card.recent_results) >= 3:
        recent = card.recent_results[-3:]
        if any(not r["correct"] for r in recent):
            score += 40.0
    score += max(0.0, 2.0 - card.stability) * 30.0
    score += (1.0 - card.mastery_score) * 40.0
    score += min(100.0, confusion_count(save, kana_id) * 15.0)
    median = card.median_rt()
    if median is not None and median > 2500:
        score += 25.0
    if card.state is KanaState.LEARNING and card.learning_step < 3:
        score += 15.0
    return score


@dataclass
class PickConfig:
    jitter: float = 20.0


class QuestionPicker:
    """Chọn kana tiếp theo trong một pool; không hỏi lặp liên tiếp khi còn lựa chọn."""

    def __init__(
        self,
        save: SaveData,
        rng: random.Random,
        now: datetime.datetime | None = None,
        config: PickConfig | None = None,
    ) -> None:
        self.save = save
        self.rng = rng
        self.now = now or utcnow()
        self.config = config or PickConfig()

    def pick(
        self,
        pool: list[str],
        *,
        last_asked: str | None = None,
        blocked: set[str] | None = None,
    ) -> str | None:
        candidates = [k for k in pool if k != last_asked and (blocked is None or k not in blocked)]
        if not candidates:
            return None
        scored = [
            (
                pick_priority(self.save.card(k), self.save, k, self.now)
                + self.rng.random() * self.config.jitter,
                k,
            )
            for k in candidates
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]


class RevisitQueue:
    """Đưa kana sai quay lại sau 2 câu, rồi sau 5 câu, rồi gần cuối; tối đa 3 lần."""

    def __init__(self) -> None:
        self._plan: dict[str, dict] = {}

    def mark_wrong(self, kana_id: str, position: int) -> None:
        entry = self._plan.get(kana_id, {"wrongs": 0})
        entry["wrongs"] += 1
        wrongs = entry["wrongs"]
        if wrongs == 1:
            entry["next_ask"] = position + 2
        elif wrongs == 2:
            entry["next_ask"] = position + 5
        elif wrongs == 3:
            entry["next_ask"] = position + 12
        else:
            entry["next_ask"] = None  # dừng hỏi lại trong phiên này
        self._plan[kana_id] = entry

    def mark_correct(self, kana_id: str) -> None:
        self._plan.pop(kana_id, None)

    def blocked(self, position: int) -> set[str]:
        """Items đang chờ spacing (next_ask trong tương lai) hoặc bị tạm dừng."""
        return {
            k
            for k, e in self._plan.items()
            if e["next_ask"] is None or e["next_ask"] > position
        }

    def has_pending(self) -> bool:
        return any(e["next_ask"] is not None for e in self._plan.values())


def compose_review_pool(
    save: SaveData,
    now: datetime.datetime,
    size: int,
    rng: random.Random,
    *,
    include_mastered: bool = True,
) -> list[str]:
    """60% due, 20% yếu, 10% confusion, 10% ổn định; bù chéo khi thiếu."""
    from kana_rush.models import KanaState

    eligible = [
        k
        for k, c in save.cards.items()
        if c.state is not KanaState.NEW
        and (include_mastered or c.state is not KanaState.MASTERED)
    ]

    def is_weak(kana_id: str) -> bool:
        card = save.cards.get(kana_id)
        if card is None:
            return False
        if card.state is KanaState.RELEARNING:
            return True
        last = card.last_result()
        if last is not None and not last["correct"]:
            return True
        acc = card.recent_accuracy(10)
        return acc is not None and acc < 0.8

    def is_confused(kana_id: str) -> bool:
        return confusion_count(save, kana_id) > 0

    def is_stable(kana_id: str) -> bool:
        card = save.cards[kana_id]
        return card.state in (KanaState.REVIEW, KanaState.MASTERED) and not is_weak(kana_id)

    due = sorted(
        [k for k in eligible if save.cards[k].next_review_at is not None and save.cards[k].next_review_at <= now],
        key=lambda k: (save.cards[k].next_review_at or now).isoformat(),
    )
    weak = [k for k in eligible if is_weak(k) and k not in due]
    confused = [k for k in eligible if is_confused(k) and k not in due and k not in weak]
    stable = [k for k in eligible if is_stable(k) and k not in due and k not in weak and k not in confused]

    n_due = round(size * 0.6)
    n_weak = round(size * 0.2)
    n_conf = round(size * 0.1)
    n_stable = size - n_due - n_weak - n_conf

    selected: list[str] = []
    selected += due[:n_due]
    if len(selected) < n_due:
        for bucket in (weak, confused, stable):
            selected += bucket[: n_due - len(selected)]
    selected += [k for k in weak if k not in selected][:n_weak]
    if len(selected) < n_due + n_weak:
        for bucket in (confused, stable, due):
            selected += [k for k in bucket if k not in selected][: n_due + n_weak - len(selected)]
    selected += [k for k in confused if k not in selected][:n_conf]
    if len(selected) < n_due + n_weak + n_conf:
        for bucket in (stable, due, weak):
            selected += [k for k in bucket if k not in selected][: n_due + n_weak + n_conf - len(selected)]
    selected += [k for k in stable if k not in selected][:n_stable]
    if len(selected) < size:
        for bucket in (due, weak, confused):
            selected += [k for k in bucket if k not in selected][: size - len(selected)]

    rng.shuffle(selected)
    return selected


def adaptive_new_count(save: SaveData, *, default: int = 5, max_new: int = 7) -> int:
    """Số kana mới thích nghi theo accuracy gần đây: <75% -> 3; 75-90% -> 5; >90% -> 7."""
    results: list[bool] = []
    for card in save.cards.values():
        for r in card.recent_results[-5:]:
            results.append(bool(r["correct"]))
    if not results:
        return default
    accuracy = sum(results) / len(results)
    if accuracy < 0.75:
        return 3
    if accuracy <= 0.90:
        return default
    return min(max_new, default + 2)
