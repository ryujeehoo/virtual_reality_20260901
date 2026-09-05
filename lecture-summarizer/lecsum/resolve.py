"""강의 페이지 주소에서 실제 스트림(m3u8) 주소를 찾아낸다.

개발자도구를 손으로 여는 대신 기계가 한다. 순서대로 시도한다.

1. 페이지 HTML 안에 m3u8 주소가 그대로 박혀 있는 경우 → 정규식으로 바로 찾는다.
2. 없으면 페이지가 부르는 JSON/PHP 엔드포인트를 따라가서 그 안을 다시 뒤진다.
   (한성대 LMS 는 viewer.php 가 JS 로 메타데이터 JSON 을 받아오고, 그 안에 주소가 있다.)
3. 그래도 없으면 Playwright 로 실제 브라우저를 띄워 네트워크를 엿본다.
   개발자도구가 하는 일과 정확히 같지만 사람이 안 눌러도 된다.

쿠키는 브라우저에서 자동으로 꺼내 쓴다. 사용자가 할 일은 주소를 넣는 것뿐이다.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

from .utils import LecsumError, log

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# JSON 안에서는 https:\/\/ 로 이스케이프되어 있기도 하다.
M3U8_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+?\.m3u8(?:\?[^\s\"'<>\\)\]]*)?")
MP4_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+?\.mp4(?:\?[^\s\"'<>\\)\]]*)?")
# 2단계에서 따라갈 후보 — 페이지가 부르는 데이터 엔드포인트.
ENDPOINT_RE = re.compile(r"""["']((?:https?://|/)[^"'\s<>]+?\.(?:json|php)(?:\?[^"'\s<>]*)?)["']""")


def _decode(raw: bytes, encoding_header: str | None) -> str:
    if encoding_header == "gzip":
        raw = gzip.decompress(raw)
    elif encoding_header == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


