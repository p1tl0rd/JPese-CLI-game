"""Hệ thống 8 lesson cố định: dữ liệu, trạng thái, mở khóa, pool, parser.

Lesson 7 và 8 có 2 subgroup (5 + 3 kana) để tránh quá tải khi học;
subgroup chỉ dùng trong logic Learn, menu vẫn hiển thị Lesson 7/8.
"""

from __future__ import annotations

import datetime
import json
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from kana_rush.data import DATA_DIR, KanaDataset
from kana_rush.models import KanaState, LessonProgress, SaveData
from kana_rush.scheduler import confusion_count

BASIC_LESSON_COUNT = 8
BASIC_HIRAGANA_TOTAL = 46


class LessonStatus(Enum):
    LOCKED = "LOCKED"
    AVAILABLE = "AVAILABLE"
    LEARNING = "LEARNING"
    COMPLETED = "COMPLETED"
    REVIEW_DUE = "REVIEW_DUE"
    MASTERED = "MASTERED"


class LessonDataError(Exception):
    """Dữ liệu lessons.json thiếu hoặc không hợp lệ."""


class LessonSelectionError(Exception):
    """Lựa chọn lesson không hợp lệ (parser / chưa mở khóa)."""


@dataclass(frozen=True)
class Lesson:
    id: int
    name_vi: str
    kana: tuple[str, ...]
    subgroups: tuple[tuple[str, ...], ...]
    order: int

    @property
    def kana_label(self) -> str:
        return "・".join(self.kana)

    @property
    def group_count(self) -> int:
        return len(self.subgroups)


