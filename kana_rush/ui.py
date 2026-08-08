"""UI terminal bằng Rich: header, kana lớn, feedback, menu, chart, input.

Tách khỏi domain logic; hỗ trợ --no-color, --ascii, --no-delay và input script
(dùng cho smoke test / test tự động).
"""

from __future__ import annotations

import sys
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from rich import box as rich_box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kana_rush.models import KanaState, SaveData
from kana_rush.scoring import level_for_xp
from kana_rush.statistics import due_count


class QuitRequested(Exception):
    """Người chơi thoát (gõ quit, EOF hoặc Ctrl+C)."""


class InputSourceExhausted(Exception):
    """Scripted input hết dòng (chỉ dùng trong test/smoke)."""


@dataclass
class UIOptions:
    delay_ms: int = 600
    no_color: bool = False
    ascii_mode: bool = False
    scripted_input: list[str] = field(default_factory=list)


def normalize_answer(text: str) -> str:
    """Trim, NFKC (full-width -> half-width), lowercase."""
    return unicodedata.normalize("NFKC", text).strip().lower()


@dataclass(frozen=True)
class Answer:
    kind: str  # "answer" | "hint" | "quit" | "eof"
    text: str = ""


class UI:
    def __init__(self, options: UIOptions | None = None) -> None:
        opts = options or UIOptions()
        self.options = opts
        self.console = Console(
            no_color=opts.no_color,
            highlight=False,
            safe_box=opts.ascii_mode,
            file=sys.stdout,
        )
        self._scripted: list[str] = list(opts.scripted_input)
        self._scripted_mode = bool(opts.scripted_input)

    def apply_settings(self, settings: dict) -> None:
        """Áp dụng settings thay đổi lúc runtime (delay, màu, ascii)."""
        self.options.delay_ms = int(settings.get("delay_ms", 600))
        self.options.no_color = bool(settings.get("no_color", False))
        self.options.ascii_mode = bool(settings.get("ascii_mode", False))
        self.console = Console(
            no_color=self.options.no_color,
            highlight=False,
            safe_box=self.options.ascii_mode,
            file=sys.stdout,
        )

    # ---------------------------------------------------------- helpers
    def say(self, text: str = "", style: str | None = None) -> None:
        self.console.print(text, style=style)

    def delay(self) -> None:
        if self.options.delay_ms > 0:
            time.sleep(self.options.delay_ms / 1000.0)

    def blank(self, n: int = 1) -> None:
        self.console.print("\n" * n, end="")

    def panel(self, title: str, lines: list[str] | str, style: str | None = None) -> None:
        content = "\n".join(lines) if isinstance(lines, list) else lines
        # rich mới crash khi style=None -> dùng "none" (style rỗng tương đương)
        self.console.print(Panel(content, title=title, title_align="left", style=style or "none"))

    def confirm(self, question: str, default_yes: bool = True) -> bool:
        suffix = "[y/N]" if not default_yes else "[Y/n]"
        while True:
            self.say(f"{question} {suffix}")
            raw = self._read_line()
            if raw is None:
                return default_yes
            answer = normalize_answer(raw)
            if not answer:
                return default_yes
            if answer in ("y", "yes", "c", "co", "có"):
                return True
            if answer in ("n", "no", "khong"):
                return False
            self.say("Hãy trả lời y hoặc n.")

    def press_enter(self, message: str = "Nhấn Enter để tiếp tục") -> None:
        self._read_line(prompt=f"  {message} > ")
        self.console.print()

    # ---------------------------------------------------------- header
    def header(self, save: SaveData, now=None) -> None:
        mastered = sum(1 for c in save.cards.values() if c.state is KanaState.MASTERED)
        due = due_count(save, now)
        level = level_for_xp(save.xp)
        self.panel(
            "KANA RUSH",
            [
                f"Chuỗi đúng: {save.streak}     XP: {save.xp}     Level {level}",
                f"Mastered: {mastered}/46     Đến hạn: {due}",
            ],
        )

    def status_bar(self, save: SaveData) -> None:
        introduced = sum(1 for c in save.cards.values() if c.introduced_at)
        self.say(
            f"[dim]Đã giới thiệu: {introduced}/46  |  "
            f"Học: {sum(1 for c in save.cards.values() if c.state is KanaState.LEARNING)}  |  "
            f"Ôn: {sum(1 for c in save.cards.values() if c.state is KanaState.REVIEW)}  |  "
            f"Mastered: {sum(1 for c in save.cards.values() if c.state is KanaState.MASTERED)}[/dim]"
        )

    # ---------------------------------------------------------- kana
    def show_kana(self, kana: str, sub: str = "") -> None:
        text = Text("\n", end="")
        text.append(f"    {kana}", style="bold yellow")
        text.append("\n")
        if sub:
            text.append(f"      {sub}", style="dim")
        self.console.print(Panel(text, box=rich_box.ROUNDED, border_style="yellow"))

    # ---------------------------------------------------------- input
    def _read_line(self, prompt: str = "") -> str | None:
        """Đọc một dòng. Trả None khi EOF (không phải lỗi)."""
        if self._scripted_mode:
            if not self._scripted:
                raise InputSourceExhausted()
            if prompt:
                self.console.print(prompt, end="", style="bold")
            value = self._scripted.pop(0)
            self.console.print(value or " ")
            return value
        try:
            if prompt:
                self.console.print(prompt, end="", style="bold")
            line = input()
            return line
        except EOFError:
            return None

    def read_answer(self, prompt: str = "Romaji > ") -> Answer:
        raw = self._read_line(prompt)
        if raw is None:
            return Answer("eof")
        text = normalize_answer(raw)
        if text in ("?", "hint", "giup"):
            return Answer("hint")
        if text in ("quit", "exit", "thoat"):
            return Answer("quit")
        if not text:
            self.say("Bạn chưa nhập gì. Gõ đáp án, '?' để xin gợi ý, hoặc 'quit' để thoát.", style="dim")
            return self.read_answer(prompt)
        return Answer("answer", text)

    def read_menu_choice(self, prompt: str = "Chọn > ") -> str | None:
        raw = self._read_line(prompt)
        if raw is None:
            return None
        return normalize_answer(raw)

    # ---------------------------------------------------------- feedback
    def feedback_correct(self, xp_gained: int, streak: int, extra: str = "") -> None:
        self.say(f"Chính xác! +{xp_gained} XP  (chuỗi: {streak})", style="bold green")
        if extra:
            self.say(extra, style="dim")
        self.delay()

    def feedback_wrong(
        self,
        correct_kana: str,
        correct_romaji: str,
        confused_kana: str | None = None,
        confused_romaji: str | None = None,
        lesson_context: bool = False,
    ) -> None:
        self.say(f"Chưa đúng. Đáp án: {correct_romaji}", style="bold red")
        if confused_kana:
            self.say(
                f"Dễ nhầm với: {confused_kana} — {confused_romaji}", style="yellow"
            )
        if lesson_context:
            self.say("Hãy gõ lại đáp án đúng để ghi nhớ lỗi.", style="dim")
        self.delay()

    def feedback_hint(self, hint_text: str, style: str | None = None) -> None:
        self.say(f"Gợi ý: {hint_text}", style=style or "cyan")
        self.delay()

    # ---------------------------------------------------------- menus
    def main_menu(self, save: SaveData, now=None) -> str | None:
        mastered = sum(1 for c in save.cards.values() if c.state is KanaState.MASTERED)
        due = due_count(save, now)
        learning = sum(
            1
            for c in save.cards.values()
            if c.state is not KanaState.NEW and c.state is not KanaState.MASTERED
        )
        self.header(save, now)
        self.say(
            f"[bold]Hôm nay:[/bold] Kana đến hạn: {due} | Đang học: {learning} | Mastered: {mastered}/46 | Chuỗi ngày: {save.day_streak}"
        )
        lines = [
            ("1", "Tiếp tục buổi học hôm nay"),
            ("2", "Learn - Học kana mới"),
            ("3", "Review - Ôn kana đã học"),
            ("4", "Confusion Drill - Luyện chữ dễ nhầm"),
            ("5", "Word Bridge - Đọc từ ngắn"),
            ("6", "Speed Run"),
            ("7", "Kana Chart"),
            ("8", "Thống kê"),
            ("9", "Cài đặt"),
            ("0", "Lưu và thoát"),
        ]
        for key, label in lines:
            self.say(f"[bold]{key}[/bold] {label}")
        if due:
            self.say("[dim]Gợi ý: chọn [3] Review nếu có kana đến hạn.[/dim]")
        self.say()
        choice = self.read_menu_choice()
        if choice is None:
            return "0"
        return choice

    def select_mode(self, title: str, options: list[tuple[str, str]]) -> str | None:
        self.say(f"[bold]{title}[/bold]")
        for key, label in options:
            self.say(f"[bold]{key}[/bold] {label}")
        return self.read_menu_choice()

    # ---------------------------------------------------------- misc
    def show_summary(self, title: str, lines: list[str]) -> None:
        self.panel(title, lines)

    def progress_bar(self, done: int, total: int, width: int = 20) -> str:
        if total <= 0:
            filled = 0
        else:
            filled = max(0, min(width, round(done * width / total)))
        block = "█" if not self.options.ascii_mode else "#"
        empty = "░" if not self.options.ascii_mode else "-"
        return block * filled + empty * (width - filled)

    def chart(self, save: SaveData, dataset, select: bool = True) -> None:
        """Bảng kana với màu theo trạng thái; chọn 1 kana xem chi tiết."""
        rows = sorted({k.row for k in dataset.kana_list})
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("", style="dim")
        for _ in range(5):
            table.add_column(justify="center")
        for row in rows:
            cells: list[Text] = []
            for kana_obj in dataset.kana_list:
                if kana_obj.row != row:
                    continue
                card = save.card(kana_obj.kana)
                text = Text(kana_obj.kana)
                if card.state is KanaState.NEW:
                    text.stylize("dim")
                elif card.state is KanaState.LEARNING:
                    text.stylize("cyan")
                elif card.state is KanaState.REVIEW:
                    text.stylize("yellow")
                elif card.state is KanaState.MASTERED:
                    text.stylize("bold green")
                else:
                    text.stylize("bold magenta")
                cells.append(text)
            table.add_row(str(row), *cells)
        legend = (
            "[dim]Chưa học[/dim]  [cyan]Đang học[/cyan]  [yellow]Ôn[/yellow]  "
            "[bold green]Mastered[/bold green]  [bold magenta]Học lại[/bold magenta]"
        )
        self.console.print(Group(table, Text(legend)))
        if not select:
            return
        self.say()
        choice = normalize_answer(self._read_line("Xem chi tiết kana nào (kana hoặc Enter để bỏ qua) > ") or "")
        if choice in dataset.by_kana:
            self.kana_detail(save, dataset, choice)

    def kana_detail(self, save: SaveData, dataset, kana_id: str) -> None:
        kana_obj = dataset.kana(kana_id)
        card = save.card(kana_id)
        lines = [
            f"Kana: {kana_id}   Romaji: {kana_obj.romaji}",
            f"Trạng thái: {card.state.value.upper()}",
            f"Mnemonics: {dataset.mnemonics.get(kana_id, '')}",
        ]
        if dataset.distinguish.get(kana_id):
            lines.append(f"Phân biệt: {dataset.distinguish[kana_id]}")
        lines.append(
            f"Đúng (không hint): {card.correct_unaided} | Sai: {card.wrong_count} | Lapse: {card.lapse_count}"
        )
        median = card.median_rt()
        lines.append(f"Median thời gian trả lời: {f'{median:.0f} ms' if median is not None else 'chưa đủ dữ liệu'}")
        if card.next_review_at:
            lines.append(f"Ôn tiếp: {card.next_review_at.astimezone().strftime('%d/%m %H:%M')}")
        else:
            lines.append("Ôn tiếp: chưa lên lịch")
        if card.confused_with:
            lines.append(
                "Hay nhầm với: "
                + ", ".join(f"{k} ({v} lần)" for k, v in sorted(card.confused_with.items(), key=lambda p: -p[1]))
            )
        history = card.recent_results[-6:]
        if history:
            mark = ("D" if self.options.ascii_mode else "✓", "S" if self.options.ascii_mode else "✗")
            lines.append(
                "Lịch sử gần đây: "
                + " ".join(mark[0] if r["correct"] else mark[1] for r in history)
            )
        self.panel("Chi tiết kana", lines)
        self.press_enter()
