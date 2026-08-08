"""CLI entry point: argparse, --smoke-test chạy kịch bản tự động rồi thoát."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from kana_rush.app import App, QuitToExit
from kana_rush.data import load_dataset
from kana_rush.storage import Storage
from kana_rush.ui import InputSourceExhausted, UIOptions, UI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kana-rush",
        description="Kana Rush - học 46 Hiragana bằng active recall & spaced repetition.",
    )
    parser.add_argument("--no-delay", action="store_true",
                        help="tắt delay giữa các câu (nhanh hơn)")
    parser.add_argument("--no-color", action="store_true",
                        help="tắt màu sắc (terminal đơn sắc)")
    parser.add_argument("--ascii", action="store_true",
                        help="chế độ ASCII (không dùng ký tự box-drawing/emoji)")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed cố định để kết quả lặp lại (dùng cho test)")
    parser.add_argument("--save-path", type=Path, default=None,
                        help="thư mục chứa progress.json (mặc định: saves/)")
    parser.add_argument("--smoke-test", action="store_true",
                        help="tự động chạy kịch bản kiểm tra rồi thoát (không đụng save thật)")
    return parser


def _smoke_script() -> list[str]:
    """Kịch bản scripted input cho --smoke-test.

    Lưu đồ: chào mừng -> chọn 2 (diagnostic) -> 46 câu trả lời sai 'a'
    -> menu: 7 chart -> 8 thống kê -> 9 cài đặt (tắt/bật màu) -> 0 lưu & thoát.
    Thêm vài dòng '0' dư để phòng lệch một-dòng.
    """
    script = ["2"] + ["a"] * 46
    script += ["7", "", "8", "", "9", "2", "0", "0"]
    script += ["0"] * 5
    return script


def main(argv: list[str] | None = None) -> int:
    # Windows console mặc định cp1252 -> bắt buộc UTF-8 để in tiếng Việt.
    # Phải chạy TRƯỚC parse_args: argparse print_help() cũng in tiếng Việt.
    # Trên Linux/macOS đã UTF-8 sẵn, reconfigure là no-op an toàn.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - môi trường không hỗ trợ thì bỏ qua
            pass

    args = build_parser().parse_args(argv)

    dataset = load_dataset()

    scripted = _smoke_script() if args.smoke_test else []
    ui = UI(UIOptions(
        delay_ms=0 if args.no_delay else 600,
        no_color=args.no_color,
        ascii_mode=args.ascii,
        scripted_input=scripted,
    ))

    smoke_tmp: Path | None = None
    if args.smoke_test:
        smoke_tmp = Path(tempfile.mkdtemp(prefix="kana-rush-smoke-"))
        storage = Storage(smoke_tmp)
        ui.say("[cyan]KANA RUSH SMOKE TEST[/cyan] - save tạm: {}".format(smoke_tmp))
    else:
        storage = Storage(args.save_path) if args.save_path else Storage()

    app = App(ui, dataset, storage, seed=args.seed)
    try:
        app.run()
    except QuitToExit:
        pass
    except InputSourceExhausted:
        if app.save is not None:
            app.save_now()
        ui.say("Smoke: scripted input hết sớm hơn dự kiến (vẫn lưu).", style="yellow")
    except KeyboardInterrupt:
        if app.save is not None:
            app.save_now()
        ui.say("\nĐã lưu (Ctrl+C).", style="green")

    if args.smoke_test:
        return _report_smoke(app, storage, smoke_tmp)
    return 0


def _report_smoke(app: App, storage: Storage, smoke_tmp: Path | None) -> int:
    """Kiểm tra kết quả smoke test và in báo cáo PASS/FAIL."""
    ok = True
    try:
        loaded = storage.load()
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE FAIL: không đọc lại được save: {exc}")
        return 1

    checks = [
        ("save tồn tại & đọc lại được", loaded is not None),
        ("diagnostic đã chạy", bool(loaded.diagnostic_done)),
        ("session_count >= 1", loaded.session_count >= 1),
        ("diagnostic sai không thăng cấp (toàn NEW)",
         all(c.state.value == "new" for c in loaded.cards.values())),
    ]
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    if smoke_tmp:
        backup = smoke_tmp / "progress.backup.json"
        print(f"  [{'PASS' if backup.exists() else 'FAIL'}] backup file được tạo")
        ok = ok and backup.exists()

    print("SMOKE TEST: " + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
