"""브라우저 개발자도구의 "Copy as cURL" 결과를 그대로 읽어들인다.

강의 주소를 손으로 뜯어 옮기는 게 제일 번거롭다. Network 탭에서 요청을
우클릭 → Copy → Copy as cURL 한 다음, 그 문자열만 넘기면 URL·Referer·Cookie·
User-Agent 를 알아서 뽑는다.

크롬(bash), 크롬(cmd), 파워셸 형식을 모두 받는다.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .utils import LecsumError

# 스트림 요청이 아닌 것을 골라내기 위한 확장자.
_STREAM_HINTS = (".m3u8", ".mp4", ".mpd", ".m4s", ".ts")


@dataclass
class CurlRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    @property
    def referer(self) -> str | None:
        return self.header("referer")

    @property
    def cookie(self) -> str | None:
        return self.header("cookie")

    @property
    def user_agent(self) -> str | None:
        return self.header("user-agent")

    def other_headers(self) -> list[str]:
        """이미 전용 옵션으로 다루는 것 말고 나머지 헤더."""
        skip = {"referer", "cookie", "user-agent", "host", "content-length", "accept-encoding"}
        return [f"{k}: {v}" for k, v in self.headers.items() if k.lower() not in skip]


def _normalize(raw: str) -> str:
    """줄바꿈 이어쓰기(\\, ^, `)를 없애 한 줄로 만든다."""
    text = raw.strip()
    # 파워셸은 `curl.exe`, cmd 는 `^` 로 줄을 잇는다.
    text = re.sub(r"\^\r?\n", " ", text)
    text = re.sub(r"`\r?\n", " ", text)
    text = re.sub(r"\\\r?\n", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def parse_curl(raw: str) -> CurlRequest:
    """cURL 명령 문자열에서 URL 과 헤더를 뽑는다."""
    text = _normalize(raw)
    if "curl" not in text[:40].lower():
        raise LecsumError("cURL 명령처럼 보이지 않습니다. 'curl ...' 로 시작해야 합니다.")

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        # 윈도우 cmd 형식은 큰따옴표 안의 ^ 이스케이프 때문에 깨질 수 있다.
        tokens = shlex.split(text.replace("^", ""), posix=True)

    url: str | None = None
    headers: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-H", "--header") and index + 1 < len(tokens):
            index += 1
            name, sep, value = tokens[index].partition(":")
            if sep:
                headers[name.strip()] = value.strip()
        elif token in ("-b", "--cookie") and index + 1 < len(tokens):
            index += 1
            headers["Cookie"] = tokens[index]
        elif token in ("-A", "--user-agent") and index + 1 < len(tokens):
            index += 1
            headers["User-Agent"] = tokens[index]
        elif token in ("-e", "--referer") and index + 1 < len(tokens):
            index += 1
            headers["Referer"] = tokens[index]
        elif token.startswith(("http://", "https://")):
            # 첫 번째로 나오는 주소가 요청 대상이다.
            if url is None:
                url = token
        elif token in ("--url",) and index + 1 < len(tokens):
            index += 1
            url = tokens[index]
        index += 1

    if not url:
        raise LecsumError("cURL 명령에서 URL 을 찾지 못했습니다.")
    return CurlRequest(url=url, headers=headers)


def looks_like_stream(url: str) -> bool:
    lowered = url.lower().split("?")[0]
    return lowered.endswith(_STREAM_HINTS)
