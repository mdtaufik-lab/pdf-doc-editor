"""Google Drive: read file IDs from pasted links and download the files.

Downloads go through rclone's `gdrive:` remote, which is already signed in on this Mac, so no
Google Cloud setup (credentials.json) is needed. Native Google Docs come down as .docx.
Files land in Input/ as `<file id>__<original name>`, so the Drive ID is in the filename for
matching. Nothing on Drive is ever changed.
"""
from __future__ import annotations

import glob
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DRIVE_CACHE, RCLONE_REMOTE

ID_RE = re.compile(r"(?:/d/|[?&]id=)([\w-]{20,})")
ATTEMPTS = 3  # rclone's shared Google client is often rate-limited for a minute


@dataclass
class Link:
    text: str
    file_id: str | None  # None: no file ID in the link (invalid_link)


def parse_links(text: str) -> list[Link]:
    """Every link in pasted text (one per line, or separated by spaces/commas), duplicates removed."""
    links, seen = [], set()
    for token in re.split(r"[\s,]+", text.strip()):
        if not token:
            continue
        m = ID_RE.search(token)
        file_id = m.group(1) if m else None
        if file_id and file_id in seen:
            continue
        seen.add(file_id)
        links.append(Link(token, file_id))
    return links


def connected() -> bool:
    rclone = shutil.which("rclone")
    if not rclone:
        return False
    out = subprocess.run([rclone, "listremotes"], capture_output=True, text=True, timeout=30).stdout
    return RCLONE_REMOTE in out.split()


def download(file_id: str, input_dir: Path) -> Path:
    """Path of the downloaded file in input_dir; files already there are not downloaded again."""
    input_dir.mkdir(parents=True, exist_ok=True)
    if existing := _existing(input_dir, file_id):
        return existing
    cached = DRIVE_CACHE / file_id
    if cached.is_dir() and (files := [f for f in cached.iterdir() if f.is_file()]):
        dst = input_dir / f"{file_id}__{files[0].name}"
        shutil.copyfile(files[0], dst)
        return dst

    partial = input_dir / ".partial" / file_id
    error = ""
    for attempt in range(ATTEMPTS):
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        proc = subprocess.run(["rclone", "backend", "copyid", RCLONE_REMOTE, file_id, f"{partial}/", "-q"],
                              capture_output=True, text=True, timeout=600)
        files = [f for f in partial.iterdir() if f.is_file()]
        if files:
            dst = input_dir / f"{file_id}__{files[0].name}"
            shutil.move(files[0], dst)
            shutil.rmtree(partial, ignore_errors=True)
            return dst
        error = _last_error(proc.stderr)
        if attempt + 1 < ATTEMPTS:
            time.sleep(20 * (attempt + 1))
    shutil.rmtree(partial, ignore_errors=True)
    raise RuntimeError(error or "no file came back (no access, or deleted)")


def _existing(input_dir: Path, file_id: str) -> Path | None:
    return next((Path(p) for p in glob.glob(str(input_dir / f"{glob.escape(file_id)}__*"))), None)


def _last_error(stderr: str) -> str:
    lines = [l for l in stderr.splitlines() if "ERROR" in l or "Failed" in l]
    if not lines:
        return ""
    if "notFound" in stderr or "File not found" in stderr:
        return "file not found, or no access with this Google account"
    if "rateLimitExceeded" in stderr or "Quota exceeded" in stderr:
        return "Google Drive rate limit - try again in a few minutes"
    return lines[-1].split(" : ", 1)[-1][:200]
