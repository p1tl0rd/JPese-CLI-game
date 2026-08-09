"""Models: trạng thái học, dữ liệu kana và progress của người chơi."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum

from kana_rush.timeutil import utcnow

SCHEMA_VERSION = 1

# Lịch review theo review_stage:
#   0 = cuối lesson, 1 = cuối ngày (10-30 phút/ngày), 2 = 1 ngày,
#   3 = 3 ngày, 4 = 7 ngày, 5 = 14 ngày, 6 = 30 ngày.
REVIEW_STAGE_INTERVALS: tuple[datetime.timedelta, ...] = (
    datetime.timedelta(minutes=0),
    datetime.timedelta(hours=8),
    datetime.timedelta(days=1),
    datetime.timedelta(days=3),
    datetime.timedelta(days=7),
    datetime.timedelta(days=14),
    datetime.timedelta(days=30),
)
MAX_REVIEW_STAGE = len(REVIEW_STAGE_INTERVALS) - 1

RECENT_RESULTS_LIMIT = 30
RESPONSE_TIMES_LIMIT = 200


class KanaState(Enum):
    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    MASTERED = "mastered"
    RELEARNING = "relearning"


class AnswerSource(Enum):
    """Nơi sinh ra câu trả lời, ảnh hưởng cách scheduler xử lý."""

    LESSON = "lesson"
    REVIEW = "review"
    MIXED = "mixed"
    DIAGNOSTIC = "diagnostic"
    CONFUSION = "confusion"
    SPEEDRUN = "speedrun"
    RANDOM = "random"


class HintLevel(Enum):
    NONE = 0
    FIRST_SOUND = 1
    LETTER_MASK = 2
    MNEMONIC = 3
    SHOW_ANSWER = 4


@dataclass(frozen=True)
class Kana:
    """Kana cố định, load từ data/hiragana.json."""

    kana: str
    romaji: str  # canonical, ví dụ "shi"
    aliases: tuple[str, ...]
    row: int
    col: int

    def all_readings(self) -> set[str]:
        return {self.romaji, *self.aliases}


@dataclass
class KanaCard:
    """Progress của một kana."""

    state: KanaState = KanaState.NEW
    learning_step: int = 0  # 0 chưa giới thiệu, 1 encode, 2 first retrieval, 3 mixed recall
    review_stage: int = 0
    next_review_at: datetime.datetime | None = None
    last_reviewed_at: datetime.datetime | None = None
    introduced_at: datetime.datetime | None = None
    correct_unaided: int = 0
    correct_with_hint: int = 0
    wrong_count: int = 0
    lapse_count: int = 0
    session_ids_correct: list[str] = field(default_factory=list)
    recent_results: list[dict] = field(default_factory=list)
    response_times_ms: list[int] = field(default_factory=list)
    confused_with: dict[str, int] = field(default_factory=dict)
    mastery_score: float = 0.0
    stability: float = 1.0
    difficulty: float = 0.5

    def append_result(
        self,
        *,
        correct: bool,
        hinted: bool,
        rt_ms: int,
        session_id: str,
        source: AnswerSource,
        confusion: str | None = None,
        now: datetime.datetime | None = None,
    ) -> None:
        now = now or utcnow()
        if correct:
            if hinted:
                self.correct_with_hint += 1
            else:
                self.correct_unaided += 1
                if session_id not in self.session_ids_correct:
                    self.session_ids_correct.append(session_id)
        else:
            self.wrong_count += 1
        self.recent_results.append(
            {
                "ts": now.isoformat(),
                "session_id": session_id,
                "correct": correct,
                "hinted": hinted,
                "rt_ms": rt_ms,
                "confusion": confusion,
                "source": source.value,
            }
        )
        if len(self.recent_results) > RECENT_RESULTS_LIMIT:
            self.recent_results = self.recent_results[-RECENT_RESULTS_LIMIT:]
        self.response_times_ms.append(rt_ms)
        if len(self.response_times_ms) > RESPONSE_TIMES_LIMIT:
            self.response_times_ms = self.response_times_ms[-RESPONSE_TIMES_LIMIT:]
        self.last_reviewed_at = now

    def recent_accuracy(self, n: int = 10) -> float | None:
        """Tỷ lệ đúng trong n kết quả gần nhất; None nếu chưa có kết quả."""
        results = self.recent_results[-n:]
        if not results:
            return None
        return sum(1 for r in results if r["correct"]) / len(results)

    def last_result(self) -> dict | None:
        return self.recent_results[-1] if self.recent_results else None

    def median_rt(self, limit: int = 10) -> float | None:
        """Median response time của các lần gần nhất (không tính lúc hint)."""
        times = sorted(self.response_times_ms[-limit:])
        if not times:
            return None
        mid = len(times) // 2
        if len(times) % 2 == 1:
            return float(times[mid])
        return (times[mid - 1] + times[mid]) / 2.0


@dataclass
class Outcome:
    """Kết quả một câu trả lời sau khi scheduler xử lý."""

    state_before: KanaState
    state_after: KanaState
    review_stage: int
    next_review_at: datetime.datetime
    became_mastered: bool = False
    correct: bool = False


@dataclass
class LessonProgress:
    """Tiến độ riêng của một lesson (trạng thái suy ra từ kana khi có thể)."""

    lesson_id: int
    introduced_kana: list[str] = field(default_factory=list)
    completed_subgroups: list[int] = field(default_factory=list)
    learn_completed: bool = False
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    last_practiced_at: datetime.datetime | None = None
    total_attempts: int = 0
    accuracy: float = 0.0


@dataclass
class SaveData:
    schema_version: int = SCHEMA_VERSION
    created_at: datetime.datetime = field(default_factory=utcnow)
    updated_at: datetime.datetime = field(default_factory=utcnow)
    session_id: str = ""
    session_count: int = 0
    total_study_seconds: float = 0.0
    xp: int = 0
    streak: int = 0  # chuỗi câu trả lời đúng liên tiếp
    best_streak: int = 0
    day_streak: int = 0  # chuỗi ngày học liên tiếp (tách riêng khỏi answer streak)
    last_active_date: str | None = None  # "YYYY-MM-DD" giờ địa phương
    cards: dict[str, KanaCard] = field(default_factory=dict)
    confusion_matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    word_progress: dict[str, list[dict]] = field(default_factory=dict)
    lesson_progress: dict[int, LessonProgress] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    achievements: list[str] = field(default_factory=list)
    best_speedrun_score: int = 0
    diagnostic_done: bool = False

    def card(self, kana: str) -> KanaCard:
        return self.cards.setdefault(kana, KanaCard())

    def due_ids(self, now: datetime.datetime) -> list[str]:
        return [
            k
            for k, c in self.cards.items()
            if c.state in (KanaState.REVIEW, KanaState.MASTERED, KanaState.RELEARNING)
            and c.next_review_at is not None
            and c.next_review_at <= now
        ]
