"""Tests độ khó boss scale theo level XP và số kana MASTERED."""

from kana_rush.learn import (
    BOSS_HP_BASE,
    BOSS_MAX_QUESTIONS_BASE,
    boss_stats,
)
from kana_rush.models import KanaState, SaveData


def test_level1_no_mastered_is_base() -> None:
    stats = boss_stats(SaveData(xp=0))
    assert stats.hp == BOSS_HP_BASE
    assert stats.damage_bonus == 0
    assert stats.max_questions == BOSS_MAX_QUESTIONS_BASE
    assert stats.level == 1
    assert stats.mastered == 0


def test_hp_scales_with_level_and_mastered() -> None:
    save = SaveData(xp=2500)  # level 6
    for i, kana in enumerate("あいうえお"):
        card = save.card(kana)
        card.state = KanaState.MASTERED
    stats = boss_stats(save)
    assert stats.level == 6
    assert stats.mastered == 5
    expected_hp = BOSS_HP_BASE + 5 * 10 + 5 * 1
    assert stats.hp == expected_hp


def test_damage_bonus_grows_with_level_and_mastered() -> None:
    save = SaveData(xp=10000)  # level 21
    for i, kana in enumerate("あいうえおかきくけこ"):  # 10 mastered
        card = save.card(kana)
        card.state = KanaState.MASTERED
    stats = boss_stats(save)
    assert stats.damage_bonus == (21 - 1) // 2 + 10 // 8  # 10 + 1


def test_max_questions_scales_with_level() -> None:
    stats = boss_stats(SaveData(xp=1500))  # level 4
    assert stats.max_questions == BOSS_MAX_QUESTIONS_BASE + (4 - 1) * 2


def test_full_mastery_is_hardest() -> None:
    save = SaveData(xp=999999)
    for i in range(46):
        kana = f"kana{i}"
        card = save.card(kana)
        card.state = KanaState.MASTERED
    stats = boss_stats(save)
    assert stats.mastered == 46
    assert stats.hp > BOSS_HP_BASE + 40 * 10
