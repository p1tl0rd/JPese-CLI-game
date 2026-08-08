"""Tests XP/streak/level/achievements (spec §23)."""

from kana_rush.models import KanaState, SaveData
from kana_rush.scoring import ACHIEVEMENT_LABELS, check_achievements, level_for_xp, score_answer


def test_score_answer_matrix() -> None:
    xp, streak = score_answer(correct=False, rt_ms=500, hinted=False, streak=7)
    assert xp == 0 and streak == 0

    xp, streak = score_answer(correct=True, rt_ms=500, hinted=True, streak=3)
    assert xp == 5 and streak == 4

    xp, streak = score_answer(correct=True, rt_ms=200, hinted=False, streak=0)
    assert xp == 30 and streak == 1

    xp, _ = score_answer(correct=True, rt_ms=6000, hinted=False, streak=0)
    assert xp == 10

    xp, streak = score_answer(correct=True, rt_ms=400, hinted=False, streak=25)
    assert xp == 87 and streak == 26


def test_level_for_xp() -> None:
    assert level_for_xp(0) == 1
    assert level_for_xp(499) == 1
    assert level_for_xp(500) == 2
    assert level_for_xp(1500) == 4


def test_check_achievements_unlocks_and_no_duplicates() -> None:
    save = SaveData()
    save.streak = 12
    save.best_streak = 12
    save.best_speedrun_score = 45
    save.diagnostic_done = True
    for k, state in zip("あいうえおかきくけこ", [KanaState.MASTERED] * 10):
        save.card(k).state = state

    unlocked = check_achievements(save)
    assert "streak_5" in unlocked
    assert "streak_10" in unlocked
    assert "speedrun_30" in unlocked
    assert "first_mastered" in unlocked
    assert "ten_mastered" in unlocked

    again = check_achievements(save)
    assert again == []
    assert set(save.achievements) == set(unlocked)


def test_achievement_labels_exist() -> None:
    assert ACHIEVEMENT_LABELS
    for key in ACHIEVEMENT_LABELS:
        assert ACHIEVEMENT_LABELS[key]
