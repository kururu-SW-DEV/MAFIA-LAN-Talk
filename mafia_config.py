# -*- coding: utf-8 -*-
"""mafia_config.py — MAFIA 전역 설정.

- LLM: 사내 vLLM 서버(OpenAI 호환 /v1/chat/completions). 폐쇄망 배포 원칙에 맞춰
  엔드포인트·모델명·키는 모두 이 파일 또는 환경변수로만 주입한다(코드 하드코딩 금지).
  API 키는 환경변수 HERMES_CUSTOM_CUSTOM_API_KEY에서 자동 읽는다(폐쇄망 허용).
- Hermes 서브에이전트(AI 플레이어): `hermes chat -Q --yolo ...` 원큐 실행,
  `--resume <sid>`로 같은 세션을 재사용해 AI가 이전 대화·자기 역할 배정을 기억한다.
"""
import os
import json

# ============================================================
# LLM (사회자 연설·변론 요약·죽음 인사말 등 자유 텍스트 생성)
# ============================================================
LLM_BASE_URL = os.environ.get("MAFIA_LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("MAFIA_LLM_MODEL", "Qwen/Qwen3.8-27B")
LLM_API_KEY_ENV = "HERMES_CUSTOM_CUSTOM_API_KEY"

# ============================================================
# AI 플레이어 (Hermes 서브에이전트)
# ============================================================
HERMES_BIN = os.environ.get("MAFIA_HERMES_BIN", "hermes")
HERMES_MODEL = os.environ.get("MAFIA_HERMES_MODEL", "Qwen/Qwen3.8-27B")
HERMES_PROVIDER = os.environ.get("MAFIA_HERMES_PROVIDER", "custom")
HERMES_MAX_TURNS = 1          # 한 번에 한 턴씩 — 서브에이전트가 도구를 함부로 안 돌림
HERMES_REASONING = "low"      # Qwen3.8-27B가 받는 최저 단계 (minimal은 400 에러 실측)
HERMES_CMD_TIMEOUT = 90       # 서브에이전트 1회 호출 타임아웃(초). 실측 20~30초 → 여유 3배.
HERMES_REPLY_LANG = \
    "대사는 반드시 한국어로 짧고 자연스럽게(1~2문장). 마크다운·이모지·코드블록 없이 순수 대사만."

# ── 페르소나(이름/성격/말버릇). 게임 시작 시 이 순서대로 AI 자리에 배정 ──
AI_PERSONAS = [
    {"name": "루카", "persona": "차분하고 논리적인 분석가형. 확신 없는 추리는 하지 않고, "
                              "논리 구멍을 지적하는 화법을 쓴다.",
     "color": "#38bdf8"},
    {"name": "미나", "persona": "밝고 수다스러운 분위기 메이커형. 추리보다 분위기를 살리는 "
                              "발언을 많이 하고, 의심받으면 억울해한다.",
     "color": "#f472b6"},
    {"name": "제이", "persona": "느긋한 관찰자형. 문장을 짧게 끊어 말하고, 은근히 특정인을 "
                              "건드리며 반응을 본다.",
     "color": "#a3e635"},
    {"name": "레오", "persona": "열정적인 정통파 추리형. 마피아 게임 경험이 많고, 대담하게 "
                              "표를 몰아가는 리더십을 낸다.",
     "color": "#fb923c"},
    {"name": "소피", "persona": "신중하고 소심한 안전추구형. 확실한 근거가 없으면 투표를 "
                              "망설이고, 의심받으면 화를 내지 않고 논리로 반박한다.",
     "color": "#22d3ee"},
]
# ============================================================
# 인격 풀 20종 확장 (2026-09-17 문서 기획 보강 — 문서 #1~#20 병합)
# 기존 AI_PERSONAS 유지 + 20종을 합쳐 '인격 풀'로 무작위 배정.
# talk/att/aggr/freq 파라미터는 문서 2절 "행동 파라미터" 그대로:
#   freq = 발언 참여 확률(%)  att = 공격성(%)
# ============================================================
PERSONAS20 = [
 {"name": "반장",   "talk": "공격적인 청유형 도입부 ~하자/~해보자. 주도하고 요약하고 다급하다",
  "freq": 80, "att": 50, "color": "#f59e0b",
  "persona": "활발한 리더형. 대화를 주도하고 투표 직전 '정리해보자면'으로 요약. 확신에 찬 어조"},
 {"name": "그림자", "talk": "극단 단문, 마침표 없이 툭. '음.. 아직 모르겠음'",
  "freq": 20, "att": 20, "color": "#64748b",
  "persona": "조용한 관찰자형. 평소 1줄 이하, 명백한 모순 캐치 시 한 번만 길게"},
 {"name": "탐정놀이", "talk": "근거 요구 + 번호 매기기 '1) 2)'. '그건 논리적으로 이상한데'",
  "freq": 70, "att": 65, "color": "#22d3ee",
  "persona": "논리충 분석러. 과거 발언과 지금 말 대조해서 모순 지적. 감정 호소에 안 흔들"},
 {"name": "울보",    "talk": "감탄사+물결표 '헐','진짜?','미쳤다ㅠㅠ'. 지목당하면 발끈",
  "freq": 60, "att": 60, "color": "#f9a8d4",
  "persona": "감정적 반응러. 느낌으로 투표, 억울한 지목에 방어적"},
 {"name": "밈장인", "talk": "ㅋㅋㅋ+드립. 진지한 순간에도 장난 한 스푼",
  "freq": 50, "att": 30, "color": "#fbbf24",
  "persona": "능글맞은 장난꾼. 긴장을 풀고 장난으로 화제를 돌려 의심 회피"},
 {"name": "의심병",  "talk": "되묻기 '~한거 수상해', '왜 굳이 그런 말을 해?'",
  "freq": 70, "att": 75, "color": "#ef4444",
  "persona": "의심 많은 편집증형. 사소한 패턴까지 근거로 두고 여러 사람 번갈아 의심"},
 {"name": "순둥이", "talk": "선의 해석 '그럴 리가 없는데','착해보이는데'",
  "freq": 45, "att": 10, "color": "#a7f3d0",
  "persona": "순진한 믿음형. 의심보다 변호 우선, 증거가 나와도 한 박자 늦게 인정"},
 {"name": "일단대기", "talk": "유보 '일단 지켜볼게','아직 판단 안 남'",
  "freq": 30, "att": 20, "color": "#94a3b8",
  "persona": "느긋한 관망형. 결정 미루고 다수 의견 형성 후 숟가락 얹기"},
 {"name": "돌직구", "talk": "단정 '너 마피아지','딱 봐도 너임'",
  "freq": 85, "att": 90, "color": "#dc2626",
  "persona": "공격적 몰아가기형. 빠르게 지목해 여론몰이, 반박당하면 더 세게 반발"},
 {"name": "매너킹", "talk": "존댓말 유지 '~인것 같아요','혹시 ~일까요?' 완곡",
  "freq": 50, "att": 30, "color": "#e5e7eb",
  "persona": "존댓말 예의러. 지목도 돌려말하고 정중한 어조 유지"},
 {"name": "절친",    "talk": "반말 캐주얼 '야','~냐','~임' 친한 척하며 정보 캐내기",
  "freq": 70, "att": 40, "color": "#4ade80",
  "persona": "반말 친근러. 편하게 대화하며 질문으로 정보 캐냄"},
 {"name": "급성격",  "talk": "초단문 연타 + 오타 '그렇케'. 투표 열리면 즉시 칼투표",
  "freq": 80, "att": 60, "color": "#fca5a5",
  "persona": "오타쟁이 급한 성격. 생각나는 대로 바로 타이핑, 결론도 빠름"},
 {"name": "눈치백단", "talk": "되묻기 '다들 어떻게 생각해?' 단정 회피",
  "freq": 45, "att": 20, "color": "#c084fc",
  "persona": "신중한 정치가형. 입장 먼저 안 밝히고 여론 형성 후 합류, 생존 중시"},
 {"name": "메모광",  "talk": "정리/타임라인 '1턴엔 ~했고 2턴엔 ~'. 모순 적발 특화",
  "freq": 55, "att": 60, "color": "#93c5fd",
  "persona": "기억력 좋은 서기형. 히스토리 순서대로 요약하며 입장 변화를 근거로 지적"},
 {"name": "토닥토닥", "talk": "위로/공감 '괜찮아?','속상해하지 마'. 갈등 완화",
  "freq": 50, "att": 10, "color": "#fda4af",
  "persona": "감성적 위로형. 억울하게 몰린 사람 위로, 전투적 분위기 중재"},
 {"name": "확률맨",  "talk": "수치 언급 '확률상','N명 중 M명이면 ~'. 계산 근거",
  "freq": 60, "att": 50, "color": "#60a5fa",
  "persona": "데이터/확률 집착형. 인원 비율 계산으로 행동 제안"},
 {"name": "새침이",  "talk": "퉁명 '딱히 신경쓰는건 아니고.. 물어본거야. 착각하지 마'",
  "freq": 45, "att": 40, "color": "#818cf8",
  "persona": "츤데레형. 겉으론 무심, 실제론 세심하게 관찰"},
 {"name": "근자감", "talk": "확신 '내가 원래 이런거 잘 맞춤','이번에도 내 말이 맞을걸?'",
  "freq": 75, "att": 70, "color": "#fb923c",
  "persona": "허세/자신감 과잉형. 과거 전적 과장, 틀려도 인정 안함"},
 {"name": "팝콘",   "talk": "실황 중계 '와 이 판 미쳤다ㅋㅋ' 관전 코멘트",
  "freq": 50, "att": 25, "color": "#fcd34d",
  "persona": "유머러스 관전러. 상황 재밌게 논평, 가끔 날카로운 지적"},
 {"name": "차분님", "talk": "중재 '자 잠깐 진정하고 정리해보자'. 팩트 기반",
  "freq": 65, "att": 20, "color": "#34d399",
  "persona": "침착한 카운슬러형. 갈등 격화 시 개입해 논점 정리"},
]

# 인격 풀 = 기존 6인 + 문서 20종 (병합, 문서 지시대로 기존은 유지)
ALL_PERSONAS = AI_PERSONAS + [
    {"name": x["name"], "persona": x["persona"], "color": x["color"]}
    for x in PERSONAS20
]
# ============================================================
# 게임 룰 상수
# ============================================================
MIN_PLAYERS = 4               # 인간 + AI 합산 최소 인원
MIN_PLAYERS_CORE = 4          # GameCore.start_game 하드 리미트(같은 값)
MAX_PLAYERS = 10
DAY_CYCLE_SECONDS = 150       # 낮(토론+투표) 기본 길이. 투표 완료 시 즉시 종료.
                              # v1.56 — 역할 공개 연출·최후변론(60초)·찬반(30초)까지
                              # 겹치면 120초는 토론 체감이 빠듯하다는 지적으로 상향.
NIGHT_SOLVE_SECONDS = 30      # 밤 제출 대기 시간.
VOTE_REVEAL_DELAY = 3.0       # 개표 전 대기(초) — 긴장감
LLM_TTL_CACHE = 12 * 3600     # LLM 프롬프트 결과 캐시 TTL
PLAYER_CONTEXT_TURNS = 12     # AI 플레이어가 기억할 최근 대화 개수

# 역할 배분 (시작 인원 = total)
# 3~6명: 마피아1   7명: 마피아2   8~10명: 마피아2+경찰   4~10명: 의사 포함
ROLE_TABLE = {
    # v1.56 — 3명 항목 제거: MIN_PLAYERS_CORE=4라 실제로는 절대 쓰이지 않는
    # 죽은 설정이었다(최소 인원 미만은 GameCore.start_game이 먼저 막음).
    4:  {"mafia": 1, "doctor": 1, "police": 0},
    5:  {"mafia": 1, "doctor": 1, "police": 0},
    6:  {"mafia": 1, "doctor": 1, "police": 0},
    7:  {"mafia": 2, "doctor": 1, "police": 0},
    8:  {"mafia": 2, "doctor": 1, "police": 1},
    9:  {"mafia": 2, "doctor": 1, "police": 1},
    10: {"mafia": 2, "doctor": 1, "police": 1},
}

# ============================================================
# 표준 투표 상태머신 동률 재투표+최후변론+찬반 투표, 문서 기획
VOTE_FULL_MACHINE = True
# 밤 행동 Grace Period 1.5초 초과 수신 인정
NIGHT_GRACE_SECONDS = 1.5

# 채팅방 식별자
# ============================================================
GAME_ROOM_NAME = "마피아 게임"

# ============================================================
# 스타일 (LAN Talk 테이마상 상수 재사용)
# ============================================================
import colors_compat  # noqa: F401  # constants.py 간접 참조용(위임)


# ── 런타임 오버라이드(설정창에서 저장) ──
RUNTIME_OVERRIDES = {}      # {"base_url":..., "model":..., "api_key":...} — mafia_ui가 채움

_OVERRIDES_FILE = None      # datadir 지정 시 mafia_llm.json 경로 (mafia_ui가 세팅)

def _portable_datadir():
    """exe(또는 소스) 옆의 data 폴더 — 모든 설정/로그의 기본 위치."""
    from netutils import default_datadir
    return default_datadir()

def set_overrides_file(datadir):
    """설정파일 경로 지정 — <datadir>/mafia_llm.json (재시작 후에도 유지).
    datadir가 없으면 프로그램 옆 data 폴더를 쓴다(%LOCALAPPDATA% 등 다른 폴더는 쓰지 않음)."""
    global _OVERRIDES_FILE
    try:
        d = datadir or _portable_datadir()
        os.makedirs(d, exist_ok=True)
        _OVERRIDES_FILE = os.path.join(d, "mafia_llm.json")
    except Exception:
        _OVERRIDES_FILE = None

def _ensure_overrides_file():
    """설정파일 경로 보장 — 미지정이면 프로그램 옆 data 폴더."""
    if not _OVERRIDES_FILE:
        set_overrides_file(None)
    return _OVERRIDES_FILE


def load_overrides():
    """저장된 설정 파일 읽기 — RUNTIME_OVERRIDES에 병합(base_url/model/api_key)."""
    if RUNTIME_OVERRIDES:
        return RUNTIME_OVERRIDES         # 이미 세션 중 덮어씀 우선
    _ensure_overrides_file()
    if not _OVERRIDES_FILE or not os.path.exists(_OVERRIDES_FILE):
        return RUNTIME_OVERRIDES
    try:
        with open(_OVERRIDES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        for k in ("base_url", "model", "api_key"):
            if d.get(k):
                RUNTIME_OVERRIDES[k] = d[k]
    except Exception:
        pass
    return RUNTIME_OVERRIDES

def save_overrides():
    """현재 RUNTIME_OVERRIDES를 파일로 저장 — api_key 등 사용자 입력 유지."""
    _ensure_overrides_file()
    if not _OVERRIDES_FILE:
        return False
    try:
        os.makedirs(os.path.dirname(_OVERRIDES_FILE), exist_ok=True)
        with open(_OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(RUNTIME_OVERRIDES, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False

def get_llm_api_key():
    # 1) 환경변수(MAFIA_LLM_API_KEY > HERMES 브릿지키) 먼저, 2) 세션,runtime, 3) 설정파일, 4) 빈값
    import os as _os
    k = _os.environ.get("MAFIA_LLM_API_KEY") or _os.environ.get(LLM_API_KEY_ENV, "")
    if k:
        return k
    load_overrides()
    return RUNTIME_OVERRIDES.get("api_key", "")

VOTE_WINDOW = 15              # 투표 팝업 카운트다운(초)
# ── AI가 사람 한 명에게 몰려 "다구리" 치는 느낌을 줄이는 튜닝 값 ──
AI_REACT_MAX_REPLIES = 1      # 사람 발언 하나에 직접 대답하는 AI 수 상한(예전엔 전원이 확률적으로 답함)
AI_REACT_SKIP_PROB = 0.15     # 아예 대답 없이 넘어가는 확률(사람 말에 매번 반응하지 않게)
AI_PILE_ON_LIMIT_RATIO = 0.5  # AI 표가 한 명에게 이 비율 이상 쌓이면(최소 2표)
AI_PILE_ON_REDIRECT_PROB = 0.6  # 뒤에 던지는 AI는 이 확률로 다른 후보에게 돌린다
DEFENSE_VOTE_WINDOW = 15      # 최후 변론 후 찬반(처형여부) 투표 제한시간(초) —
                              # 시간 안에 안 누르면 기권=반대(부결 쪽) 취급.
NIGHT_ACTION_WINDOW = 15      # 마피아 살해/의사 치료/경찰 조사 대상 선택 팝업
                              # 제한시간(초) — 시간 안에 안 고르면 이번 밤은
                              # 아무 행동도 하지 않은 걸로 처리.
