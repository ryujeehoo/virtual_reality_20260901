"""명령줄 진입점.

    lecsum "https://.../lecture.m3u8" --title "컴퓨터그래픽스 3주차"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .audio import extract_audio
from .config import Settings, load_env
from .curlparse import parse_curl
from .resolve import browser_cookie_header
from .download import FetchOptions, fetch_video
from .summarize import summarize
from .transcribe import Transcript, transcribe
from .transcript_signals import collect_signals
from .utils import LecsumError, log, slugify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lecsum",
        description="강의 영상 주소를 넣으면 받아서 → 텍스트로 옮기고 → 시험 대비 노트로 정리합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시
  lecsum "https://cdn.example/vod/abc.m3u8" --title "가상현실 3주차" \\
      --referer "https://learn.hansung.ac.kr/" --cookie "$(cat cookie.txt)"

  # 강의 페이지 주소만. 쿠키는 브라우저에서 알아서 꺼내 씁니다.
  lecsum "https://learn.hansung.ac.kr/mod/vod/viewer.php?id=1183874" \
      --browser chrome --title "명품자바 1장"

  # 자동 탐색이 막히면 개발자도구에서 Copy as cURL 한 걸 넘기면 끝
  lecsum --curl-file curl.txt --title "가상현실 3주차"

  lecsum ./내려받은강의.mp4 --title "가상현실 3주차"

  lecsum ./강의.mp4 --skip-summary        # 텍스트만
  lecsum ./transcript.json --from-transcript --title "..."   # 요약만 다시
""",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="강의 영상 URL(.m3u8/.mp4/플레이어 페이지) 또는 로컬 파일. --curl-file 을 쓰면 생략",
    )
    parser.add_argument("--title", help="노트 제목 (기본: 파일 이름)")
    parser.add_argument("-o", "--outdir", type=Path, help="결과 폴더 (기본: out/)")

    net = parser.add_argument_group("네트워크 / 로그인")
    net.add_argument("--referer", help="스트림이 요구하는 Referer (보통 LMS 주소)")
    net.add_argument("--cookie", help="Cookie 헤더 값 전체")
    net.add_argument("--cookies-file", type=Path, help="Netscape 형식 쿠키 파일 (yt-dlp 용)")
    net.add_argument(
        "--browser",
        help="로그인해 둔 브라우저 이름 (chrome/edge/firefox/whale/safari). "
             "쿠키를 직접 꺼내 쓰므로 쿠키를 손으로 옮길 필요가 없습니다.",
    )
    net.add_argument(
        "--cookies-from-browser",
        help="yt-dlp 에 넘길 브라우저 이름. 보통은 --browser 만 쓰면 됩니다.",
    )
    net.add_argument(
        "--no-sniff",
        action="store_true",
        help="스트림 주소를 못 찾아도 브라우저(Playwright)를 띄우지 않는다",
    )
    net.add_argument("--header", action="append", default=[], help="추가 헤더. 'Origin: https://...' 형식")
    net.add_argument(
        "--curl-file",
        type=Path,
        help="개발자도구에서 'Copy as cURL' 한 내용을 저장한 파일. "
             "URL·Referer·Cookie 를 알아서 뽑아 씁니다. ('-' 이면 표준입력)",
    )

    asr = parser.add_argument_group("음성 인식")
    asr.add_argument("--asr", choices=["whisper", "openai"], help="백엔드 (기본: whisper, 로컬)")
    asr.add_argument("--whisper-model", help="tiny/base/small/medium/large-v3 (기본: large-v3)")
    asr.add_argument("--device", choices=["auto", "cpu", "cuda"], help="whisper 실행 장치")
    asr.add_argument("--language", help="강의 언어 (기본: ko)")

    llm = parser.add_argument_group("요약")
    llm.add_argument("--model", help="NVIDIA NIM 모델 이름")

    flow = parser.add_argument_group("단계 건너뛰기")
    flow.add_argument("--skip-summary", action="store_true", help="텍스트까지만 만들고 끝낸다")
    flow.add_argument(
        "--from-transcript",
        action="store_true",
        help="source 를 이미 만들어 둔 transcript.json 으로 보고 요약만 다시 한다",
    )
    flow.add_argument("--keep-video", action="store_true", help="내려받은 영상 파일을 지우지 않는다")
    return parser


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.asr:
        settings.asr_backend = args.asr
    if args.whisper_model:
        settings.whisper_model = args.whisper_model
    if args.device:
        settings.whisper_device = args.device
    if args.language:
        settings.language = args.language
    if args.model:
        settings.llm_model = args.model
    if args.outdir:
        settings.outdir = args.outdir
    return settings