class LessonDataset:
    """8 lesson cố định, validate chéo với dataset hiragana."""

    def __init__(self, data_dir: Path = DATA_DIR, dataset: KanaDataset | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.dataset = dataset or KanaDataset(self.data_dir)
        self.lessons: list[Lesson] = []
        self.by_id: dict[int, Lesson] = {}
        self._load()

    # ------------------------------------------------------------- load
    def _load(self) -> None:
        path = self.data_dir / "lessons.json"
        if not path.exists():
            raise LessonDataError(f"Thiếu file dữ liệu: {path}")
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise LessonDataError(f"Không đọc được {path}: {exc}") from exc

        entries = raw.get("lessons", [])
        if len(entries) != BASIC_LESSON_COUNT:
            raise LessonDataError(
                f"Phải có đúng {BASIC_LESSON_COUNT} lesson, thấy {len(entries)}"
            )

        seen_kana: set[str] = set()
        lessons: list[Lesson] = []
        for entry in sorted(entries, key=lambda e: (e.get("order", e.get("id")), e.get("id"))):
            lesson_id = int(entry.get("id", 0))
            if lesson_id in self.by_id:
                raise LessonDataError(f"Trùng lesson id: {lesson_id}")
            kana = tuple(entry.get("kana", []))
            if not kana:
                raise LessonDataError(f"Lesson {lesson_id} thiếu kana")
            for ch in kana:
                if ch not in self.dataset.by_kana:
                    raise LessonDataError(f"Lesson {lesson_id} chứa kana ngoài dataset: {ch}")
                if ch in seen_kana:
                    raise LessonDataError(f"Kana {ch} xuất hiện ở nhiều lesson")
                seen_kana.add(ch)
            subgroups_raw = entry.get("subgroups")
            if subgroups_raw:
                subgroups = tuple(tuple(g) for g in subgroups_raw)
            else:
                subgroups = (kana,)
            flat = [ch for group in subgroups for ch in group]
            if sorted(flat) != sorted(kana):
                raise LessonDataError(
                    f"Subgroups của lesson {lesson_id} không khớp danh sách kana"
                )
            if len(set(flat)) != len(flat):
                raise LessonDataError(f"Subgroups của lesson {lesson_id} trùng kana")
            if lesson_id in (7, 8) and len(subgroups) != 2:
                raise LessonDataError(
                    f"Lesson {lesson_id} phải có đúng 2 subgroup (5+3 kana)"
                )
            lesson = Lesson(
                id=lesson_id,
                name_vi=entry.get("name_vi", ""),
                kana=kana,
                subgroups=subgroups,
                order=int(entry.get("order", lesson_id)),
            )
            lessons.append(lesson)
            self.by_id[lesson_id] = lesson

        if len(seen_kana) != BASIC_HIRAGANA_TOTAL:
            raise LessonDataError(
                f"Tổng kana của toàn bộ lesson phải là {BASIC_HIRAGANA_TOTAL}, "
                f"thấy {len(seen_kana)}"
            )
        self.lessons = sorted(lessons, key=lambda l: (l.order, l.id))

    def lesson(self, lesson_id: int) -> Lesson:
        return self.by_id[lesson_id]


_LESSON_CACHE: LessonDataset | None = None


def load_lessons(dataset: KanaDataset | None = None) -> LessonDataset:
    """Load lessons (cache theo dataset mặc định)."""
    global _LESSON_CACHE
    if dataset is not None or _LESSON_CACHE is None:
        loaded = LessonDataset(dataset=dataset)
        if dataset is None:
            _LESSON_CACHE = loaded
        return loaded
    return _LESSON_CACHE


# ------------------------------------------------------------- progress

def lesson_progress(save: SaveData, lesson_id: int) -> LessonProgress:
    return save.lesson_progress.setdefault(lesson_id, LessonProgress(lesson_id=lesson_id))


def lesson_cards(save: SaveData, lesson: Lesson) -> list:
    return [save.card(k) for k in lesson.kana]


def lesson_all_promoted(save: SaveData, lesson: Lesson) -> bool:
    """Mọi kana trong lesson đã rời khỏi vòng học (vào REVIEW/MASTERED/RELEARNING)."""
    return all(
        c.state in (KanaState.REVIEW, KanaState.MASTERED, KanaState.RELEARNING)
        for c in lesson_cards(save, lesson)
    )


def lesson_learn_done(save: SaveData, lesson: Lesson) -> bool:
    """Learn Mode của lesson coi như xong: cờ lưu hoặc mọi kana đã vào REVIEW."""
    progress = save.lesson_progress.get(lesson.id)
    if progress is not None and progress.learn_completed:
        return True
    return lesson_all_promoted(save, lesson)


def lesson_unlocked(save: SaveData, lesson_dataset: LessonDataset, lesson_id: int) -> bool:
    """Lesson đầu luôn mở; lesson sau mở khi lesson liền trước đã xong Learn Mode."""
    lessons = lesson_dataset.lessons
    if lesson_id == lessons[0].id:
        return True
    index = next(
        (i for i, l in enumerate(lessons) if l.id == lesson_id),
        None,
    )
    if index is None or index == 0:
        return False
    return lesson_learn_done(save, lessons[index - 1])


def lesson_status(
    save: SaveData,
    lesson_dataset: LessonDataset,
    lesson: Lesson,
    now: datetime.datetime | None = None,
) -> LessonStatus:
    """Trạng thái lesson suy ra từ trạng thái kana (không lưu trạng thái)."""
    if not lesson_unlocked(save, lesson_dataset, lesson.id):
        return LessonStatus.LOCKED
    cards = lesson_cards(save, lesson)
    if all(c.state is KanaState.MASTERED for c in cards):
        return LessonStatus.MASTERED
    if lesson_learn_done(save, lesson):
        if now is not None and any(
            c.state in (KanaState.REVIEW, KanaState.MASTERED, KanaState.RELEARNING)
            and c.next_review_at is not None
            and c.next_review_at <= now
            for c in cards
        ):
            return LessonStatus.REVIEW_DUE
        return LessonStatus.COMPLETED
    if any(c.state is not KanaState.NEW for c in cards):
        return LessonStatus.LEARNING
    return LessonStatus.AVAILABLE


def lesson_introduced_kana(save: SaveData, lesson: Lesson) -> list[str]:
    """Kana của lesson đã được giới thiệu (không nằm ở NEW)."""
    return [k for k in lesson.kana if save.card(k).state is not KanaState.NEW]


def lesson_pool(save: SaveData, lesson: Lesson) -> list[str]:
    """Pool review của một lesson: kana đã giới thiệu, đúng thứ tự trong lesson."""
    return lesson_introduced_kana(save, lesson)


def lesson_accuracy(save: SaveData, lesson: Lesson) -> float | None:
    """Accuracy gần đây gộp của các kana trong lesson; None nếu chưa có dữ liệu."""
    results: list[bool] = []
    for k in lesson.kana:
        card = save.card(k)
        for r in card.recent_results[-10:]:
            results.append(bool(r["correct"]))
    if not results:
        return None
    return sum(results) / len(results)


def lesson_due_count(
    save: SaveData, lesson: Lesson, now: datetime.datetime
) -> int:
    return sum(
        1
        for c in lesson_cards(save, lesson)
        if c.state in (KanaState.REVIEW, KanaState.MASTERED, KanaState.RELEARNING)
        and c.next_review_at is not None
        and c.next_review_at <= now
    )


def lesson_mastered_count(save: SaveData, lesson: Lesson) -> int:
    return sum(1 for c in lesson_cards(save, lesson) if c.state is KanaState.MASTERED)


def next_lesson_to_learn(save: SaveData, lesson_dataset: LessonDataset) -> Lesson | None:
    """Lesson tiếp theo cần học: chưa xong Learn và còn kana chưa vào REVIEW."""
    for lesson in lesson_dataset.lessons:
        if lesson_learn_done(save, lesson):
            continue
        if any(
            save.card(k).state in (KanaState.NEW, KanaState.LEARNING)
            for k in lesson.kana
        ):
            return lesson
    return None


# ------------------------------------------------------------- pools

def learned_pool(save: SaveData, lesson_dataset: LessonDataset) -> list[str]:
    """Pool Random/Smart: kana đã giới thiệu trong lesson đã mở khóa.

    Chỉ lấy kana LEARNING/REVIEW/RELEARNING/MASTERED đã được giới thiệu;
    tuyệt đối không lấy kana NEW hoặc thuộc lesson LOCKED.
    """
    pool: list[str] = []
    for lesson in lesson_dataset.lessons:
        if not lesson_unlocked(save, lesson_dataset, lesson.id):
            continue
        for k in lesson.kana:
            card = save.card(k)
            if (
                card.introduced_at is not None
                and card.state
                in (KanaState.LEARNING, KanaState.REVIEW, KanaState.RELEARNING, KanaState.MASTERED)
            ):
                pool.append(k)
    return pool


def srs_pool(
    save: SaveData,
    now: datetime.datetime,
    rng: random.Random | None = None,
) -> list[str]:
    """Pool SRS Recommended: toàn bộ kana đến hạn (quá hạn lâu trước)
    + thêm một phần kana vừa trả lời sai."""
    due = [
        k
        for k, c in save.cards.items()
        if c.state in (KanaState.LEARNING, KanaState.REVIEW, KanaState.MASTERED, KanaState.RELEARNING)
        and c.next_review_at is not None
        and c.next_review_at <= now
    ]
    due.sort(key=lambda k: save.cards[k].next_review_at or now)
    recently_wrong = [
        k
        for k, c in save.cards.items()
        if c.state is not KanaState.NEW
        and k not in due
        and c.last_result() is not None
        and not c.last_result()["correct"]
    ]
    rng = rng or random.Random()
    rng.shuffle(recently_wrong)
    extra = recently_wrong[: max(2, len(due) // 5)]
    return due + extra


def srs_lesson_breakdown(
    save: SaveData, lesson_dataset: LessonDataset, pool: list[str]
) -> list[tuple[Lesson, int]]:
    """Số kana trong pool theo từng lesson (chỉ hiển thị, không sắp theo lesson)."""
    in_pool = set(pool)
    return [
        (lesson, sum(1 for k in lesson.kana if k in in_pool))
        for lesson in lesson_dataset.lessons
    ]


def multi_lesson_pool(save: SaveData, lesson_dataset: LessonDataset, lesson_ids: list[int]) -> list[str]:
    """Pool gộp từ nhiều lesson: chỉ chứa kana của các lesson được chọn."""
    pool: list[str] = []
    for lesson_id in sorted(set(lesson_ids)):
        lesson = lesson_dataset.by_id.get(lesson_id)
        if lesson is None:
            continue
        pool.extend(lesson_pool(save, lesson))
    return pool


def reviewable_lessons(save: SaveData, lesson_dataset: LessonDataset) -> list[Lesson]:
    """Lesson có thể review: đã bắt đầu hoặc đã hoàn thành (không phải LOCKED)."""
    result = []
    for lesson in lesson_dataset.lessons:
        if not lesson_unlocked(save, lesson_dataset, lesson.id):
            continue
        if any(save.card(k).state is not KanaState.NEW for k in lesson.kana):
            result.append(lesson)
    return result


def check_lessons_reviewable(
    save: SaveData, lesson_dataset: LessonDataset, lesson_ids: list[int]
) -> None:
    """Raise LessonSelectionError nếu có lesson chưa mở khóa / chưa bắt đầu."""
    for lesson_id in sorted(set(lesson_ids)):
        lesson = lesson_dataset.by_id.get(lesson_id)
        if lesson is None:
            raise LessonSelectionError(f"Lesson {lesson_id} không tồn tại.")
        if not lesson_unlocked(save, lesson_dataset, lesson_id):
            raise LessonSelectionError(f"Lesson {lesson_id} chưa mở khóa.")
        if not any(save.card(k).state is not KanaState.NEW for k in lesson.kana):
            raise LessonSelectionError(f"Lesson {lesson_id} chưa bắt đầu.")


# ------------------------------------------------------------- parser

def parse_lesson_selection(text: str, max_id: int = BASIC_LESSON_COUNT) -> list[int]:
    """Parse cú pháp chọn lesson: '1,3,5', '1-4', '1,3-6', 'all'.

    Loại bỏ trùng, sắp xếp theo ID, raise LessonSelectionError nếu không hợp lệ.
    """
    if text is None:
        raise LessonSelectionError("Chưa nhập lựa chọn lesson.")
    text = text.strip().lower()
    if not text:
        raise LessonSelectionError("Chưa nhập lựa chọn lesson.")
    if text == "all":
        return list(range(1, max_id + 1))
    ids: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise LessonSelectionError(f"Phần '{part}' trống.")
        if "-" in part:
            try:
                start, end = (int(x.strip()) for x in part.split("-", 1))
            except ValueError as exc:
                raise LessonSelectionError(f"'{part}' không phải range hợp lệ.") from exc
            if start > end:
                raise LessonSelectionError(f"Range '{part}' phải theo thứ tự tăng dần (a-b).")
            if start < 1 or end > max_id:
                raise LessonSelectionError(f"Range '{part}' nằm ngoài phạm vi 1-{max_id}.")
            ids.update(range(start, end + 1))
        else:
            try:
                value = int(part)
            except ValueError as exc:
                raise LessonSelectionError(f"'{part}' không phải số lesson hợp lệ.") from exc
            if value < 1 or value > max_id:
                raise LessonSelectionError(f"Lesson {value} không tồn tại (1-{max_id}).")
            ids.add(value)
    if not ids:
        raise LessonSelectionError("Chưa chọn lesson nào.")
    return sorted(ids)


# ------------------------------------------------------------- smart random

def smart_weight(card, save: SaveData, kana_id: str, now: datetime.datetime) -> float:
    """Trọng số ưu tiên kana yếu trong Smart Random (càng cao càng dễ được hỏi)."""
    weight = 1.0
    last = card.last_result()
    if last is not None and not last["correct"]:
        weight += 5.0  # recently wrong
    if card.state is KanaState.RELEARNING:
        weight += 4.0
    weight += (1.0 - card.mastery_score) * 3.0  # low mastery
    median = card.median_rt()
    if median is not None and median > 2500:
        weight += 2.0  # slow response
    weight += min(3.0, confusion_count(save, kana_id) * 2.0)  # common confusion
    if card.next_review_at is not None and card.next_review_at <= now:
        weight += 4.0  # due or overdue
    return weight


def smart_pick(
    pool: list[str],
    save: SaveData,
    rng: random.Random,
    now: datetime.datetime,
    last_asked: str | None = None,
) -> str:
    """Chọn kana theo trọng số (vẫn có yếu tố random; deterministic theo seed)."""
    candidates = [k for k in pool if k != last_asked]
    if not candidates:
        candidates = list(pool)
    weights = [smart_weight(save.card(k), save, k, now) for k in candidates]
    return rng.choices(candidates, weights=weights, k=1)[0]


class RoundRobinDeck:
    """Trộn pool thành vòng; mỗi vòng hỏi đủ pool trước khi tạo vòng mới;
    không lặp kana liên tiếp (kể cả giữa hai vòng)."""

    def __init__(self, pool: list[str], rng: random.Random) -> None:
        self.pool = list(pool)
        self.rng = rng
        self._round: list[str] = []
        self._last_drawn: str | None = None

    def _new_round(self) -> None:
        rnd = list(self.pool)
        self.rng.shuffle(rnd)
        if (
            self._last_drawn is not None
            and len(rnd) > 1
            and rnd[0] == self._last_drawn
        ):
            rnd[0], rnd[1] = rnd[1], rnd[0]
        self._round = rnd

    def draw(self) -> str | None:
        if not self.pool:
            return None
        if not self._round:
            self._new_round()
        kana = self._round.pop(0)
        self._last_drawn = kana
        return kana
