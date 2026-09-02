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
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

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


def resolve_stream(page_url: str, *, cookie: str | None = None, use_browser: bool = True) -> Resolved:
    """페이지 주소 → 스트림 주소. 못 찾으면 무엇을 하면 되는지 알려주며 실패한다."""
    found = resolve_from_page(page_url, cookie=cookie)
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
    except Exception as exc:  # 브라우저가 실행 중이면 DB 가 잠겨 있을 수 있다.
        log(f"브라우저 쿠키를 꺼내지 못했습니다: {exc}")
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
