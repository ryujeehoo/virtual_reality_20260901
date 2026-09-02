"""녹취록에서 '시험에 나올 것 같은 신호'를 기계적으로 뽑는다.

LLM 한테 "중요한 걸 골라줘" 라고만 하면 그럴듯한 걸 지어낸다.
그래서 먼저 코드로 근거를 만든다.

  1. 반복 용어 — 교수님이 계속 입에 올린 단어. 등장 횟수와 시각.
  2. 강조 표현 — "시험에 나온다", "중요합니다", "꼭 기억하세요" 같은 문장과 그 위치.
  3. 되짚기 — "다시 말하면", "아까 말했듯이" 처럼 같은 내용을 되풀이하는 지점.

이 결과를 요약 프롬프트에 근거로 같이 넘긴다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .transcribe import Transcript
from .utils import format_timestamp

# 시험 출제 신호가 가장 강한 표현부터.
CUE_PATTERNS: list[tuple[str, str]] = [
    ("시험", r"시험(에|에서)?\s*(꼭\s*)?(나올|나옵니|나온|낼|출제|출제됩)"),
    ("시험", r"(중간|기말)\s*고사"),
    ("강조", r"(이건|이거|여기는|이것만은)\s*(꼭|반드시|정말)"),
    ("강조", r"(중요|핵심)(합니다|해요|하다|한\s*부분|한\s*개념|하니까)"),
    ("암기", r"(외우|암기|기억)(세요|하세요|해야|해 두|해두|하시)"),
    ("정의", r"(정의|개념)(는|은|를|가)\s*"),
    ("되짚기", r"(다시\s*말|다시\s*한\s*번|아까\s*말|앞에서\s*말|정리하자면|요약하자면)"),
]

# 강의 녹취에 흔한, 뜻 없는 말들.
STOPWORDS = {
    "그래서", "그러면", "그리고", "그러니까", "이제", "우리", "여러분", "지금", "여기", "저기",
    "이것", "그것", "저것", "이거", "그거", "저거", "때문", "경우", "정도", "부분", "생각",
    "하나", "이렇게", "그렇게", "어떤", "무슨", "조금", "약간", "다음", "이번", "사실",
    "얘기", "이야기", "말씀", "설명", "내용", "문제", "방법", "사람", "시간", "오늘",
    "수업", "강의", "선생", "교수", "학생", "자료", "화면", "페이지", "슬라이드",
    "네요", "습니다", "합니다", "입니다", "있습니다", "없습니다", "됩니다", "겠습니다",
    "the", "and", "for", "that", "this", "with", "you", "are", "not", "but", "can",
}

TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9+#._-]{2,}")
# 한국어 조사 — 같은 단어가 다른 조사로 갈라지는 걸 막는다.
PARTICLE_RE = re.compile(
    r"(은|는|이|가|을|를|의|에|에서|에게|으로|로|와|과|도|만|까지|부터|보다|처럼|라는|이라는|라고|이라고|입니다|이다|한|하는|해서|하고)$"
)


def _normalize(token: str) -> str:
    if re.fullmatch(r"[가-힣]+", token):
        stripped = PARTICLE_RE.sub("", token)
        if len(stripped) >= 2:
            return stripped
        return token
    return token.lower()


@dataclass
class TermHit:
    term: str
    count: int
    timestamps: list[float] = field(default_factory=list)

    def render(self) -> str:
        shown = [format_timestamp(t) for t in self.timestamps[:6]]
        more = "" if len(self.timestamps) <= 6 else f" 외 {len(self.timestamps) - 6}회"
        return f"- {self.term}: {self.count}회 — [{'] ['.join(shown)}]{more}"


@dataclass
class CueHit:
    kind: str
    at: float
    sentence: str

    def render(self) -> str:
        return f"- ({self.kind}) [{format_timestamp(self.at)}] {self.sentence}"


@dataclass
class ExamSignals:
    terms: list[TermHit]
    cues: list[CueHit]
    duration: float

    def render(self) -> str:
        lines = [f"강의 길이: 약 {format_timestamp(self.duration)}", "", "■ 반복해서 등장한 용어 (많이 말할수록 중요)"]
        lines += [t.render() for t in self.terms] or ["- (없음)"]
        lines += ["", "■ 교수님이 명시적으로 강조한 지점"]
        lines += [c.render() for c in self.cues] or ["- (없음)"]
        return "\n".join(lines)


def collect_signals(
    transcript: Transcript,
    *,
    top_terms: int = 25,
    min_count: int = 3,
    max_cues: int = 40,
) -> ExamSignals:
    counter: Counter[str] = Counter()
    positions: defaultdict[str, list[float]] = defaultdict(list)
    cues: list[CueHit] = []

    for seg in transcript.segments:
        text = seg.text.strip()
        for raw in TOKEN_RE.findall(text):
            term = _normalize(raw)
            if len(term) < 2 or term in STOPWORDS:
                continue
            counter[term] += 1
            positions[term].append(seg.start)

        for kind, pattern in CUE_PATTERNS:
            if re.search(pattern, text):
                cues.append(CueHit(kind=kind, at=seg.start, sentence=text[:120]))
                break  # 한 문장에서 신호는 하나면 충분하다.

    terms = [
        TermHit(term=term, count=count, timestamps=positions[term])
        for term, count in counter.most_common(top_terms * 3)
        if count >= min_count
    ][:top_terms]

    # 강조 신호가 너무 많으면 시험/암기 쪽을 우선해 남긴다.
    if len(cues) > max_cues:
        priority = {"시험": 0, "암기": 1, "강조": 2, "정의": 3, "되짚기": 4}
        cues = sorted(cues, key=lambda c: (priority.get(c.kind, 9), c.at))[:max_cues]
        cues.sort(key=lambda c: c.at)

    duration = transcript.segments[-1].end if transcript.segments else 0.0
    return ExamSignals(terms=terms, cues=cues, duration=duration)
