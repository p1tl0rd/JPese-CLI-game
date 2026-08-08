"""Tests cloud sync qua git: push/pull end-to-end với bare remote tạm."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kana_rush import cloud
from kana_rush.cloud import (
    PENDING_STATE_FILE,
    CloudSyncError,
    retry_pending_push,
    sync_pull,
    sync_push,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git chưa được cài đặt"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, timeout=60
    )


def _make_clone(root: Path, name: str, remote: Path) -> Path:
    clone = root / name
    assert _git(root, "clone", str(remote), str(clone)).returncode == 0
    assert _git(clone, "config", "user.email", f"{name}@test").returncode == 0
    assert _git(clone, "config", "user.name", name).returncode == 0
    return clone


def _write_save(save_dir: Path, updated_at: str) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema_version": 1, "updated_at": updated_at, "xp": 0},
        ensure_ascii=False,
    )
    (save_dir / "progress.json").write_text(payload, encoding="utf-8")


def _read_save(save_dir: Path) -> dict:
    return json.loads((save_dir / "progress.json").read_text(encoding="utf-8"))


def test_push_then_pull_second_machine(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0

    repo_a = _make_clone(tmp_path, "a", remote)
    save_a = repo_a / "saves"
    _write_save(save_a, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_a) is None

    # Máy B clone đã kèm sẵn save từ remote.
    repo_b = _make_clone(tmp_path, "b", remote)
    save_b = repo_b / "saves"
    assert _read_save(save_b)["updated_at"] == "2026-08-08T10:00:00+00:00"
    # Đồng bằng remote -> không cần kéo.
    assert sync_pull(save_b) is None

    # Máy A chơi tiếp và push bản mới hơn; B kéo về.
    _write_save(save_a, "2026-08-08T11:00:00+00:00")
    assert sync_push(save_a) is None
    assert sync_pull(save_b) is not None
    assert _read_save(save_b)["updated_at"] == "2026-08-08T11:00:00+00:00"


def test_pull_downloads_when_local_save_missing(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0

    repo_a = _make_clone(tmp_path, "a", remote)
    save_a = repo_a / "saves"
    _write_save(save_a, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_a) is None

    repo_b = _make_clone(tmp_path, "b", remote)
    save_b = repo_b / "saves"
    for path in save_b.iterdir():
        path.unlink()
    assert not save_b.exists() or not list(save_b.iterdir())
    assert sync_pull(save_b) is not None
    assert _read_save(save_b)["updated_at"] == "2026-08-08T10:00:00+00:00"


def test_pull_keeps_local_when_local_newer(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0

    repo_a = _make_clone(tmp_path, "a", remote)
    save_a = repo_a / "saves"
    _write_save(save_a, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_a) is None

    repo_b = _make_clone(tmp_path, "b", remote)
    save_b = repo_b / "saves"
    # B chơi (local mới hơn 11:00) nhưng chưa push; A đẩy 10:30 lên remote.
    _write_save(save_b, "2026-08-08T11:00:00+00:00")
    _write_save(save_a, "2026-08-08T10:30:00+00:00")
    assert sync_push(save_a) is None
    # Remote (10:30) cũ hơn local (11:00) -> giữ nguyên.
    assert sync_pull(save_b) is None
    assert _read_save(save_b)["updated_at"] == "2026-08-08T11:00:00+00:00"


def test_pull_takes_newer_remote_over_local(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0

    repo_a = _make_clone(tmp_path, "a", remote)
    save_a = repo_a / "saves"
    _write_save(save_a, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_a) is None

    repo_b = _make_clone(tmp_path, "b", remote)
    save_b = repo_b / "saves"
    # A push 12:00; B chơi (11:00, cũ hơn) và thoát -> push bị từ chối vì B
    # đang sau remote (hành vi đúng, chỉ cảnh báo).
    _write_save(save_a, "2026-08-08T12:00:00+00:00")
    assert sync_push(save_a) is None
    _write_save(save_b, "2026-08-08T11:00:00+00:00")
    assert sync_push(save_b) is not None
    # A mở game: remote 11:00 cũ hơn local 12:00 -> giữ local.
    assert sync_pull(save_a) is None
    assert _read_save(save_a)["updated_at"] == "2026-08-08T12:00:00+00:00"
    # B mở game: remote 12:00 mới hơn local 11:00 -> kéo về.
    assert sync_pull(save_b) is not None
    assert _read_save(save_b)["updated_at"] == "2026-08-08T12:00:00+00:00"


def test_push_without_changes_returns_none(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0
    repo = _make_clone(tmp_path, "a", remote)
    save_dir = repo / "saves"
    _write_save(save_dir, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_dir) is None
    assert sync_push(save_dir) is None  # lần 2: không có thay đổi


def test_no_remote_returns_none(tmp_path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    assert _git(repo, "init").returncode == 0
    assert sync_pull(repo / "saves") is None
    assert sync_push(repo / "saves") is None


def test_push_timeout_marks_pending_then_retry_pushes(tmp_path, monkeypatch) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0
    repo = _make_clone(tmp_path, "a", remote)
    save_dir = repo / "saves"
    _write_save(save_dir, "2026-08-08T10:00:00+00:00")

    real_run = cloud._run_git
    failed = {"done": False}

    def flaky_run(repo_dir, args, timeout=cloud.GIT_TIMEOUT_S):
        if args[0] == "push" and not failed["done"]:
            failed["done"] = True
            raise CloudSyncError("git push quá chậm (>5s)")
        return real_run(repo_dir, args, timeout=timeout)

    monkeypatch.setattr(cloud, "_run_git", flaky_run)
    warning = sync_push(save_dir, timeout=5)
    assert "push lỗi" in warning
    assert (repo / PENDING_STATE_FILE).exists()
    # Commit đã nằm local - remote chưa có ref nào.
    assert _git(remote, "for-each-ref").stdout == b""

    monkeypatch.undo()
    assert retry_pending_push(save_dir) is None  # mở game sau: tự đẩy lại, im lặng
    assert not (repo / PENDING_STATE_FILE).exists()
    assert _git(remote, "for-each-ref").stdout != b""
    clone2 = _make_clone(tmp_path, "b", remote)
    assert _read_save(clone2 / "saves")["updated_at"] == "2026-08-08T10:00:00+00:00"


def test_retry_pending_push_rejected_clears_state(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0
    repo_a = _make_clone(tmp_path, "a", remote)
    save_a = repo_a / "saves"
    _write_save(save_a, "2026-08-08T10:00:00+00:00")
    assert sync_push(save_a) is None

    # Máy B đẩy commit mới lên remote (làm branch đi xa hơn).
    repo_b = _make_clone(tmp_path, "b", remote)
    _write_save(repo_b / "saves", "2026-08-08T11:00:00+00:00")
    assert sync_push(repo_b / "saves") is None

    # A có commit nợ chưa push -> lần thoát bị từ chối (sau remote) -> không
    # đánh dấu pending (retry không cứu được non-ff); thử lại cũng bị từ chối.
    _write_save(save_a, "2026-08-08T12:00:00+00:00")
    warning = sync_push(save_a)
    assert warning is not None and "từ chối" in warning
    assert not (repo_a / PENDING_STATE_FILE).exists()
    (repo_a / PENDING_STATE_FILE).write_text("{}", encoding="utf-8")
    warning = retry_pending_push(save_a)
    assert warning is not None and "từ chối" in warning
    assert not (repo_a / PENDING_STATE_FILE).exists()
    # Remote không bị ghi đè.
    assert _read_save(repo_b / "saves")["updated_at"] == "2026-08-08T11:00:00+00:00"


def test_retry_pending_push_without_pending_returns_none(tmp_path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    assert _git(repo, "init").returncode == 0
    assert retry_pending_push(repo / "saves") is None


def test_retry_pending_push_retries_transient_timeout(tmp_path, monkeypatch) -> None:
    remote = tmp_path / "remote.git"
    assert _git(tmp_path, "init", "--bare", str(remote)).returncode == 0
    repo = _make_clone(tmp_path, "a", remote)
    save_dir = repo / "saves"
    _write_save(save_dir, "2026-08-08T10:00:00+00:00")

    # Tạo commit local và đánh dấu pending như sau một lần thoát bị timeout.
    assert sync_push(save_dir) is None
    _write_save(save_dir, "2026-08-08T11:00:00+00:00")
    assert sync_push(save_dir, timeout=1) is None
    (repo / PENDING_STATE_FILE).write_text("{}", encoding="utf-8")

    real_run = cloud._run_git
    failures = {"count": 0}

    def flaky_run(repo_dir, args, timeout=cloud.GIT_TIMEOUT_S):
        if args[0] == "push" and failures["count"] < 2:
            failures["count"] += 1
            raise CloudSyncError(f"git push quá chậm (>{timeout}s)")
        return real_run(repo_dir, args, timeout=timeout)

    monkeypatch.setattr(cloud, "_run_git", flaky_run)
    assert retry_pending_push(save_dir, timeout=1, attempts=3) is None
    assert failures["count"] == 2
    assert not (repo / PENDING_STATE_FILE).exists()
    assert _git(remote, "for-each-ref").stdout != b""
