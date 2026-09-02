"""강의 영상 내려받기.

두 가지 경로를 쓴다.

1. yt-dlp: 강의 플레이어 페이지 주소를 그대로 넣었을 때. 쿠키/로그인 세션을 다룰 수 있다.
2. ffmpeg: 이미 실제 스트림 주소(.m3u8 / .mp4)를 알고 있을 때. 가장 확실하다.

eclass(코스모스/LMS)는 로그인이 필요한 스트림이라서, 브라우저 개발자도구에서 뽑은
m3u8 주소 + Referer + Cookie 조합이 가장 잘 통한다. README 의 "강의 주소 찾기" 참고.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .utils import LecsumError, log, require_binary, run, slugify


@dataclass
class FetchOptions:
    referer: str | None = None
    cookie: str | None = None
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    extra_headers: list[str] = field(default_factory=list)

    def header_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.referer:
            pairs.append(("Referer", self.referer))
        if self.cookie:
            pairs.append(("Cookie", self.cookie))
        for raw in self.extra_headers:
            if ":" not in raw:
                raise LecsumError(f"--header 형식이 잘못됐습니다: {raw!r} (예: 'Origin: https://...')")
            name, _, value = raw.partition(":")
            pairs.append((name.strip(), value.strip()))
        return pairs


def _is_direct_stream(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".m3u8", ".mp4", ".m4a", ".mpd", ".ts", ".mov", ".mkv", ".webm"))


def _download_with_ffmpeg(url: str, dest: Path, opts: FetchOptions) -> Path:
    ffmpeg = require_binary("ffmpeg", "https://ffmpeg.org 에서 설치하거나 `brew install ffmpeg` / `winget install ffmpeg`.")
    cmd = [ffmpeg, "-y", "-loglevel", "warning", "-stats"]

    headers = opts.header_pairs()
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers)]
    cmd += ["-user_agent", opts.user_agent]
    cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto"]
    # 끊긴 세그먼트에서 멈추지 않도록.
    cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
    cmd += ["-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest)]

    run(cmd, quiet=False)
    if not dest.exists() or dest.stat().st_size == 0:
        raise LecsumError("ffmpeg 이 파일을 만들지 못했습니다. 주소나 쿠키가 만료됐을 수 있습니다.")
    return dest


def _download_with_ytdlp(url: str, dest: Path, opts: FetchOptions) -> Path:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise LecsumError(
            "yt-dlp 가 없습니다. `pip install yt-dlp` 로 설치하거나,\n"
            "브라우저 개발자도구에서 .m3u8 주소를 직접 뽑아 넣어 주세요 (README 참고)."
        )
    cmd = [ytdlp, "--no-playlist", "-o", str(dest), "--force-overwrites"]
    if opts.referer:
        cmd += ["--referer", opts.referer]
    if opts.cookies_file:
        cmd += ["--cookies", str(opts.cookies_file)]
    if opts.cookies_from_browser:
        cmd += ["--cookies-from-browser", opts.cookies_from_browser]
    cmd += ["--user-agent", opts.user_agent]
    for name, value in opts.header_pairs():
        if name.lower() == "referer":
            continue
        cmd += ["--add-header", f"{name}:{value}"]
    cmd += [url]

    run(cmd, quiet=False)
    if dest.exists():
        return dest
    # yt-dlp 가 확장자를 바꿔 저장했을 수 있다.
    matches = sorted(dest.parent.glob(dest.stem + ".*"))
    if not matches:
        raise LecsumError("yt-dlp 가 파일을 만들지 못했습니다.")
    return matches[0]


def fetch_video(source: str, workdir: Path, opts: FetchOptions, *, name: str | None = None) -> Path:
    """URL 또는 로컬 경로를 받아 영상 파일 경로를 돌려준다."""
    local = Path(source).expanduser()
    if local.exists():
        log(f"로컬 파일 사용: {local}")
        return local

    if not source.lower().startswith(("http://", "https://")):
        raise LecsumError(f"파일도 URL도 아닙니다: {source}")

    workdir.mkdir(parents=True, exist_ok=True)
    stem = slugify(name or Path(urlparse(source).path).stem or "lecture")
    dest = workdir / f"{stem}.mp4"

    if _is_direct_stream(source):
        log("스트림 주소로 판단 → ffmpeg 으로 내려받습니다.")
        return _download_with_ffmpeg(source, dest, opts)

    log("페이지 주소로 판단 → yt-dlp 로 내려받습니다.")
    return _download_with_ytdlp(source, dest, opts)
