"""Cho phép chạy `python -m kana_rush`."""

import sys

from kana_rush.cli import main

if __name__ == "__main__":
    sys.exit(main())
