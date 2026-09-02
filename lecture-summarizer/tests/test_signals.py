"""외부 의존성 없이 돌아가는 기본 검증. `python -m pytest` 또는 `python tests/test_signals.py`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lecsum.summarize import chunk_transcript
from lecsum.transcribe import Segment, Transcript
from lecsum.transcript_signals import collect_signals
from lecsum.utils import format_timestamp, slugify


def sample() -> Transcript:
    return Transcript([
        Segment(0, 5, "오늘은 렌더링 파이프라인을 배웁니다."),
        Segment(5, 12, "렌더링 파이프라인은 중요합니다. 시험에 나옵니다."),
        Segment(12, 20, "다시 말하면 렌더링 파이프라인의 순서를 외우세요."),
        Segment(20, 30, "그래서 이제 여러분 렌더링 파이프라인 예시를 보겠습니다."),
    ])


def test_timestamp_format():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(75) == "01:15"
    assert format_timestamp(3725) == "1:02:05"
    assert format_timestamp(75.5, srt=True) == "00:01:15,500"


def test_slugify_keeps_hangul():
    assert slugify("가상현실 3주차") == "가상현실-3주차"
    assert slugify("과목/1주차: 개요") == "과목-1주차-개요"
    assert slugify("???") == "lecture"


def test_repeated_term_detected():
    signals = collect_signals(sample(), min_count=2)
    terms = {t.term: t.count for t in signals.terms}
    assert terms.get("렌더링", 0) >= 4
    # 조사가 붙어도 같은 단어로 합쳐진다.
    assert terms.get("파이프라인", 0) >= 4


def test_stopwords_dropped():
    signals = collect_signals(sample(), min_count=1)
    assert "그래서" not in {t.term for t in signals.terms}
    assert "여러분" not in {t.term for t in signals.terms}


def test_cue_detection():
    signals = collect_signals(sample())
    kinds = {c.kind for c in signals.cues}
    assert "시험" in kinds
    assert "암기" in kinds or "되짚기" in kinds


def test_chunking_respects_limit():
    long_transcript = Transcript([Segment(i, i + 1, "가" * 100) for i in range(200)])
    chunks = chunk_transcript(long_transcript, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(body) <= 1200 for _, body in chunks)
    assert chunks[0][0] == 0.0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)
