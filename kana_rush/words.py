"""Word Bridge: từ tiếng Nhật ngắn, chỉ mở khi mọi kana đã học."""

from __future__ import annotations

import random

from kana_rush.data import KanaDataset
from kana_rush.models import KanaState, SaveData
from kana_rush.timeutil import utcnow


class NoWordsAvailable(Exception):
    """Chưa đủ kana đã học để có từ khả dụng."""


def word_unlocked(save: SaveData, word: dict) -> bool:
    """Từ chỉ xuất hiện khi tất cả kana cấu thành đã được học (đã giới thiệu)."""
    return all(
        save.card(ch).state is not KanaState.NEW for ch in word["decomposition"]
    )


def available_words(save: SaveData, dataset: KanaDataset) -> list[dict]:
    return [w for w in dataset.words if word_unlocked(save, w)]


def pick_words(
    save: SaveData, dataset: KanaDataset, count: int, rng: random.Random
) -> list[dict]:
    pool = available_words(save, dataset)
    if not pool:
        raise NoWordsAvailable("Chưa có từ khả dụng: hãy học thêm kana trước.")
    rng.shuffle(pool)
    return pool[:count]


def record_word_result(
    save: SaveData,
    word: dict,
    *,
    correct: bool,
    rt_ms: int,
    session_id: str,
) -> None:
    """Ghi kết quả đọc từ RIÊNG với nhận diện ký tự; không tăng mastery kana."""
    entry = {
        "ts": utcnow().isoformat(),
        "session_id": session_id,
        "correct": correct,
        "rt_ms": rt_ms,
    }
    records = save.word_progress.setdefault(word["kana"], [])
    records.append(entry)
    if len(records) > 100:
        save.word_progress[word["kana"]] = records[-100:]


def word_accuracy(save: SaveData, word_kana: str) -> float | None:
    records = save.word_progress.get(word_kana, [])
    if not records:
        return None
    return sum(1 for r in records if r["correct"]) / len(records)
