"""ffmpeg 으로 음성 트랙만 뽑고, 필요하면 잘라 놓는다."""

from __future__ import annotations

from pathlib import Path

from .utils import LecsumError, log, media_duration, require_binary, run

# 음성인식 모델은 대부분 16 kHz 모노를 기대한다.
SAMPLE_RATE = 16_000

# 이보다 짧은 조각은 버린다. ffmpeg 가 끝에 남기는 꼬리 조각을 걸러내기 위한 값.
MIN_CHUNK_SECONDS = 1.0


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
    # 스트림 복사와 -ac/-ar 은 같이 쓸 수 없다 (복사하면 리샘플이 조용히 무시된다).
    # 어차피 16 kHz 모노로 맞춰 두려는 것이므로 다시 인코딩한다.
    codec = ["-c:a", "pcm_s16le"] if audio.suffix == ".wav" else ["-c:a", "aac", "-b:a", "48k"]
    run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(audio),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1",
        *codec,
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        str(pattern),
    ])

    # 시작 시각은 실제 길이를 누적해서 구한다. 조각 경계는 요청한 값과 조금씩
    # 어긋나기 때문에, index * chunk_seconds 로 계산하면 뒤로 갈수록 자막이 밀린다.
    chunks: list[tuple[Path, float]] = []
    offset = 0.0
    for path in sorted(outdir.glob(f"chunk_*{audio.suffix}")):
        length = media_duration(path)
        # ffmpeg 가 끝에 만드는 0.0x 초짜리 꼬리 조각은 버린다.
        # 인식할 내용이 없는데 클라우드 백엔드에서는 요청 하나를 더 쓰고,
        # 너무 짧은 오디오를 거부하는 서비스에서는 강의 끝에서 전체가 실패한다.
        if length is not None and length < MIN_CHUNK_SECONDS:
            path.unlink(missing_ok=True)
            continue
        chunks.append((path, offset))
        offset += length if length is not None else float(chunk_seconds)

    if not chunks:
        raise LecsumError("오디오 분할에 실패했습니다.")
    log(f"{len(chunks)}개 조각으로 분할 ({chunk_seconds}초 단위)")
    return chunks
