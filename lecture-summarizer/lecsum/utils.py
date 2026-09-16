"""공용 헬퍼."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


class LecsumError(RuntimeError):
    """사용자에게 그대로 보여줄 수 있는 오류."""


def log(message: str) -> None:
    print(f"[lecsum] {message}", file=sys.stderr, flush=True)


def require_binary(name: str, hint: str) -> str:
    path = shutil.which(name)
    if not path:
        raise LecsumError(f"'{name}' 을(를) 찾을 수 없습니다. {hint}")
    return path


def run(cmd: list[str], *, quiet: bool = True) -> None:
    """외부 명령 실행. 실패하면 stderr 를 포함한 오류를 던진다."""
    log("$ " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-25:]
        raise LecsumError(
            f"명령이 실패했습니다 (exit {proc.returncode}): {' '.join(cmd[:3])} ...\n"
            + "\n".join(tail)
        )


def encode_url(url: str) -> str:
    """URL 에 든 한글·공백 등 비ASCII 문자를 퍼센트 인코딩한다.

    urllib 은 비ASCII 가 섞인 주소를 그대로 받으면 UnicodeEncodeError 로 죽는다.
    브라우저에서 복사한 주소에는 한글 파일명이 들어 있을 수 있다.
    이미 인코딩된 %XX 는 그대로 둔다(safe 에 % 포함).
    """
    split = urlsplit(url)
    return urlunsplit((
        split.scheme,
        split.netloc.encode("idna").decode("ascii") if not split.netloc.isascii() else split.netloc,
        quote(split.path, safe="/%:@!$&'()*+,;=~"),
        quote(split.query, safe="=&/%:@!$'()*+,;~?"),
        quote(split.fragment, safe="/%:@!$&'()*+,;=~?"),
    ))


def slugify(text: str, fallback: str = "lecture") -> str:
    text = unicodedata.normalize("NFC", text).strip()
    text = re.sub(r"[\s/\\]+", "-", text)
    text = re.sub(r"[^0-9A-Za-z가-힣._-]", "", text)
    text = text.strip("-._")
    return text[:80] or fallback


def format_timestamp(seconds: float, *, srt: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if srt:
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def media_duration(path: Path) -> float | None:
    """ffprobe 로 길이(초)를 얻는다. 실패하면 None."""
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None
