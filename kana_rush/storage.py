"""Persistence: save/load JSON atomic, backup, phục hồi khi hỏng, migration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from kana_rush.models import (
    AnswerSource,
    KanaCard,
    KanaState,
    LessonProgress,
    SCHEMA_VERSION,
    SaveData,
)
from kana_rush.timeutil import parse_iso

SAVE_DIR = Path(__file__).resolve().parent.parent / "saves"
SAVE_FILE = "progress.json"
BACKUP_FILE = "progress.backup.json"


class StorageError(Exception):
    """Lỗi lưu/đọc tiến độ."""


class SaveCorrupted(Exception):
    """File save chính và backup đều hỏng."""


# Migration framework: MIGRATIONS[v] nhận dict version v, trả dict version v+1.
MIGRATIONS: dict[int, callable] = {}


def migrate(raw: dict) -> dict:
    version = int(raw.get("schema_version", 1))
    while version < SCHEMA_VERSION:
        if version not in MIGRATIONS:
            raise StorageError(f"Không có migration từ version {version}.")
        raw = MIGRATIONS[version](raw)
        version += 1
        raw["schema_version"] = version
    return raw


def _state_from(value: str) -> KanaState:
    try:
        return KanaState(value)
    except ValueError as exc:
        raise StorageError(f"Trạng thái không hợp lệ: {value}") from exc


def card_from_dict(data: dict) -> KanaCard:
    card = KanaCard(state=_state_from(data["state"]))
    card.learning_step = int(data.get("learning_step", 0))
    card.review_stage = int(data.get("review_stage", 0))
    card.next_review_at = parse_iso(data["next_review_at"]) if data.get("next_review_at") else None
    card.last_reviewed_at = parse_iso(data["last_reviewed_at"]) if data.get("last_reviewed_at") else None
    card.introduced_at = parse_iso(data["introduced_at"]) if data.get("introduced_at") else None
    card.correct_unaided = int(data.get("correct_unaided", 0))
    card.correct_with_hint = int(data.get("correct_with_hint", 0))
    card.wrong_count = int(data.get("wrong_count", 0))
    card.lapse_count = int(data.get("lapse_count", 0))
    card.session_ids_correct = list(data.get("session_ids_correct", []))
    card.recent_results = list(data.get("recent_results", []))
    card.response_times_ms = list(data.get("response_times_ms", []))
    card.confused_with = {str(k): int(v) for k, v in data.get("confused_with", {}).items()}
    card.mastery_score = float(data.get("mastery_score", 0.0))
    card.stability = float(data.get("stability", 1.0))
    card.difficulty = float(data.get("difficulty", 0.5))
    return card


def card_to_dict(card: KanaCard) -> dict:
    return {
        "state": card.state.value,
        "learning_step": card.learning_step,
        "review_stage": card.review_stage,
        "next_review_at": card.next_review_at.isoformat() if card.next_review_at else None,
        "last_reviewed_at": card.last_reviewed_at.isoformat() if card.last_reviewed_at else None,
        "introduced_at": card.introduced_at.isoformat() if card.introduced_at else None,
        "correct_unaided": card.correct_unaided,
        "correct_with_hint": card.correct_with_hint,
        "wrong_count": card.wrong_count,
        "lapse_count": card.lapse_count,
        "session_ids_correct": card.session_ids_correct,
        "recent_results": card.recent_results,
        "response_times_ms": card.response_times_ms,
        "confused_with": card.confused_with,
        "mastery_score": round(card.mastery_score, 4),
        "stability": round(card.stability, 4),
        "difficulty": round(card.difficulty, 4),
    }


def lesson_progress_to_dict(progress: LessonProgress) -> dict:
    return {
        "lesson_id": progress.lesson_id,
        "introduced_kana": list(progress.introduced_kana),
        "completed_subgroups": [int(i) for i in progress.completed_subgroups],
        "learn_completed": progress.learn_completed,
        "started_at": progress.started_at.isoformat() if progress.started_at else None,
        "completed_at": progress.completed_at.isoformat() if progress.completed_at else None,
        "last_practiced_at": progress.last_practiced_at.isoformat() if progress.last_practiced_at else None,
        "total_attempts": progress.total_attempts,
        "accuracy": round(progress.accuracy, 4),
    }


def lesson_progress_from_dict(data: dict) -> LessonProgress:
    progress = LessonProgress(lesson_id=int(data.get("lesson_id", 0)))
    progress.introduced_kana = list(data.get("introduced_kana", []))
    progress.completed_subgroups = [int(i) for i in data.get("completed_subgroups", [])]
    progress.learn_completed = bool(data.get("learn_completed", False))
    progress.started_at = parse_iso(data["started_at"]) if data.get("started_at") else None
    progress.completed_at = parse_iso(data["completed_at"]) if data.get("completed_at") else None
    progress.last_practiced_at = parse_iso(data["last_practiced_at"]) if data.get("last_practiced_at") else None
    progress.total_attempts = int(data.get("total_attempts", 0))
    progress.accuracy = float(data.get("accuracy", 0.0))
    return progress


def save_to_dict(save: SaveData) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": save.created_at.isoformat(),
        "updated_at": save.updated_at.isoformat(),
        "session_id": save.session_id,
        "session_count": save.session_count,
        "total_study_seconds": round(save.total_study_seconds, 3),
        "xp": save.xp,
        "streak": save.streak,
        "best_streak": save.best_streak,
        "day_streak": save.day_streak,
        "last_active_date": save.last_active_date,
        "cards": {kana: card_to_dict(card) for kana, card in save.cards.items()},
        "confusion_matrix": save.confusion_matrix,
        "word_progress": save.word_progress,
        "lesson_progress": {
            str(k): lesson_progress_to_dict(v)
            for k, v in save.lesson_progress.items()
        },
        "settings": save.settings,
        "achievements": save.achievements,
        "best_speedrun_score": save.best_speedrun_score,
        "diagnostic_done": save.diagnostic_done,
    }


def save_from_dict(data: dict) -> SaveData:
    version = int(data.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise StorageError(
            f"Save từ phiên bản mới hơn ({version} > {SCHEMA_VERSION}), nâng cấp game."
        )
    save = SaveData(schema_version=version)
    save.created_at = parse_iso(data["created_at"]) if data.get("created_at") else save.created_at
    save.updated_at = parse_iso(data["updated_at"]) if data.get("updated_at") else save.updated_at
    save.session_id = str(data.get("session_id", ""))
    save.session_count = int(data.get("session_count", 0))
    save.total_study_seconds = float(data.get("total_study_seconds", 0.0))
    save.xp = int(data.get("xp", 0))
    save.streak = int(data.get("streak", 0))
    save.best_streak = int(data.get("best_streak", 0))
    save.day_streak = int(data.get("day_streak", 0))
    save.last_active_date = data.get("last_active_date")
    save.cards = {str(k): card_from_dict(v) for k, v in data.get("cards", {}).items()}
    save.confusion_matrix = {
        str(k): {str(k2): int(v2) for k2, v2 in v.items()}
        for k, v in data.get("confusion_matrix", {}).items()
    }
    save.word_progress = data.get("word_progress", {})
    save.lesson_progress = {
        int(k): lesson_progress_from_dict(v)
        for k, v in data.get("lesson_progress", {}).items()
    }
    save.settings = data.get("settings", {})
    save.achievements = list(data.get("achievements", []))
    save.best_speedrun_score = int(data.get("best_speedrun_score", 0))
    save.diagnostic_done = bool(data.get("diagnostic_done", False))
    return save


class Storage:
    """Lưu trữ tiến độ tại <dir>/progress.json với backup + ghi atomic."""

    def __init__(self, save_dir: Path | None = None) -> None:
        self.save_dir = Path(save_dir) if save_dir else SAVE_DIR

    def progress_path(self) -> Path:
        return self.save_dir / SAVE_FILE

    def backup_path(self) -> Path:
        return self.save_dir / BACKUP_FILE

    def exists(self) -> bool:
        return self.progress_path().exists()

    def load(self) -> SaveData:
        if not self.progress_path().exists():
            return SaveData()
        try:
            return self._read_file(self.progress_path())
        except (json.JSONDecodeError, StorageError, KeyError, ValueError) as exc:
            backup = self.backup_path()
            if backup.exists():
                try:
                    save = self._read_file(backup)
                    self.save(save, make_backup=False)
                    return save
                except Exception as backup_exc:  # noqa: BLE001 - phải báo rõ
                    raise SaveCorrupted(
                        f"File save chính hỏng ({exc}); backup cũng hỏng ({backup_exc})."
                    ) from backup_exc
            raise SaveCorrupted(
                f"File save chính hỏng ({exc}) và không có backup."
            ) from exc

    def _read_file(self, path: Path) -> SaveData:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise StorageError("Save không phải object JSON.")
        return save_from_dict(migrate(raw))

    def save(self, save: SaveData, *, make_backup: bool = True) -> None:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        if make_backup and self.progress_path().exists():
            try:
                os.replace(self.progress_path(), self.backup_path())
            except OSError:
                pass  # backup là phụ; không được chặn lưu chính
        payload = json.dumps(save_to_dict(save), ensure_ascii=False, indent=2)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.save_dir, delete=False, suffix=".tmp"
            ) as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, self.progress_path())
        except OSError as exc:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise StorageError(f"Không ghi được save: {exc}") from exc
        if not os.path.exists(self.progress_path()):
            raise StorageError("Save không tồn tại sau khi ghi.")