def http_get(url: str, *, cookie: str | None = None, referer: str | None = None, timeout: int = 30) -> str:
    headers = {"User-Agent": DEFAULT_UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"}
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _decode(resp.read(), resp.headers.get("Content-Encoding"))
    except urllib.error.HTTPError as exc:
        raise LecsumError(f"{url} 요청 실패 ({exc.code}). 로그인 쿠키가 필요하거나 만료됐을 수 있습니다.") from exc
    except urllib.error.URLError as exc:
        raise LecsumError(f"{url} 에 연결하지 못했습니다: {exc.reason}") from exc


def _unescape(text: str) -> str:
    return text.replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&")


def find_streams(text: str) -> list[str]:
    """텍스트 덩어리에서 m3u8(우선) 또는 mp4 주소를 뽑는다."""
    body = _unescape(text)
    found = list(dict.fromkeys(M3U8_RE.findall(body)))
    if found:
        return found
    return list(dict.fromkeys(MP4_RE.findall(body)))


LOGIN_HINTS = ("login/index.php", "loginform", "sso.hansung", "id=\"username\"", "로그인이 필요")


def looks_like_login_page(html: str) -> bool:
    """로그인 화면이 대신 돌아왔는지 본다. 쿠키가 안 먹었다는 뜻이다."""
    lowered = html.lower()
    return sum(hint.lower() in lowered for hint in LOGIN_HINTS) >= 1


@dataclass
class Resolved:
    url: str
    referer: str
    how: str


def _endpoint_candidates(html: str, page_url: str) -> list[str]:
    """페이지가 부르는 JSON/PHP 중 같은 사이트 것만 절대주소로."""
    origin = urllib.parse.urlsplit(page_url)
    out: list[str] = []
    for raw in dict.fromkeys(ENDPOINT_RE.findall(_unescape(html))):
        absolute = urllib.parse.urljoin(page_url, raw)
        if urllib.parse.urlsplit(absolute).netloc != origin.netloc:
            continue
        # 로그아웃·출석처리 같은 부작용 있는 것은 건드리지 않는다.
        if re.search(r"(logout|delete|action\.php|attend)", absolute, re.I):
            continue
        out.append(absolute)
    return out[:12]


def resolve_from_page(page_url: str, *, cookie: str | None = None) -> Resolved | None:
    """HTML 과 그 페이지가 부르는 엔드포인트를 뒤져 스트림 주소를 찾는다."""
    origin = urllib.parse.urlsplit(page_url)
    referer = f"{origin.scheme}://{origin.netloc}/"

    log(f"페이지를 읽는 중: {page_url}")
    html = http_get(page_url, cookie=cookie, referer=referer)

    if looks_like_login_page(html):
        raise LecsumError(
            "강의 페이지 대신 로그인 화면이 돌아왔습니다. 로그인 쿠키가 넘어가지 않았습니다.\n"
            + ("  쿠키를 하나도 못 꺼냈습니다.\n" if not cookie else "  쿠키는 보냈지만 거부됐습니다 (만료되었을 수 있습니다).\n")
            + "\n"
            "가장 확실한 방법 — Copy as cURL (설치할 것 없음):\n"
            "  1. 강의 영상을 재생한 채로 F12 → Network 탭\n"
            "  2. 필터에 m3u8 입력 → F5 로 새로고침 → 다시 재생\n"
            "  3. index.m3u8 우클릭 → Copy → Copy as cURL\n"
            "  4. curl.txt 로 저장한 뒤:\n"
            "     lecsum --curl-file curl.txt --title \"제목\""
        )

    streams = find_streams(html)
    if streams:
        log(f"페이지 안에서 바로 찾음: {streams[0][:100]}")
        return Resolved(url=streams[0], referer=page_url, how="page-html")

    candidates = _endpoint_candidates(html, page_url)
    if candidates:
        log(f"페이지에 주소가 없어 {len(candidates)}개 엔드포인트를 따라갑니다.")
    for endpoint in candidates:
        try:
            body = http_get(endpoint, cookie=cookie, referer=page_url, timeout=20)
        except LecsumError:
            continue
        streams = find_streams(body)
        if streams:
            log(f"{endpoint.rsplit('/', 1)[-1][:40]} 안에서 찾음: {streams[0][:100]}")
            return Resolved(url=streams[0], referer=page_url, how=f"endpoint:{endpoint}")
    return None


def resolve_with_browser(page_url: str, *, cookie: str | None = None, timeout_ms: int = 45_000) -> Resolved | None:
    """Playwright 로 실제 브라우저를 띄워 네트워크에서 m3u8 을 낚는다.

    개발자도구 Network 탭을 사람이 보는 것과 같은 일을 자동으로 한다.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log("playwright 가 없어 브라우저 방식은 건너뜁니다. (pip install playwright && playwright install chromium)")
        return None

    origin = urllib.parse.urlsplit(page_url)
    hits: list[str] = []

    log("브라우저를 띄워 네트워크를 관찰합니다 (최대 45초)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_UA)
        if cookie:
            context.add_cookies([
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": origin.netloc,
                    "path": "/",
                }
                for name, _, value in (c.partition("=") for c in cookie.split(";"))
                if name.strip() and value.strip()
            ])

        def on_request(request) -> None:
            if ".m3u8" in request.url or ".mp4" in request.url:
                hits.append(request.url)

        page = context.new_page()
        page.on("request", on_request)
        try:
            page.goto(page_url, timeout=timeout_ms, wait_until="domcontentloaded")
            # 플레이어가 자동재생을 안 하면 가운데를 눌러 본다.
            for _ in range(3):
                page.wait_for_timeout(3000)
                if hits:
                    break
                try:
                    page.mouse.click(page.viewport_size["width"] // 2, page.viewport_size["height"] // 2)
                except Exception:
                    pass
        finally:
            browser.close()

    if not hits:
        return None
    best = next((h for h in hits if ".m3u8" in h), hits[0])
    log(f"브라우저가 잡아냄: {best[:100]}")
    return Resolved(url=best, referer=page_url, how="browser")


def resolve_stream(
    page_url: str,
    *,
    cookie: str | None = None,
    use_browser: bool = True,
    browser: str | None = None,
) -> Resolved:
    """페이지 주소 → 스트림 주소. 못 찾으면 무엇을 하면 되는지 알려주며 실패한다."""
    if cookie:
        # 쿠키를 손에 넣었을 때만 HTTP 로 먼저 시도한다. 없으면 로그인 화면만 온다.
        found = resolve_from_page(page_url, cookie=cookie)
        if found:
            return found

    if use_browser and browser:
        # 쿠키를 꺼낼 수 없는 브라우저(크롬 127+)면 그 브라우저 본인에게 시킨다.
        found = resolve_with_real_browser(page_url, browser)
        if found:
            return found
    if use_browser:
        found = resolve_with_browser(page_url, cookie=cookie)
        if found:
            return found
    raise LecsumError(
        "이 페이지에서 스트림 주소를 찾지 못했습니다.\n"
        "  - 로그인 쿠키가 필요할 수 있습니다: --browser chrome 을 붙여 보세요.\n"
        "  - 브라우저 자동 관찰을 쓰려면: pip install playwright && playwright install chromium\n"
        "  - 그래도 안 되면 개발자도구에서 Copy as cURL 후 --curl-file 로 넘겨 주세요."
    )


def user_profile_dir(browser: str) -> Path | None:
    """설치된 브라우저의 프로필 폴더. 여기를 그대로 열면 로그인 상태가 살아 있다."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        table = {
            "chrome": local / "Google/Chrome/User Data",
            "edge": local / "Microsoft/Edge/User Data",
            "whale": local / "Naver/Naver Whale/User Data",
            "brave": local / "BraveSoftware/Brave-Browser/User Data",
        }
    elif sys.platform == "darwin":
        support = home / "Library/Application Support"
        table = {
            "chrome": support / "Google/Chrome",
            "edge": support / "Microsoft Edge",
            "whale": support / "Naver/Whale",
            "brave": support / "BraveSoftware/Brave-Browser",
        }
    else:
        table = {
            "chrome": home / ".config/google-chrome",
            "edge": home / ".config/microsoft-edge",
            "brave": home / ".config/BraveSoftware/Brave-Browser",
        }
    path = table.get(browser.lower())
    return path if path and path.is_dir() else None


