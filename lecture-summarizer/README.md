# lecsum — 강의 영상 → 텍스트 → 시험 대비 노트

한성대 LMS(learn.hansung.ac.kr, 코스모스/무들 기반)처럼 웹으로 듣는 강의 영상을 **주소만 넣으면**
내려받아서 → 음성인식으로 텍스트를 만들고 → 시험에 나올 만한 것 위주로 정리해 줍니다.

블렌디드 수업이라 절반이 사이버 강의인 상황을 염두에 두고 만들었습니다.
한 시간짜리 영상을 다시 돌려보는 대신, 요약본을 읽고 필요한 부분만 타임스탬프로 찾아가면 됩니다.

```
영상 주소 ──ffmpeg/yt-dlp──▶ mp4 ──ffmpeg──▶ 16kHz wav
   ──whisper──▶ transcript.txt / .srt ──NVIDIA LLM──▶ summary.md
```

## 결과물

`out/<제목>/` 안에 이렇게 생깁니다.

| 파일 | 내용 |
|---|---|
| `transcript.txt` | `[12:34] 문장` 형식의 전체 녹취록 |
| `transcript.srt` | 자막 파일. 영상에 얹어서 볼 수 있음 |
| `transcript.json` | 요약만 다시 돌릴 때 쓰는 원본 |
| `signals.md` | 반복 용어·강조 표현 통계 (아래 설명) |
| `summary.md` | **최종 학습 노트** |

`summary.md` 구성:

1. 한 줄 요약
2. 오늘의 흐름 (타임스탬프 목차)
3. 핵심 개념 정리 — *무엇인가 / 왜 필요한가 / 예시*
4. ⭐ **시험에 나올 가능성이 높은 것**
5. 용어 사전
6. 스스로 점검하는 질문 5개 (답 포함)
7. 더 공부해야 할 것

### ⭐ 표시는 감이 아니라 근거로 붙습니다

"중요한 걸 골라줘" 라고만 하면 AI 는 그럴듯한 걸 지어냅니다.
그래서 요약 전에 **코드로 먼저 근거를 뽑습니다** (`lecsum/transcript_signals.py`).

- **반복 용어** — 교수님이 몇 번 말했는지 세고, 등장 시각을 모읍니다.
  한국어 조사(`은/는/이/가/을/를...`)를 떼어 내서 "파이프라인은", "파이프라인을" 을 같은 단어로 셉니다.
- **강조 표현** — `시험에 나옵니다`, `중요합니다`, `외우세요`, `다시 말하면` 같은
  표현을 정규식으로 찾아 위치와 함께 기록합니다.

이 통계(`signals.md`)를 요약 프롬프트에 **근거로 같이 넣기 때문에**,
⭐ 항목은 "몇 번 나왔고 몇 분에 나왔는지"가 붙어 나옵니다.

## 설치

### 1. ffmpeg (필수)

```bash
# Windows
winget install Gyan.FFmpeg
# macOS
brew install ffmpeg
# Ubuntu
sudo apt install ffmpeg
```

`ffmpeg -version` 이 나오면 성공입니다.

### 2. 이 프로그램

```bash
git clone https://github.com/ryujeehoo/lecture-summarizer.git
cd lecture-summarizer

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -e ".[all]"        # faster-whisper + yt-dlp 까지
```

### 3. NVIDIA API 키

1. <https://build.nvidia.com> 접속 → 로그인
2. 모델 아무거나 클릭 (예: `llama-3.3-70b-instruct`)
3. 오른쪽 **Get API Key** → `nvapi-...` 복사

```bash
cp .env.example .env
# .env 를 열어 NVIDIA_API_KEY=nvapi-... 채우기
```

> `.env` 는 `.gitignore` 에 들어 있습니다. **키를 커밋하지 마세요.**

## 쓰는 법

### 이미 받아 둔 영상이 있을 때 (가장 간단)

```bash
lecsum ./3주차강의.mp4 --title "가상현실 3주차"
```

### 강의 주소로 바로

```bash
lecsum "https://cdn.example.com/vod/abcd.m3u8" \
  --title "가상현실 3주차" \
  --referer "https://learn.hansung.ac.kr/" \
  --cookie "$(cat cookie.txt)"
```

### 자주 쓰는 옵션

```bash
lecsum ./강의.mp4 --skip-summary                 # 텍스트만 (API 키 없이도 됨)
lecsum out/가상현실-3주차/transcript.json \
       --from-transcript --title "가상현실 3주차"  # 요약만 다시 (음성인식 재실행 X)
lecsum ./강의.mp4 --whisper-model medium         # CPU 라서 느릴 때
lecsum ./강의.mp4 --device cuda                  # GPU 있을 때
```

## 강의 주소 찾기 (제일 중요)

한성대 LMS 강의 페이지 주소는 보통 이렇게 생겼습니다.

```
https://learn.hansung.ac.kr/mod/vod/view.php?id=1183874
```

이건 **영상 자체가 아니라 플레이어가 들어있는 페이지**입니다.
실제 영상은 그 안에서 따로 불러오고, 로그인한 사람만 볼 수 있게 되어 있습니다.
그래서 두 가지 방법이 있습니다.

### 방법 A — 페이지 주소 그대로 (먼저 이걸 시도)

브라우저에 로그인되어 있으면 그 세션을 빌려 씁니다. 한 줄이면 끝입니다.

```bash
lecsum "https://learn.hansung.ac.kr/mod/vod/view.php?id=1183874" \
  --cookies-from-browser chrome --title "가상현실 3주차"
```

