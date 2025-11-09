---

### `comfy_purge_loras/__init__.py`

```python
# comfy-purge-loras
# ComfyUI custom node: Purge LoRAs When Disk Full
# Path: comfy_purge_loras/__init__.py

import math
import time
import shutil
from pathlib import Path
from typing import List, Tuple

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
__version__ = "1.0.0"

# ------------- helpers -------------

def _format_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = min(int(math.log(n, 1024)), len(units) - 1)
    return f"{n / (1024 ** i):.2f} {units[i]}"

def _disk_usage_percent(path: Path) -> float:
    """Return used percentage [0..100] for filesystem holding 'path'."""
    usage = shutil.disk_usage(path)
    used = usage.used
    total = usage.total if usage.total else 1
    return (used / total) * 100.0

def _collect_lora_files(
    loras_dir: Path,
    allowed_suffixes: Tuple[str, ...],
    exclude_names: Tuple[str, ...],
) -> List[Path]:
    """Recursively collect files in loras_dir filtered by extension and excluded name substrings."""
    files: List[Path] = []
    if not loras_dir.exists() or not loras_dir.is_dir():
        return files

    for f in loras_dir.rglob("*"):
        if not f.is_file():
            continue
        if allowed_suffixes and f.suffix.lower() not in allowed_suffixes:
            continue
        name_lower = f.name.lower()
        if any(x in name_lower for x in exclude_names):
            continue
        files.append(f)

    return files

def _mtime_safe(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return time.time()

def _folder_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except FileNotFoundError:
                pass
    return total

def _purge_oldest_until_below(
    root_for_usage: Path,
    loras_dir: Path,
    threshold_percent: float,
    target_percent: float,
    *,
    allowed_suffixes: Tuple[str, ...],
    exclude_names: Tuple[str, ...],
    min_age_seconds: int,
    delete_count_limit: int,
    dry_run: bool,
) -> dict:
    """
    Delete oldest files in LoRAs until disk used% < target_percent.
    Returns summary with before/after usage, files deleted and bytes freed.
    """
    before_used_pct = _disk_usage_percent(root_for_usage)

    if before_used_pct < threshold_percent:
        return {
            "triggered": False,
            "before_used_pct": before_used_pct,
            "after_used_pct": before_used_pct,
            "files_deleted": 0,
            "bytes_freed": 0,
            "log": f"Disk usage OK: {before_used_pct:.2f}% < {threshold_percent:.2f}% (no purge).",
        }

    files = _collect_lora_files(loras_dir, allowed_suffixes, exclude_names)
    files.sort(key=_mtime_safe)

    now = time.time()
    bytes_freed = 0
    files_deleted = 0
    lines = [
        f"Disk usage {before_used_pct:.2f}% ≥ {threshold_percent:.2f}% — purging LoRAs in {loras_dir}...",
        f"Target after purge: < {target_percent:.2f}%",
        f"Allowed suffixes: {', '.join(allowed_suffixes) if allowed_suffixes else '(all)'}",
        f"Excluded name contains: {', '.join(exclude_names) if exclude_names else '(none)'}",
        f"Min age: {min_age_seconds}s, Delete limit: {delete_count_limit or '∞'}, Dry-run: {dry_run}",
        "",
        "Deletions (oldest first):",
    ]

    # Loop deleting oldest until we’re below target or out of files/limits
    for f in files:
        if delete_count_limit and files_deleted >= delete_count_limit:
            lines.append(f"[STOP] Reached delete_count_limit = {delete_count_limit}")
            break

        try:
            st = f.stat()
        except FileNotFoundError:
            continue

        if min_age_seconds > 0 and (now - st.st_mtime) < min_age_seconds:
            # File is too fresh to delete
            continue

        size = st.st_size

        if dry_run:
            files_deleted += 1
            bytes_freed += size
            lines.append(f"[DRY RUN] {f} ({_format_bytes(size)})")
        else:
            try:
                f.unlink(missing_ok=True)
                files_deleted += 1
                bytes_freed += size
                lines.append(f"Deleted {f} ({_format_bytes(size)})")
            except PermissionError:
                lines.append(f"[SKIP: permission] {f}")
            except IsADirectoryError:
                lines.append(f"[SKIP: directory] {f}")
            except Exception as e:
                lines.append(f"[SKIP: {f}] {e}")

        # Re-check usage after each deletion to stop early
        after_used_pct_now = _disk_usage_percent(root_for_usage)
        if after_used_pct_now < target_percent:
            lines.append(f"[OK] Reached target: used {after_used_pct_now:.2f}% < {target_percent:.2f}%")
            break

    after_used_pct = _disk_usage_percent(root_for_usage)
    lines[:0] = [
        f"Freed total: {_format_bytes(bytes_freed)}; Files deleted: {files_deleted}",
        f"Usage before: {before_used_pct:.2f}%, after: {after_used_pct:.2f}%",
        "",
    ]

    return {
        "triggered": True,
        "before_used_pct": before_used_pct,
        "after_used_pct": after_used_pct,
        "files_deleted": files_deleted,
        "bytes_freed": bytes_freed,
        "log": "\n".join(lines),
    }

def _clear_user_trash() -> str:
    """
    Safely clear $HOME/.local/share/Trash/files using shutil.rmtree (no shell).
    Returns a log line including an approximate freed size.
    """
    trash_path = Path.home() / ".local/share/Trash/files"
    if not trash_path.exists():
        return f"Trash folder not found at {trash_path}"

    before = _folder_size(trash_path)
    try:
        shutil.rmtree(trash_path, ignore_errors=True)
        freed = _format_bytes(before)
        return f"Cleared trash at {trash_path}, freed ~{freed}"
    except Exception as e:
        return f"Failed to clear trash at {trash_path}: {e}"

# ------------- ComfyUI Node -------------

class PurgeLoRAsWhenDiskFull:
    """
    If the filesystem holding 'loras_path' is >= threshold_percent used, delete oldest files
    under 'loras_path' (filtered by extensions) until usage < target_percent (hysteresis),
    then clear $HOME/.local/share/Trash/files.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "loras_path": ("STRING", {
                    "default": "ComfyUI/models/loras",
                    "tooltip": "Folder containing your LoRA weights.",
                }),
                "threshold_percent": ("FLOAT", {
                    "default": 90.0,
                    "min": 50.0,
                    "max": 99.9,
                    "step": 0.1,
                    "tooltip": "Start purging when disk used% is >= this value.",
                }),
            },
            "optional": {
                "target_percent": ("FLOAT", {
                    "default": 85.0,
                    "min": 40.0,
                    "max": 99.0,
                    "step": 0.1,
                    "tooltip": "Stop purging once used% falls below this (hysteresis).",
                }),
                "allowed_suffixes_csv": ("STRING", {
                    "default": ".safetensors,.ckpt,.pt,.bin,.onnx",
                    "tooltip": "Comma-separated file extensions to delete.",
                }),
                "exclude_names_csv": ("STRING", {
                    "default": "favorite,keep,_core,essential",
                    "tooltip": "Comma-separated substrings to protect (case-insensitive).",
                }),
                "min_age_minutes": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 525600,
                    "tooltip": "Only delete files older than this many minutes.",
                }),
                "delete_count_limit": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 1000000,
                    "tooltip": "Max files to delete in one run (0 = no limit).",
                }),
                "dry_run": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Simulate deletions and print log without removing files.",
                }),
            },
        }

    RETURN_TYPES = ("STRING", "FLOAT", "FLOAT", "INT", "INT")
    RETURN_NAMES = ("log", "used_before_pct", "used_after_pct", "files_deleted", "bytes_freed")
    FUNCTION = "run"
    CATEGORY = "utils/storage"

    def run(
        self,
        loras_path: str,
        threshold_percent: float,
        target_percent: float = 85.0,
        allowed_suffixes_csv: str = ".safetensors,.ckpt,.pt,.bin,.onnx",
        exclude_names_csv: str = "favorite,keep,_core,essential",
        min_age_minutes: int = 0,
        delete_count_limit: int = 0,
        dry_run: bool = True,
    ):
        loras_dir = Path(loras_path).expanduser().resolve()
        if not loras_dir.exists():
            used = _disk_usage_percent(Path.cwd())
            log = f"[WARN] LoRAs path does not exist: {loras_dir}\nCurrent usage (cwd): {used:.2f}%"
            return (log, float(used), float(used), 0, 0)

        # The FS to check capacity on = the device holding loras_dir
        root_for_usage = loras_dir

        # normalize CSV inputs
        def _csv_list(s: str) -> List[str]:
            return [x.strip() for x in (s or "").split(",") if x.strip()]

        allowed_suffixes = tuple(
            x.lower() if x.startswith(".") else f".{x.lower()}"
            for x in _csv_list(allowed_suffixes_csv)
        )
        exclude_names = tuple(x.lower() for x in _csv_list(exclude_names_csv))

        # enforce a bit of hysteresis if misconfigured
        if target_percent >= threshold_percent:
            target_percent = threshold_percent - 1.0

        # Purge LoRAs if needed
        res = _purge_oldest_until_below(
            root_for_usage=root_for_usage,
            loras_dir=loras_dir,
            threshold_percent=float(threshold_percent),
            target_percent=float(target_percent),
            allowed_suffixes=allowed_suffixes,
            exclude_names=exclude_names,
            min_age_seconds=int(min_age_minutes * 60),
            delete_count_limit=int(delete_count_limit or 0),
            dry_run=bool(dry_run),
        )

        # Always attempt to clear trash; report freed size
        trash_log = _clear_user_trash()
        log = res["log"] + "\n\n" + trash_log

        return (
            log,
            float(res["before_used_pct"]),
            float(res["after_used_pct"]),
            int(res["files_deleted"]),
            int(res["bytes_freed"]),
        )

NODE_CLASS_MAPPINGS = {
    "PurgeLoRAsWhenDiskFull": PurgeLoRAsWhenDiskFull,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PurgeLoRAsWhenDiskFull": "Purge LoRAs When Disk Full",
}
