"""Cloud save qua git: sync_pull khi mở game, sync_push khi thoát (best-effort).

Tất cả lỗi chỉ trả về chuỗi cảnh báo - không bao giờ chặn game. Tắt hoàn toàn
bằng env KANA_RUSH_NO_CLOUD=1.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from kana_rush.storage import BACKUP_FILE, SAVE_FILE
from kana_rush.timeutil import parse_iso

REMOTE_REF_PREFIX = "origin/"
GIT_TIMEOUT_S = 12
# Push có thể cần thêm thời gian cho TLS/authentication hoặc mạng chậm. Timeout
# 5 giây khiến push hợp lệ bị đánh dấu lỗi gần như chắc chắn.
PUSH_QUIT_TIMEOUT_S = 30
PUSH_RETRY_TIMEOUT_S = 30
PUSH_RETRY_ATTEMPTS = 3
PUSH_RETRY_DELAY_S = 1
ENV_DISABLE = "KANA_RUSH_NO_CLOUD"
PENDING_STATE_FILE = ".kana-rush-cloud-state.json"

# Cấm git hỏi mật khẩu tương tác (tránh treo chờ nhập / hộp thoại GCM).
NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
}
# Bỏ các lần truyền mạng "chết" (nhỏ hơn 1KB/s trong 8s).
LOW_SPEED_ARGS = ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=8"]


class CloudSyncError(Exception):
    """Lỗi khi đồng bộ qua git."""


def cloud_enabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() not in ("1", "true", "yes")


def repo_dir_for(save_dir: Path) -> Path:
    """Save ở <root>/saves nên repo git là thư mục cha."""
    return Path(save_dir).parent


def _current_branch(repo_dir: Path) -> str | None:
    result = _run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""
    if name and name != "HEAD":
        return name
    result = _run_git(repo_dir, ["symbolic-ref", "--short", "HEAD"])
    name = result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""
    return name or None


def _run_git(repo_dir: Path, args: list[str], timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.update(NO_PROMPT_ENV)
    try:
        return subprocess.run(
            ["git", *LOW_SPEED_ARGS, *args],
            cwd=str(repo_dir),
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CloudSyncError("git chưa được cài đặt") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloudSyncError(f"git {args[0]} quá chậm (>{timeout}s)") from exc
    except OSError as exc:
        raise CloudSyncError(f"lỗi git: {exc}") from exc


def _pending_push_path(repo_dir: Path) -> Path:
    return repo_dir / PENDING_STATE_FILE


def _mark_pending_push(repo_dir: Path) -> None:
    try:
        _pending_push_path(repo_dir).write_text("{}", encoding="utf-8")
    except OSError:
        pass


def _clear_pending_push(repo_dir: Path) -> None:
    try:
        _pending_push_path(repo_dir).unlink(missing_ok=True)
    except OSError:
        pass


def _has_pending_push(repo_dir: Path) -> bool:
    return _pending_push_path(repo_dir).exists()


def _has_remote(repo_dir: Path) -> bool:
    try:
        return _run_git(repo_dir, ["remote", "get-url", "origin"]).returncode == 0
    except CloudSyncError:
        return False


def _remote_file(repo_dir: Path, ref: str, path: str) -> dict | None:
    result = _run_git(repo_dir, ["show", f"{ref}:{path}"])
    if result.returncode != 0:
        return None
    try:
        raw = json.loads(result.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _remote_file_exists(repo_dir: Path, ref: str, path: str) -> bool:
    return _run_git(repo_dir, ["cat-file", "-e", f"{ref}:{path}"]).returncode == 0


def _local_updated_at(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = raw.get("updated_at") if isinstance(raw, dict) else None
    return value if isinstance(value, str) else None


def _checkout_remote_save(repo_dir: Path, branch: str) -> None:
    ref = f"{REMOTE_REF_PREFIX}{branch}"
    files = [f"saves/{SAVE_FILE}"]
    if _remote_file_exists(repo_dir, ref, f"saves/{BACKUP_FILE}"):
        files.append(f"saves/{BACKUP_FILE}")
    _run_git(repo_dir, ["checkout", ref, "--", *files])
    # Căn chỉnh branch về origin để lần push sau không bị từ chối
    # (reset --mixed giữ nguyên nội dung file làm việc).
    result = _run_git(repo_dir, ["reset", "--mixed", ref])
    if result.returncode != 0:
        # Branch chưa có commit nào (clone từ remote rỗng): tạo branch tại remote.
        _run_git(repo_dir, ["checkout", "-B", branch, ref])


def sync_pull(save_dir: Path) -> str | None:
    """Kéo save mới hơn từ origin. Trả về cảnh báo hoặc None."""
    save_dir = Path(save_dir)
    if not cloud_enabled():
        return None
    repo = repo_dir_for(save_dir)
    if not _has_remote(repo):
        return None  # chưa cấu hình remote - dùng save cục bộ, không phiền người chơi
    branch = _current_branch(repo)
    if branch is None:
        return None
    ref = f"{REMOTE_REF_PREFIX}{branch}"
    try:
        fetch = _run_git(repo, ["fetch", "origin", branch])
    except CloudSyncError as exc:
        return f"Cloud: không fetch được ({exc}) - dùng save cục bộ."
    if fetch.returncode != 0:
        return "Cloud: fetch lỗi - dùng save cục bộ."
    local_path = save_dir / SAVE_FILE
    local_ts = _local_updated_at(local_path) if local_path.exists() else None
    if _run_git(repo, ["rev-list", "--count", "HEAD.." + ref]).stdout.strip() == b"0":
        if local_ts is not None:
            return None  # đã ngang bằng remote và có save cục bộ
        # Ngang bằng remote nhưng máy này thiếu file save -> tải về.
        try:
            remote_json = _remote_file(repo, ref, f"saves/{SAVE_FILE}")
        except CloudSyncError as exc:
            return f"Cloud: không đọc được save remote ({exc})."
        if remote_json is None:
            return None
        try:
            _checkout_remote_save(repo, branch)
        except CloudSyncError as exc:
            return f"Cloud: có save remote nhưng không tải được ({exc})."
        return "Đã tải save từ cloud (máy này chưa có tiến độ)."
    try:
        remote_json = _remote_file(repo, ref, f"saves/{SAVE_FILE}")
    except CloudSyncError as exc:
        return f"Cloud: không đọc được save remote ({exc}) - dùng save cục bộ."
    if remote_json is None:
        return None  # remote chưa có save (lần đầu)
    if local_ts is None:
        try:
            _checkout_remote_save(repo, branch)
        except CloudSyncError as exc:
            return f"Cloud: có save remote nhưng không tải được ({exc})."
        return "Đã tải save từ cloud (máy này chưa có tiến độ)."
    remote_ts = remote_json.get("updated_at")
    if not isinstance(remote_ts, str):
        return None
    try:
        remote_newer = parse_iso(remote_ts) > parse_iso(local_ts)
    except ValueError:
        return None  # không so sánh được thời gian - giữ save cục bộ an toàn
    if not remote_newer:
        return None  # save cục bộ không cũ hơn remote
    try:
        _checkout_remote_save(repo, branch)
    except CloudSyncError as exc:
        return f"Cloud: có save mới hơn nhưng không tải được ({exc})."
    return f"Đã đồng bộ save từ cloud (bản {remote_ts} mới hơn)."


def sync_push(save_dir: Path, timeout: int = GIT_TIMEOUT_S) -> str | None:
    """Commit + push save lên origin. Trả về cảnh báo hoặc None.

    Nếu push lỗi mạng/timeout, ghi dấu pending để retry_pending_push() ở lần
    mở game sau đẩy lại (commit đã nằm ở local, chưa bao giờ lên remote).
    """
    save_dir = Path(save_dir)
    if not cloud_enabled():
        return None
    repo = repo_dir_for(save_dir)
    if not _has_remote(repo):
        return None
    branch = _current_branch(repo)
    if branch is None:
        return None
    files = []
    for name in (SAVE_FILE, BACKUP_FILE):
        if (save_dir / name).exists():
            files.append(f"saves/{name}")
    if not files:
        return None
    try:
        _run_git(repo, ["add", "--", *files], timeout=timeout)
        commit = _run_git(repo, ["commit", "-m", "save: đồng bộ tiến độ Kana Rush"], timeout=timeout)
    except CloudSyncError as exc:
        return f"Cloud: không push được ({exc}) - tiến độ vẫn an toàn cục bộ."
    if commit.returncode != 0:
        return None  # không có gì thay đổi
    try:
        push = _run_git(repo, ["push", "origin", branch], timeout=timeout)
    except CloudSyncError as exc:
        _mark_pending_push(repo)
        return f"Cloud: đã commit nhưng push lỗi ({exc}) - sẽ tự thử lại lần mở game sau."
    if push.returncode != 0:
        return "Cloud: commit xong nhưng push bị từ chối - thử lại lần thoát sau."
    _clear_pending_push(repo)
    return None


def retry_pending_push(
    save_dir: Path,
    *,
    timeout: int = PUSH_RETRY_TIMEOUT_S,
    attempts: int = PUSH_RETRY_ATTEMPTS,
) -> str | None:
    """Đẩy lại commit còn nợ, retry mạng tạm thời trước khi báo lỗi.

    Hàm được gọi trong background worker lúc mở game nên có thể chờ retry mà
    không làm chậm màn hình đầu tiên. Rejection không thể tự giải quyết thì
    xóa pending để tránh lặp cảnh báo vô hạn.
    """
    save_dir = Path(save_dir)
    if not cloud_enabled():
        return None
    repo = repo_dir_for(save_dir)
    if not _has_pending_push(repo):
        return None
    if not _has_remote(repo):
        return None
    branch = _current_branch(repo)
    if branch is None:
        return None
    attempts = max(1, attempts)
    last_error: CloudSyncError | None = None
    for attempt in range(attempts):
        try:
            push = _run_git(repo, ["push", "origin", branch], timeout=timeout)
        except CloudSyncError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(PUSH_RETRY_DELAY_S)
                continue
            return (
                f"Cloud: chưa đẩy được save lên cloud ({last_error}) - "
                "sẽ thử lại lần mở game sau."
            )
        if push.returncode != 0:
            _clear_pending_push(repo)
            return "Cloud: save chưa được đẩy lên cloud (bị từ chối) - kiểm tra git push thủ công."
        _clear_pending_push(repo)
        return None
    return f"Cloud: chưa đẩy được save lên cloud ({last_error}) - sẽ thử lại lần mở game sau."