`chrome` 자리에 실제 쓰는 브라우저를 넣으세요 (`edge`, `firefox`, `whale`, `safari`).
**되면 여기서 끝입니다.** 안 되면 (`403`, `Unsupported URL`, 빈 파일) 방법 B로 갑니다.

### 방법 B — Copy as cURL (확실한 방법, 1분)

플레이어가 실제로 받아 가는 주소를 그대로 가져옵니다.

1. LMS 에 로그인하고 강의 영상을 **재생**합니다.
2. `F12` → **Network(네트워크)** 탭 → 필터 칸에 `m3u8` 입력
   - 아무것도 안 뜨면 `mp4` 로, 그래도 없으면 `Media` 탭을 보세요.
   - 영상이 이미 재생 중이었다면 `F5` 로 새로고침하고 다시 재생하세요.
3. 목록에 뜬 요청을 **우클릭 → Copy → Copy as cURL**
4. 그대로 `curl.txt` 에 붙여넣고 저장

```bash
lecsum --curl-file curl.txt --title "가상현실 3주차"
```

URL·Referer·Cookie·User-Agent 를 **알아서 다 뽑아 씁니다.** 손으로 옮길 필요 없습니다.
(윈도우 cmd·파워셸에서 복사한 형식도 읽습니다.)

터미널에 바로 붙여넣고 싶으면:

```bash
lecsum --curl-file - --title "가상현실 3주차"    # 붙여넣고 Ctrl+D (윈도우는 Ctrl+Z, Enter)
```

수동으로 주고 싶다면 여전히 이렇게도 됩니다.

```bash
lecsum "붙여넣은_m3u8_주소" --title "과목명 N주차" \
  --referer "https://learn.hansung.ac.kr/" --cookie "$(cat cookie.txt)"
```

> 쿠키는 보통 몇 시간이면 만료됩니다. `403`/`401` 이 나면 3번부터 다시 하세요.
> 내려받은 영상은 **본인 수강 과목의 개인 학습용**으로만 쓰세요.
> 재배포·공유는 학칙과 저작권법 위반입니다. 기본 동작은 요약이 끝나면 영상 파일을 지웁니다
> (`--keep-video` 로 유지 가능).

## 다른 컴퓨터 / GPU 없이 쓰기 (Google Colab)

노트북이 느리거나 학교 컴퓨터에서 쓸 때는 Colab 이 가장 편합니다. GPU 가 공짜입니다.

`notebooks/lecsum_colab.ipynb` 를 Colab 에 올리고 위에서부터 실행하세요.
런타임 유형을 **T4 GPU** 로 바꾸면 한 시간짜리 강의가 5분 안에 텍스트로 나옵니다.

## 음성인식 백엔드

| 백엔드 | 언제 쓰나 | 설정 |
|---|---|---|
| `whisper` (기본) | 한국어 강의. 로컬에서 돌아 요금 없음 | `pip install faster-whisper` |
| `openai` | OpenAI 호환 `/audio/transcriptions` 를 제공하는 서비스 | `LECSUM_ASR_BASE_URL`, `LECSUM_ASR_API_KEY` |

NVIDIA API 키는 **요약(LLM)** 에 씁니다.
NVIDIA 가 호스팅하는 음성인식 모델(Parakeet/Canary)은 영어 중심이라,
한국어 강의는 `로컬 whisper + NVIDIA LLM 요약` 조합이 결과가 가장 좋습니다.

속도 참고 (1시간 강의 기준, 대략):

| 환경 | whisper `large-v3` | `medium` |
|---|---|---|
| CPU (노트북) | 2~4시간 | 40~70분 |
| GPU (T4, Colab) | 4~8분 | 2~4분 |

CPU 만 있다면 `--whisper-model medium` 으로 시작하세요. 한국어도 꽤 잘 알아듣습니다.

## 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `ffmpeg 을(를) 찾을 수 없습니다` | ffmpeg 미설치. 위 설치 항목 참고 |
| ffmpeg `403 Forbidden` | 쿠키 만료. Network 탭에서 다시 복사 |
| 다운로드가 중간에 멈춤 | m3u8 세그먼트가 끊긴 것. 같은 명령 다시 실행 |
| `NVIDIA_API_KEY 가 없습니다` | `.env` 에 키가 없음. `--skip-summary` 로 텍스트만 뽑는 건 가능 |
| `LLM API 오류 404` | `.env` 의 `LECSUM_LLM_MODEL` 이름이 카탈로그와 다름 |
| 인식 결과가 엉망 | 강의 오디오가 작을 때. `--whisper-model large-v3` 로 올려 보세요 |
| 같은 문장이 계속 반복됨 | whisper 의 알려진 증상. 이미 `condition_on_previous_text=False` 로 막아 둠 |

## 프로젝트 구조

```
lecsum/
  cli.py                 명령줄 + 전체 파이프라인
  download.py            ffmpeg / yt-dlp 로 영상 받기
  curlparse.py           "Copy as cURL" 에서 URL·쿠키 뽑기
  audio.py               16kHz 모노 추출, 조각 내기
  transcribe.py          음성인식 (whisper / OpenAI 호환)
  transcript_signals.py  반복 용어·강조 표현 추출  ← ⭐의 근거
  summarize.py           NVIDIA NIM 으로 map-reduce 요약
  config.py              .env 및 설정
  utils.py               공용 헬퍼
tests/test_signals.py    의존성 없이 도는 검증
```

테스트:

```bash
python tests/test_signals.py
```

## 참고한 글

- <https://m-nes.tistory.com/702>
- <https://kinfolust.tistory.com/4>
