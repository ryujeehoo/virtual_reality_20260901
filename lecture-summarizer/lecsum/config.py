"""환경 변수와 실행 옵션."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# NIM 카탈로그(build.nvidia.com)에서 고를 수 있는 모델. 한국어 요약 품질이 괜찮은 쪽.
DEFAULT_LLM_MODEL = "meta/llama-3.3-70b-instruct"

# faster-whisper 모델 이름. 한국어는 large-v3 가 확실히 낫지만 무겁다.
DEFAULT_WHISPER_MODEL = "large-v3"


def _load_dotenv(path: Path) -> None:
    """의존성 없이 .env 를 읽어 os.environ 에 채운다(이미 있는 값은 유지)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_env(explicit: Path | None = None) -> None:
    """현재 폴더와 프로젝트 루트의 .env 를 읽는다."""
    for candidate in (explicit, Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if candidate is not None:
            _load_dotenv(candidate)


@dataclass
class Settings:
    nvidia_api_key: str | None = None
    llm_model: str = DEFAULT_LLM_MODEL
    base_url: str = NVIDIA_BASE_URL

    asr_backend: str = "whisper"  # whisper | openai
    whisper_model: str = DEFAULT_WHISPER_MODEL
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    language: str = "ko"

    # asr_backend == "openai" 일 때 쓰는 OpenAI 호환 음성인식 엔드포인트
    asr_base_url: str | None = None
    asr_api_key: str | None = None
    asr_model: str = "whisper-1"

    outdir: Path = field(default_factory=lambda: Path("out"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
            llm_model=os.getenv("LECSUM_LLM_MODEL", DEFAULT_LLM_MODEL),
            base_url=os.getenv("LECSUM_LLM_BASE_URL", NVIDIA_BASE_URL),
            asr_backend=os.getenv("LECSUM_ASR_BACKEND", "whisper"),
            whisper_model=os.getenv("LECSUM_WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
            whisper_device=os.getenv("LECSUM_WHISPER_DEVICE", "auto"),
            whisper_compute_type=os.getenv("LECSUM_WHISPER_COMPUTE_TYPE", "auto"),
            language=os.getenv("LECSUM_LANGUAGE", "ko"),
            asr_base_url=os.getenv("LECSUM_ASR_BASE_URL"),
            asr_api_key=os.getenv("LECSUM_ASR_API_KEY"),
            asr_model=os.getenv("LECSUM_ASR_MODEL", "whisper-1"),
            outdir=Path(os.getenv("LECSUM_OUTDIR", "out")),
        )