# Playwright 가 이 브라우저를 직접 실행할 수 있는지.
PLAYWRIGHT_CHANNEL = {"chrome": "chrome", "edge": "msedge", "brave": "chrome", "whale": "chrome"}


def resolve_with_real_browser(
    page_url: str,
    browser: str,
    *,
    timeout_ms: int = 60_000,
    headless: bool = False,
) -> Resolved | None:
    """사용자가 쓰는 브라우저를 그 프로필 그대로 띄워 m3u8 을 잡는다.

    크롬 127+ 는 쿠키를 자기만 풀 수 있게 잠갔다. 그러면 쿠키를 꺼내오는 대신
    크롬 본인에게 시키면 된다. 프로필을 그대로 쓰므로 로그인 상태가 살아 있고,
    복호화도 크롬이 알아서 한다.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise LecsumError(
            "이 방법은 Playwright 가 필요합니다. 한 번만 설치하면 됩니다.\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "\n"
            "설치가 싫으면 Copy as cURL 방식을 쓰세요 (설치 없이 바로 됩니다).\n"
            "  lecsum --curl-file curl.txt --title \"제목\""
        )

    profile = user_profile_dir(browser)
    if profile is None:
        raise LecsumError(f"{browser} 프로필 폴더를 찾지 못했습니다. 설치되어 있나요?")

    hits: list[str] = []
    log(f"{browser} 를 프로필째 띄웁니다: {profile}")
    log(f"※ {browser} 가 실행 중이면 먼저 완전히 종료해 주세요.")

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(
                str(profile),
                channel=PLAYWRIGHT_CHANNEL.get(browser.lower(), "chrome"),
                headless=headless,
                args=["--profile-directory=Default", "--mute-audio"],
                no_viewport=True,
            )
        except Exception as exc:
            if "ProcessSingleton" in str(exc) or "already running" in str(exc).lower():
                raise LecsumError(
                    f"{browser} 가 이미 실행 중이라 프로필을 열 수 없습니다.\n"
                    f"{browser} 를 완전히 종료(트레이 아이콘까지)한 뒤 다시 실행하세요."
                ) from exc
            raise LecsumError(f"{browser} 를 띄우지 못했습니다: {exc}") from exc

        def on_request(request) -> None:
            if ".m3u8" in request.url or ".mp4" in request.url:
                hits.append(request.url)

        page = context.pages[0] if context.pages else context.new_page()
        page.on("request", on_request)
        try:
            page.goto(page_url, timeout=timeout_ms, wait_until="domcontentloaded")
            for _ in range(6):
                page.wait_for_timeout(3000)
                if any(".m3u8" in h for h in hits):
                    break
                try:  # 자동재생이 아니면 눌러 본다.
                    page.mouse.click(640, 360)
                except Exception:
                    pass
        finally:
            context.close()

    if not hits:
        return None
    best = next((h for h in hits if ".m3u8" in h), hits[0])
    log(f"잡았습니다: {best[:100]}")
    return Resolved(url=best, referer=page_url, how=f"{browser}-profile")


def browser_cookie_header(browser: str, domain: str) -> str | None:
    """설치된 브라우저에서 해당 도메인 쿠키를 직접 꺼낸다.

    사용자가 쿠키 문자열을 어디에도 붙여넣지 않아도 되게 하는 게 핵심이다.
    yt-dlp 의 추출기를 재사용한다 (크롬/엣지/파이어폭스/웨일/사파리).
    """
    try:
        from yt_dlp.cookies import extract_cookies_from_browser  # type: ignore
    except ImportError:
        log("yt-dlp 가 없어 브라우저 쿠키를 꺼내지 못했습니다. (pip install yt-dlp)")
        return None

    try:
        jar = extract_cookies_from_browser(browser.lower())
    except Exception as exc:
        # 크롬 127+ 는 앱 바운드 암호화로 쿠키를 잠근다. 크롬 자신 말고는 못 읽는다.
        if "DPAPI" in str(exc) or "decrypt" in str(exc).lower():
            raise LecsumError(
                f"{browser} 의 쿠키를 읽을 수 없습니다 (앱 바운드 암호화).\n"
                "크롬 127 버전부터 쿠키를 크롬 자신만 풀 수 있게 잠갔습니다. 우회할 방법이 없습니다.\n"
                "\n"
                "다음 중 하나를 쓰세요.\n"
                "  1) Copy as cURL — 지금 바로 됩니다. 설치할 것 없습니다.\n"
                "     영상 재생 → F12 → Network → 필터 m3u8 → F5 →\n"
                "     index.m3u8 우클릭 → Copy → Copy as cURL → curl.txt 로 저장\n"
                "     lecsum --curl-file curl.txt --title \"제목\"\n"
                "\n"
                "  2) 파이어폭스로 LMS 에 로그인한 뒤 --browser firefox\n"
                "     (파이어폭스는 이 잠금을 쓰지 않습니다)"
            ) from exc
        # 브라우저가 실행 중이면 쿠키 DB 가 잠겨 있을 수 있다.
        log(f"브라우저 쿠키를 꺼내지 못했습니다: {exc}")
        log(f"{browser} 를 완전히 종료한 뒤 다시 실행해 보세요.")
        return None

    host = domain.lower()
    pairs = []
    for cookie in jar:
        cookie_domain = cookie.domain.lstrip(".").lower()
        if host == cookie_domain or host.endswith("." + cookie_domain):
            pairs.append(f"{cookie.name}={cookie.value}")

    if not pairs:
        log(f"{browser} 에서 {domain} 쿠키를 찾지 못했습니다. 그 브라우저로 로그인되어 있나요?")
        return None
    log(f"{browser} 에서 {domain} 쿠키 {len(pairs)}개를 꺼냈습니다.")
    return "; ".join(pairs)
