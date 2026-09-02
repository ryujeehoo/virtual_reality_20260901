"""음성 → 텍스트.

백엔드 두 가지:

* ``whisper`` (기본): faster-whisper 를 로컬에서 돌린다. 한국어 강의는 이쪽이 가장 정확하다.
* ``openai``: OpenAI 호환 ``/audio/transcriptions`` 엔드포인트에 올린다.
  (NVIDIA 를 포함해 이 규격을 제공하는 서비스면 base URL 만 바꿔 끼우면 된다.)

NVIDIA API 키는 기본적으로 '요약' 단계(summarize.py)에서 쓴다. NVIDIA 의 호스팅
음성인식 모델은 영어 중심이라, 한국어 강의는 로컬 whisper + NVIDIA LLM 요약 조합이
가장 결과가 좋다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .audio import split_audio, to_compressed
from .config import Settings
from .utils import LecsumError, format_timestamp, log


@dataclass
class Segment:
    start: float
    end: float
    text: str


class Transcript:
    def __init__(self, segments: list[Segment]):
        self.segments = [s for s in segments if s.text.strip()]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    def as_timestamped(self) -> str:
        return "\n".join(f"[{format_timestamp(s.start)}] {s.text.strip()}" for s in self.segments)

    def as_srt(self) -> str:
        blocks = []
        for i, s in enumerate(self.segments, start=1):
            blocks.append(
                f"{i}\n"
                f"{format_timestamp(s.start, srt=True)} --> {format_timestamp(s.end, srt=True)}\n"
                f"{s.text.strip()}\n"
            )
        return "\n".join(blocks)

    def to_json(self) -> str:
        return json.dumps(
            [{"start": s.start, "end": s.end, "text": s.text} for s in self.segments],
            ensure_ascii=False,
            indent=2,
        )


def _transcribe_whisper(audio: Path, settings: Settings) -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:  # pragma: no cover - 설치 안내용
        raise LecsumError(
            "faster-whisper 가 설치되어 있지 않습니다.\n"
            "  pip install faster-whisper\n"
            "또는 --asr openai 로 클라우드 음성인식을 쓰세요."
        ) from exc

    device = settings.whisper_device
    compute_type = settings.whisper_compute_type
    if device == "auto":
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    log(f"whisper 모델 로딩: {settings.whisper_model} ({device}/{compute_type})")
    model = WhisperModel(settings.whisper_model, device=device, compute_type=compute_type)

    raw, info = model.transcribe(
        str(audio),
        language=settings.language or None,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=False,  # 긴 강의에서 같은 문장이 반복 출력되는 걸 막는다.
    )
    log(f"인식 언어: {info.language} (확률 {info.language_probability:.2f})")

    segments: list[Segment] = []
    for seg in raw:
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text))
        if len(segments) % 50 == 0:
            log(f"  ... {format_timestamp(seg.end)} 까지 인식")
    return Transcript(segments)


def _post_audio(url: str, api_key: str, model: str, language: str, path: Path) -> dict:
    boundary = "----lecsum-boundary-7f3a2b"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )

    field("model", model)
    if language:
        field("language", language)
    field("response_format", "verbose_json")

    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise LecsumError(f"음성인식 API 오류 {exc.code}: {detail}") from exc


def _transcribe_openai_compatible(audio: Path, settings: Settings, workdir: Path) -> Transcript:
    base = (settings.asr_base_url or "").rstrip("/")
    key = settings.asr_api_key or settings.nvidia_api_key
    if not base or not key:
        raise LecsumError(
            "--asr openai 를 쓰려면 LECSUM_ASR_BASE_URL 과 LECSUM_ASR_API_KEY 가 필요합니다."
        )
    url = f"{base}/audio/transcriptions"

    compressed = to_compressed(audio, workdir / "audio.m4a")
    chunks = split_audio(compressed, workdir / "chunks", chunk_seconds=600)

    segments: list[Segment] = []
    for index, (chunk, offset) in enumerate(chunks, start=1):
        log(f"업로드 {index}/{len(chunks)} ({format_timestamp(offset)}~)")
        payload = _post_audio(url, key, settings.asr_model, settings.language, chunk)
        raw_segments = payload.get("segments")
        if raw_segments:
            for seg in raw_segments:
                segments.append(
                    Segment(
                        start=float(seg.get("start", 0.0)) + offset,
                        end=float(seg.get("end", 0.0)) + offset,
                        text=str(seg.get("text", "")),
                    )
                )
        else:  # verbose_json 을 지원하지 않는 서버
            segments.append(Segment(start=offset, end=offset + 600, text=str(payload.get("text", ""))))
    return Transcript(segments)


def transcribe(audio: Path, settings: Settings, workdir: Path) -> Transcript:
    if settings.asr_backend == "whisper":
        return _transcribe_whisper(audio, settings)
    if settings.asr_backend == "openai":
        return _transcribe_openai_compatible(audio, settings, workdir)
    raise LecsumError(f"알 수 없는 음성인식 백엔드: {settings.asr_backend}")
