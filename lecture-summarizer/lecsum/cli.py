"""명령줄 진입점.

    lecsum "https://.../lecture.m3u8" --title "컴퓨터그래픽스 3주차"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio import extract_audio
from .config import Settings, load_env
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
      --referer "https://eclass.hansung.ac.kr/" --cookie "$(cat cookie.txt)"

  lecsum ./내려받은강의.mp4 --title "가상현실 3주차"

  lecsum ./강의.mp4 --skip-summary        # 텍스트만
  lecsum ./transcript.json --from-transcript --title "..."   # 요약만 다시
""",
    )
    parser.add_argument("source", help="강의 영상 URL(.m3u8/.mp4/플레이어 페이지) 또는 로컬 파일")
    parser.add_argument("--title", help="노트 제목 (기본: 파일 이름)")
    parser.add_argument("-o", "--outdir", type=Path, help="결과 폴더 (기본: out/)")

    net = parser.add_argument_group("네트워크 / 로그인")
    net.add_argument("--referer", help="스트림이 요구하는 Referer (보통 eclass 주소)")
    net.add_argument("--cookie", help="Cookie 헤더 값 전체")
    net.add_argument("--cookies-file", type=Path, help="Netscape 형식 쿠키 파일 (yt-dlp 용)")
    net.add_argument(
        "--cookies-from-browser",
        help="브라우저에서 쿠키 자동 추출 (chrome/edge/firefox/whale ...)",
    )
    net.add_argument("--header", action="append", default=[], help="추가 헤더. 'Origin: https://...' 형식")

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


def run_pipeline(args: argparse.Namespace) -> int:
    load_env()
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
