"""NVIDIA NIM(OpenAI 호환) LLM 으로 강의록을 시험 대비 노트로 만든다.

긴 강의는 한 번에 못 넣으니 map-reduce 로 처리한다.
  1) 10~15분 단위로 잘라 각 구간 노트를 만든다 (map)
  2) 구간 노트를 모아 최종 정리본을 만든다 (reduce)

여기에 더해, LLM 에 넘기기 전에 코드로 '반복 신호'를 뽑는다.
  - 교수님이 여러 번 말한 용어의 빈도와 등장 시각
  - "시험에 나온다", "중요합니다", "꼭 기억" 같은 강조 표현의 위치
이 근거를 프롬프트에 같이 넣어야 ⭐표시가 감이 아니라 실제 데이터가 된다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Settings
from .transcript_signals import ExamSignals, collect_signals
from .transcribe import Transcript
from .utils import LecsumError, format_timestamp, log

MAP_PROMPT = """당신은 대학 강의를 듣고 노트를 정리해 주는 조교입니다.
아래는 강의 녹취록의 한 구간입니다. 음성인식이라 오타나 잘못 들린 단어가 있을 수 있으니
문맥으로 자연스럽게 바로잡아 읽으세요.

이 구간에 대해 다음을 한국어로 작성하세요.

## 구간 요약
- 3~6개 불릿. 학생이 읽고 바로 이해할 수 있게 쉬운 말로.

## 등장한 개념
- 개념명 — 한 줄 설명 (강의에서 설명한 그대로)

## 교수님이 강조한 부분
- 강조 표현("중요", "시험", "꼭", "다시 말하지만")이나 반복 설명이 있으면 [시각]과 함께 적기.
- 없으면 "없음".

구간 시작 시각: {start}
---
{chunk}
---
"""

REDUCE_PROMPT = """당신은 대학 강의 노트를 정리해 주는 조교입니다.
아래는 한 강의를 구간별로 정리한 노트들과, 녹취록에서 기계적으로 뽑은 반복/강조 통계입니다.
이걸 합쳐서 하나의 완성된 학습 노트를 한국어로 작성하세요.

작성 규칙
- 처음 배우는 학생이 읽어도 이해되게 쉬운 말로 쓰되, 용어 자체는 정확히 유지한다.
- 근거 없는 내용을 지어내지 않는다. 녹취록에 없는 건 쓰지 않는다.
- 시각 표기는 [12:34] 형식으로, 해당 내용이 나온 지점을 붙인다.

다음 구조를 정확히 지켜서 마크다운으로 출력하세요.

# {title}

## 1. 한 줄 요약
이 강의가 결국 무슨 이야기였는지 두세 문장.

## 2. 오늘의 흐름
강의 전개를 5~8단계로. 각 항목에 [시각].

## 3. 핵심 개념 정리
개념마다 소제목(###)을 두고, "무엇인가 / 왜 필요한가 / 예시" 세 줄로 설명.

## 4. ⭐ 시험에 나올 가능성이 높은 것
아래 통계에서 반복 횟수가 많거나 강조 표현이 붙은 항목을 우선한다.
각 항목은 다음 형식으로:
- **항목** — 왜 중요한지 한 줄 / 언급 횟수·시각 / 예상되는 출제 형태

## 5. 용어 사전
| 용어 | 뜻 | 처음 나온 시각 |

## 6. 스스로 점검하는 질문 5개
답까지 접어서 함께 적는다. 형식:
1. 질문
   - 답:

## 7. 더 공부해야 할 것
강의에서 "다음 시간에", "따로 찾아보라"고 한 것들. 없으면 생략.

---
[반복/강조 통계]
{signals}

---
[구간별 노트]
{notes}
"""


@dataclass
class LLMClient:
    settings: Settings

    def chat(self, prompt: str, *, max_tokens: int = 3000, temperature: float = 0.2) -> str:
        if not self.settings.nvidia_api_key:
            raise LecsumError(
                "NVIDIA_API_KEY 가 없습니다.\n"
                "https://build.nvidia.com 에서 키를 받아 .env 에 NVIDIA_API_KEY=nvapi-... 로 넣으세요."
            )
        payload = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{self.settings.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.nvidia_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise LecsumError(f"LLM API 오류 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LecsumError(f"LLM API 에 연결하지 못했습니다: {exc.reason}") from exc

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise LecsumError(f"LLM 응답 형식을 이해하지 못했습니다: {str(data)[:300]}") from exc


def chunk_transcript(transcript: Transcript, *, max_chars: int = 6000) -> list[tuple[float, str]]:
    """녹취록을 (시작초, 시각표기가 붙은 텍스트) 로 쪼갠다."""
    chunks: list[tuple[float, str]] = []
    buffer: list[str] = []
    start = transcript.segments[0].start if transcript.segments else 0.0
    size = 0

    for seg in transcript.segments:
        line = f"[{format_timestamp(seg.start)}] {seg.text.strip()}"
        if size + len(line) > max_chars and buffer:
            chunks.append((start, "\n".join(buffer)))
            buffer, size, start = [], 0, seg.start
        buffer.append(line)
        size += len(line) + 1

    if buffer:
        chunks.append((start, "\n".join(buffer)))
    return chunks


def summarize(transcript: Transcript, settings: Settings, *, title: str) -> tuple[str, ExamSignals]:
    client = LLMClient(settings)
    signals = collect_signals(transcript)

    chunks = chunk_transcript(transcript)
    log(f"{len(chunks)}개 구간으로 나눠 요약합니다.")

    notes: list[str] = []
    for index, (start, body) in enumerate(chunks, start=1):
        log(f"  구간 {index}/{len(chunks)} ({format_timestamp(start)}~) 요약 중")
        note = client.chat(
            MAP_PROMPT.format(start=format_timestamp(start), chunk=body),
            max_tokens=1500,
        )
        notes.append(f"### 구간 {index} ({format_timestamp(start)}~)\n{note}")

    if len(notes) == 1:
        joined = notes[0]
    else:
        joined = "\n\n".join(notes)

    log("최종 노트 작성 중")
    final = client.chat(
        REDUCE_PROMPT.format(title=title, signals=signals.render(), notes=joined),
        max_tokens=4000,
    )
    return final, signals