def _load_transcript(path: Path) -> Transcript:
    import json

    from .transcribe import Segment

    data = json.loads(path.read_text(encoding="utf-8"))
    return Transcript([Segment(start=d["start"], end=d["end"], text=d["text"]) for d in data])


def _apply_curl_file(args: argparse.Namespace) -> None:
    """--curl-file 내용에서 URL 과 헤더를 채운다. 직접 준 옵션이 우선한다."""
    if not args.curl_file:
        return
    raw = sys.stdin.read() if str(args.curl_file) == "-" else args.curl_file.read_text(encoding="utf-8")
    request = parse_curl(raw)

    args.source = args.source or request.url
    args.referer = args.referer or request.referer
    args.cookie = args.cookie or request.cookie
    args.header = list(args.header) + request.other_headers()
    log(f"cURL 에서 주소를 읽었습니다: {request.url[:90]}")
    if not request.cookie:
        log("경고: cURL 에 Cookie 헤더가 없습니다. 로그인이 필요한 강의라면 403 이 날 수 있습니다.")


def _apply_browser_cookies(args: argparse.Namespace) -> None:
    """--browser 가 주어지면 그 브라우저에서 강의 사이트 쿠키를 직접 꺼낸다.

    사용자가 쿠키 문자열을 어디에도 붙여넣지 않아도 되게 하는 부분이다.
    """
    if not args.browser or args.cookie or not args.source:
        return
    if not args.source.lower().startswith(("http://", "https://")):
        return
    domain = urlsplit(args.source).netloc
    args.cookie = browser_cookie_header(args.browser, domain)
    args.cookies_from_browser = args.cookies_from_browser or args.browser


def run_pipeline(args: argparse.Namespace) -> int:
    load_env()
    _apply_curl_file(args)
    if not args.source:
        raise LecsumError("강의 주소나 파일을 지정하세요. (또는 --curl-file 사용)")
    _apply_browser_cookies(args)
    settings = _apply_overrides(Settings.from_env(), args)

    title = args.title or Path(args.source).stem or "강의 노트"
    workdir = settings.outdir / slugify(title)
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"결과 폴더: {workdir}")

    if args.from_transcript:
        transcript = _load_transcript(Path(args.source))
        log(f"녹취록 불러옴: {len(transcript.segments)}개 구간")
    else:
        # 1. 내려받기
        opts = FetchOptions(
            referer=args.referer,
            cookie=args.cookie,
            cookies_file=args.cookies_file,
            cookies_from_browser=args.cookies_from_browser,
            extra_headers=args.header,
            use_browser=not args.no_sniff,
        )
        video = fetch_video(args.source, workdir, opts, name=title)

        # 2. 음성 추출
        audio = extract_audio(video, workdir / "audio.wav")

        # 3. 음성 인식
        transcript = transcribe(audio, settings, workdir)
        if not transcript.segments:
            raise LecsumError("음성에서 아무 말도 인식하지 못했습니다. 오디오 트랙을 확인해 주세요.")

        (workdir / "transcript.json").write_text(transcript.to_json(), encoding="utf-8")
        (workdir / "transcript.srt").write_text(transcript.as_srt(), encoding="utf-8")

        if not args.keep_video and video.parent == workdir:
            video.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)

    (workdir / "transcript.txt").write_text(transcript.as_timestamped(), encoding="utf-8")
    log(f"녹취록 저장: {workdir / 'transcript.txt'}")

    if args.skip_summary:
        signals = collect_signals(transcript)
        (workdir / "signals.md").write_text(signals.render(), encoding="utf-8")
        log("요약은 건너뛰었습니다. (--skip-summary)")
        return 0

    # 4. 요약
    note, signals = summarize(transcript, settings, title=title)
    (workdir / "signals.md").write_text(signals.render(), encoding="utf-8")
    summary_path = workdir / "summary.md"
    summary_path.write_text(note + "\n", encoding="utf-8")

    log(f"완료! → {summary_path}")
    print(note)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_pipeline(args)
    except LecsumError as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
