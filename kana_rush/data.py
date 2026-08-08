"""Load và validate toàn bộ dữ liệu kana từ data/."""

from __future__ import annotations

import json
from pathlib import Path

from kana_rush.models import Kana

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

BASIC_HIRAGANA_COUNT = 46


class DataError(Exception):
    """Dữ liệu thiếu hoặc không hợp lệ."""


class KanaDataset:
    """Toàn bộ kana + mnemonics + confusion pairs + từ vựng đã được validate."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.kana_list: list[Kana] = []
        self.by_kana: dict[str, Kana] = {}
        self.mnemonics: dict[str, str] = {}
        self.distinguish: dict[str, str] = {}
        self.stroke_orders: dict[str, str] = {}
        self.confusion_pairs: list[tuple[str, str]] = []
        self.words: list[dict] = []
        self._load_all()

    # ------------------------------------------------------------- load
    def _load_all(self) -> None:
        hiragana = self._read_json("hiragana.json")
        mnemonic_data = self._read_json("mnemonics_vi.json")
        confusion_data = self._read_json("confusion_pairs.json")
        words_data = self._read_json("words.json")

        seen_kana: set[str] = set()
        seen_romaji: set[str] = set()
        for entry in hiragana["kana"]:
            kana = entry["kana"]
            if kana in seen_kana:
                raise DataError(f"Trùng kana: {kana}")
            seen_kana.add(kana)
            romaji = entry["romaji"]
            if romaji in seen_romaji:
                raise DataError(f"Trùng canonical romaji: {romaji}")
            seen_romaji.add(romaji)
            kana_obj = Kana(
                kana=kana,
                romaji=romaji,
                aliases=tuple(entry.get("aliases", [])),
                row=int(entry.get("row", 0)),
                col=int(entry.get("col", 0)),
            )
            self.kana_list.append(kana_obj)
            self.by_kana[kana] = kana_obj

        if len(self.kana_list) != BASIC_HIRAGANA_COUNT:
            raise DataError(
                f"Phải có đúng {BASIC_HIRAGANA_COUNT} kana cơ bản, "
                f"thấy {len(self.kana_list)}"
            )

        # Mnemonics: mọi kana phải có mnemonic tiếng Việt.
        for kana in seen_kana:
            record = mnemonic_data.get(kana)
            if not record or not record.get("mnemonic"):
                raise DataError(f"Thiếu mnemonic cho kana {kana}")
            self.mnemonics[kana] = record["mnemonic"]
            self.distinguish[kana] = record.get("distinguish", "")
            self.stroke_orders[kana] = record.get("stroke", "")

        # Alias phải trỏ đúng: alias "shi" của し không được trùng romaji kana khác.
        alias_map: dict[str, str] = {}
        for kana_obj in self.kana_list:
            for alias in kana_obj.aliases:
                if alias in alias_map:
                    raise DataError(f"Alias {alias} bị trùng giữa các kana")
                alias_map[alias] = kana_obj.kana
                if alias == kana_obj.romaji:
                    raise DataError(f"Alias {alias} trùng canonical của {kana_obj.kana}")

        # Confusion pairs chỉ chứa kana hợp lệ.
        for pair in confusion_data.get("pairs", []):
            a, b = pair.get("a"), pair.get("b")
            if a not in seen_kana or b not in seen_kana:
                raise DataError(f"Confusion pair chứa kana không hợp lệ: {a}/{b}")
            if a == b:
                raise DataError(f"Confusion pair trùng kana: {a}")
            self.confusion_pairs.append((a, b))
        if not self.confusion_pairs:
            raise DataError("Danh sách confusion pairs rỗng")

        # Words: kana hợp lệ, decomposition khớp nội dung, kana thuộc dataset.
        for word in words_data.get("words", []):
            kana_text = word.get("kana", "")
            romaji = word.get("romaji", "")
            meaning = word.get("meaning", "")
            if not kana_text or not romaji or not meaning:
                raise DataError(f"Word thiếu trường: {word}")
            decomposed = list(kana_text)
            for ch in decomposed:
                if ch not in seen_kana:
                    raise DataError(f"Word {kana_text} chứa kana ngoài dataset: {ch}")
            word["decomposition"] = decomposed
            self.words.append(word)
        if not self.words:
            raise DataError("Danh sách từ rỗng")

    def _read_json(self, filename: str) -> dict:
        path = self.data_dir / filename
        if not path.exists():
            raise DataError(f"Thiếu file dữ liệu: {path}")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise DataError(f"Không đọc được {path}: {exc}") from exc

    # --------------------------------------------------------- helpers
    def kana(self, ch: str) -> Kana:
        return self.by_kana[ch]

    def is_introduced(self, save: object, kana_id: str) -> bool:
        from kana_rush.models import KanaState

        card = save.card(kana_id)
        return card.state is not KanaState.NEW

    def romaji_ok(self, kana_id: str, answer: str) -> bool:
        """Kiểm tra đáp án romaji đã normalize (thường, trim) của một kana."""
        kana_obj = self.by_kana[kana_id]
        return answer in kana_obj.all_readings()

    def confusion_target(self, answer: str) -> str | None:
        """Nếu đáp án sai trùng romaji của kana khác -> trả về kana đó."""
        for kana_obj in self.kana_list:
            if answer in kana_obj.all_readings():
                return kana_obj.kana
        return None


_DATASET_CACHE: KanaDataset | None = None


def load_dataset(data_dir: Path | None = None) -> KanaDataset:
    """Load dataset (cache theo thư mục)."""
    global _DATASET_CACHE
    if data_dir is not None:
        return KanaDataset(data_dir)
    if _DATASET_CACHE is None:
        _DATASET_CACHE = KanaDataset()
    return _DATASET_CACHE
