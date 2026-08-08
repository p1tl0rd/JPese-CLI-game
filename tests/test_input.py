"""Tests nhập liệu: normalize, hint/quit mapping, scripted input (spec §23)."""

import pytest

from kana_rush.ui import InputSourceExhausted, UI, UIOptions, normalize_answer


def test_normalize_answer() -> None:
    assert normalize_answer("  Si ") == "si"
    assert normalize_answer("TU") == "tu"
    assert normalize_answer(" Ｎ") == "n"
    assert normalize_answer("あ") == "あ"
    assert normalize_answer("ShI") == "shi"


def test_read_answer_maps_hint_and_quit() -> None:
    ui = UI(UIOptions(scripted_input=["?", "hint", "giup", "quit", "exit", "thoat"]))
    assert ui.read_answer().kind == "hint"
    assert ui.read_answer().kind == "hint"
    assert ui.read_answer().kind == "hint"
    assert ui.read_answer().kind == "quit"
    assert ui.read_answer().kind == "quit"
    assert ui.read_answer().kind == "quit"


def test_read_answer_empty_reprompts() -> None:
    ui = UI(UIOptions(scripted_input=["", "", "n"]))
    answer = ui.read_answer()
    assert answer.kind == "answer"
    assert answer.text == "n"


def test_scripted_input_exhausted_raises() -> None:
    ui = UI(UIOptions(scripted_input=["x"]))
    assert ui.read_answer().text == "x"
    with pytest.raises(InputSourceExhausted):
        ui.read_answer()


def test_progress_bar_ascii_vs_unicode() -> None:
    ascii_ui = UI(UIOptions(ascii_mode=True))
    unicode_ui = UI(UIOptions(ascii_mode=False))
    bar = ascii_ui.progress_bar(5, 10)
    assert "#" in bar and "-" in bar
    assert "█" not in bar
    assert "█" in unicode_ui.progress_bar(5, 10)
    assert ascii_ui.progress_bar(10, 10).count("#") == 20
