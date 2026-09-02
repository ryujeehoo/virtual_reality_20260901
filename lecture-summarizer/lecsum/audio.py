"""ffmpeg 으로 음성 트랙만 뽑고, 필요하면 잘라 놓는다."""

from __future__ import annotations

import re
from pathlib import Path

from .utils import LecsumError, log, media_duration, require_binary, run

# 음성인식 모델은 대부분 16 kHz 모노를 기대한다.
SAMPLE_RATE = 16_000


def extract_audio(video: Path, dest: Path) -> Path:
    """영상에서 16 kHz 모노 WAV 를 뽑는다."""
    ffmpeg = require_binary("ffmpeg", "https://ffmpeg.org 에서 설치하세요.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(video),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(dest),
    ])
    if not dest.exists() or dest.stat().st_size == 0:
        raise LecsumError(f"음성 추출에 실패했습니다: {video}")
    log(f"음성 추출 완료: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def to_compressed(wav: Path, dest: Path, *, bitrate: str = "48k") -> Path:
    """업로드 용량을 줄이기 위한 mono m4a. 클라우드 음성인식에 쓴다."""
    ffmpeg = require_binary("ffmpeg", "https://ffmpeg.org 에서 설치하세요.")
    run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(wav),
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "aac", "-b:a", bitrate,
        str(dest),
    ])
    return dest


def split_audio(audio: Path, outdir: Path, *, chunk_seconds: int = 600) -> list[tuple[Path, float]]:
    """오디오를 chunk_seconds 단위로 자르고 (파일, 시작초) 목록을 준다.

    한 시간짜리 강의를 클라우드 음성인식에 통째로 올리면 용량 제한에 걸린다.
    """
    duration = media_duration(audio)
    if duration is not None and duration <= chunk_seconds:
        return [(audio, 0.0)]

    ffmpeg = require_binary("ffmpeg", "https://ffmpeg.org 에서 설치하세요.")
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.glob("chunk_*"):
        stale.unlink()

    pattern = outdir / f"chunk_%04d{audio.suffix}"
    run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(audio),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        "-c", "copy" if audio.suffix != ".wav" else "pcm_s16le",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        str(pattern),
    ])

    chunks: list[tuple[Path, float]] = []
    for path in sorted(outdir.glob(f"chunk_*{audio.suffix}")):
        index = int(re.search(r"(\d+)", path.stem).group(1))
        chunks.append((path, float(index * chunk_seconds)))
    if not chunks:
        raise LecsumError("오디오 분할에 실패했습니다.")
    log(f"{len(chunks)}개 조각으로 분할 ({chunk_seconds}초 단위)")
    return chunks
