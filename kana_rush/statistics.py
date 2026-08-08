"""Statistics: tổng hợp từ SaveData. Không bịa số khi thiếu dữ liệu."""

from __future__ import annotations

import datetime
from collections import Counter

from kana_rush.models import KanaState, SaveData
from kana_rush.scheduler import confusion_count, is_overdue
from kana_rush.timeutil import parse_iso

RETENTION_MIN_SAMPLES = 5


def _results_between(save: SaveData, start: datetime.datetime, end: datetime.datetime | None = None):
    for kana_id, card in save.cards.items():
        for r in card.recent_results:
            ts = parse_iso(r["ts"])
            if ts >= start and (end is None or ts <= end):
                yield kana_id, r


def state_counts(save: SaveData) -> dict[KanaState, int]:
    counts = {s: 0 for s in KanaState}
    for card in save.cards.values():
        counts[card.state] += 1
    return counts


def due_count(save: SaveData, now: datetime.datetime) -> int:
    return len(save.due_ids(now))


def accuracy_between(save: SaveData, start: datetime.datetime) -> float | None:
    results = list(_results_between(save, start))
    if not results:
        return None
    return sum(1 for _, r in results if r["correct"]) / len(results)


def introduced_count(save: SaveData) -> int:
    return sum(1 for c in save.cards.values() if c.introduced_at)


def _review_gap_records(save: SaveData, min_gap_hours: float) -> list[tuple[str, bool]]:
    """Lấy lần review sau khoảng cách >= min_gap_hours kể từ lần trước đó.

    Mỗi cặp (lần trước, lần sau) cùng kana và cách nhau đủ lâu:
    trả (kana, lần_sau đúng hay không) -> dùng ước lượng retention.
    """
    samples: list[tuple[str, bool]] = []
    for kana_id, card in save.cards.items():
        times = []
        for r in card.recent_results:
            ts = parse_iso(r["ts"])
            if r["source"] != "speedrun":
                times.append((ts, bool(r["correct"])))
        times.sort()
        for previous, current in zip(times, times[1:]):
            gap = (current[0] - previous[0]).total_seconds() / 3600.0
            if gap >= min_gap_hours:
                samples.append((kana_id, current[1]))
    return samples


def estimated_retention(save: SaveData, hours: float) -> float | None:
    """Tỷ lệ recall đúng sau khoảng cách >= hours. None nếu chưa đủ dữ liệu."""
    samples = _review_gap_records(save, hours)
    if len(samples) < RETENTION_MIN_SAMPLES:
        return None
    return sum(1 for _, correct in samples if correct) / len(samples)


def median_recall_time(save: SaveData) -> float | None:
    times = [
        r["rt_ms"]
        for card in save.cards.values()
        for r in card.recent_results
        if r["correct"] and not r["hinted"]
    ]
    if not times:
        return None
    times.sort()
    mid = len(times) // 2
    return float(times[mid]) if len(times) % 2 else (times[mid - 1] + times[mid]) / 2


def weakest_kanas(save: SaveData, limit: int = 5) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    for kana_id, card in save.cards.items():
        if card.state is KanaState.NEW:
            continue
        acc = card.recent_accuracy(10)
        if acc is None:
            acc = 0.0 if card.wrong_count else 1.0
        ranked.append((kana_id, acc))
    ranked.sort(key=lambda pair: (pair[1], -card_strength(save.cards[pair[0]])))
    return ranked[:limit]


def card_strength(card) -> float:
    return card.correct_unaided + card.stability * 2 - card.lapse_count * 5


def slowest_kanas(save: SaveData, limit: int = 5) -> list[tuple[str, float]]:
    ranked = [
        (kana_id, card.median_rt())
        for kana_id, card in save.cards.items()
        if card.median_rt() is not None
    ]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:limit]


def top_confusions(save: SaveData, limit: int = 5) -> list[tuple[str, str, int]]:
    pairs: list[tuple[str, str, int]] = []
    for given, row in save.confusion_matrix.items():
        for mistyped, count in row.items():
            pairs.append((given, mistyped, count))
    pairs.sort(key=lambda p: p[2], reverse=True)
    return pairs[:limit]


def total_unaided_corrects(save: SaveData) -> int:
    return sum(c.correct_unaided for c in save.cards.values())


def total_hints_used(save: SaveData) -> int:
    return sum(c.correct_with_hint for c in save.cards.values())


def recent_answers(save: SaveData, n: int = 200) -> list[tuple[str, dict]]:
    items = [
        (kana_id, r)
        for kana_id, card in save.cards.items()
        for r in card.recent_results
    ]
    items.sort(key=lambda pair: pair[1]["ts"])
    return items[-n:]


def overall_accuracy(save: SaveData) -> float | None:
    items = recent_answers(save, 200)
    if not items:
        return None
    return sum(1 for _, r in items if r["correct"]) / len(items)


def session_length_seconds(save: SaveData) -> float:
    return save.total_study_seconds
