"""외부 의존성 없이 돌아가는 기본 검증. `python -m pytest` 또는 `python tests/test_signals.py`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lecsum.curlparse import parse_curl
from lecsum.resolve import _endpoint_candidates, find_streams
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


CURL_BASH = """curl 'https://vod.hansung.ac.kr/vod/2026/abc/index.m3u8?tk=x' \\
  -H 'Referer: https://learn.hansung.ac.kr/' \\
  -H 'Cookie: MoodleSession=abc123; lang=ko' \\
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0)' \\
  -H 'Origin: https://learn.hansung.ac.kr' \\
  --compressed"""

CURL_CMD = (
    'curl "https://vod.hansung.ac.kr/vod/abc/index.m3u8" ^\n'
    '  -H "Referer: https://learn.hansung.ac.kr/" ^\n'
    '  -H "Cookie: MoodleSession=abc123"'
)


def test_parse_curl_bash():
    req = parse_curl(CURL_BASH)
    assert req.url == "https://vod.hansung.ac.kr/vod/2026/abc/index.m3u8?tk=x"
    assert req.referer == "https://learn.hansung.ac.kr/"
    assert req.cookie == "MoodleSession=abc123; lang=ko"
    assert req.user_agent.startswith("Mozilla/5.0")
    # Referer/Cookie/UA 는 전용 옵션으로 가고, 나머지만 --header 로 넘어간다.
    assert req.other_headers() == ["Origin: https://learn.hansung.ac.kr"]


def test_parse_curl_windows_cmd():
    req = parse_curl(CURL_CMD)
    assert req.url.endswith("index.m3u8")
    assert req.cookie == "MoodleSession=abc123"


def test_parse_curl_rejects_junk():
    try:
        parse_curl("https://example.com/a.m3u8")
    except Exception as exc:
        assert "cURL" in str(exc)
    else:
        raise AssertionError("cURL 이 아닌 입력을 통과시켰다")


REAL_STREAM = (
    "https://oktop8mo7927.edge.naverncp.com/hls/uobtyoaUJ0eFGC97AE~6WQ__/"
    "459b8e7e-fb20-425e-9f7a-bc31b386a10d/mp4/"
    "459b8e7e-fb20-425e-9f7a-bc31b386a10d.mp4/index.m3u8"
)

PAGE_URL = "https://learn.hansung.ac.kr/mod/vod/viewer.php?id=1183874"


def test_finds_stream_in_escaped_json():
    # JSON 안에서는 슬래시가 이스케이프되어 있다.
    body = '{"source":"' + REAL_STREAM.replace("/", "\\/") + '","type":"hls"}'
    assert find_streams(body) == [REAL_STREAM]


def test_finds_stream_in_plain_html():
    html = f'<video><source src="{REAL_STREAM}" type="application/x-mpegURL"></video>'
    assert find_streams(html) == [REAL_STREAM]


def test_m3u8_wins_over_mp4():
    # 경로에 .mp4 가 들어 있어도 최종 재생목록인 m3u8 을 골라야 한다.
    html = f'<a href="https://cdn.example/x.mp4"></a><script>src="{REAL_STREAM}"</script>'
    assert find_streams(html) == [REAL_STREAM]


def test_endpoint_candidates_skip_side_effects():
    html = (
        '<script src="/mod/vod/js/d5zFAlMi.js"></script>'
        '<script>fetch("/mod/vod/aThGRmZAEeOxhCIACmOLpg.json");'
        'fetch("/mod/vod/action.php");'          # 출석처리 — 건드리면 안 된다
        'fetch("https://other.example/x.json")</script>'  # 다른 사이트 — 제외
    )
    found = _endpoint_candidates(html, PAGE_URL)
    assert found == ["https://learn.hansung.ac.kr/mod/vod/aThGRmZAEeOxhCIACmOLpg.json"]


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
