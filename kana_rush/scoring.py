"""Scoring và game hóa: XP, streak, achievements (không ảnh hưởng mastery)."""

from __future__ import annotations

from kana_rush.models import SaveData
from kana_rush.scheduler import FAST_MS

BASE_XP = 10
MAX_FAST_BONUS = 20
MAX_STREAK_MULTIPLIER = 3
HINT_XP = 5


def score_answer(correct: bool, rt_ms: int, hinted: bool, streak: int) -> tuple[int, int]:
    """Trả (xp, streak_mới). Corrective typing (hinted+đã thấy đáp án) = 0 điểm."""
    if not correct or hinted:
        new_streak = streak + 1 if correct else 0
        return (HINT_XP if correct else 0, new_streak)
    fast_bonus = max(0, MAX_FAST_BONUS - rt_ms // 250)
    fast_bonus = min(fast_bonus, MAX_FAST_BONUS)
    multiplier = min(MAX_STREAK_MULTIPLIER, 1 + streak // 5)
    xp = round((BASE_XP + fast_bonus) * multiplier)
    return xp, streak + 1


def level_for_xp(xp: int) -> int:
    return xp // 500 + 1


def check_achievements(save: SaveData) -> list[str]:
    """Trả các achievement mới đạt được (chưa có trong save)."""
    from kana_rush.models import KanaState

    unlocked: list[str] = []
    counts = {s: 0 for s in KanaState}
    for card in save.cards.values():
        counts[card.state] += 1
    if save.streak >= 5 and "streak_5" not in save.achievements:
        unlocked.append("streak_5")
    if save.streak >= 10 and "streak_10" not in save.achievements:
        unlocked.append("streak_10")
    if save.best_streak >= 20 and "streak_20" not in save.achievements:
        unlocked.append("streak_20")
    if counts[KanaState.MASTERED] >= 1 and "first_mastered" not in save.achievements:
        unlocked.append("first_mastered")
    if counts[KanaState.MASTERED] >= 10 and "ten_mastered" not in save.achievements:
        unlocked.append("ten_mastered")
    if save.best_speedrun_score >= 30 and "speedrun_30" not in save.achievements:
        unlocked.append("speedrun_30")
    if sum(1 for c in save.cards.values() if c.introduced_at) >= 46 and "all_introduced" not in save.achievements:
        unlocked.append("all_introduced")
    if save.word_progress and "first_word" not in save.achievements:
        unlocked.append("first_word")
    save.achievements.extend(unlocked)
    return unlocked


ACHIEVEMENT_LABELS: dict[str, str] = {
    "streak_5": "Chuỗi 5 câu đúng",
    "streak_10": "Chuỗi 10 câu đúng",
    "streak_20": "Chuỗi 20 câu đúng",
    "first_mastered": "Kana đầu tiên đạt MASTERED",
    "ten_mastered": "10 kana MASTERED",
    "speedrun_30": "Speed Run đạt 30+ điểm",
    "all_introduced": "Đã giới thiệu cả 46 kana",
    "first_word": "Đọc từ đầu tiên",
}


def fast_rt(rt_ms: int) -> bool:
    return rt_ms < FAST_MS
