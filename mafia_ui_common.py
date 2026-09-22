# -*- coding: utf-8 -*-
"""mafia_ui_common.py — mafia_ui 계열 믹스인이 함께 쓰는 import·상수·헬퍼.
(원래 mafia_ui.py 상단 그대로. 각 믹스인 모듈이 `from mafia_ui_common import *`로 가져다 쓴다.)

원 설명: mafia_ui.py — MAFIA의 게임방 UI 믹스인.

App(DialogsMixin, ChatRendererMixin, ChatSearchMixin, DndMixin, MafiaUIMixin).
게임방은 가상 키 ("mgame",)로 식별된다. engine의 실제 1:1·그룹 채팅 로직은
전혀 건드리지 않고, 게임룸 안에서만 발언·투표·밤낮을 가로채 처리한다.
"""
import applog
import os
import re
import random as random_mod
import threading
import time
import tkinter as tk

from constants import (C_MAIN, C_CARD, C_TEXT, C_MUTE, C_BORDER, C_ME, C_SIDEBAR,
                       FONT_FAM, FONT_XS_PAD, FONT_MSG, FONT_HEAD, FONT_NAME,
                       FONT_SM, PAD_TOP, PEER_TIMEOUT)
from mafia_core import GameCore, Phase
import random as _rndm
import mafia_config
from mafia_config import (ALL_PERSONAS, AI_PERSONAS, GAME_ROOM_NAME, MIN_PLAYERS,
                          MAX_PLAYERS,
                          DAY_CYCLE_SECONDS, NIGHT_SOLVE_SECONDS,
                          VOTE_REVEAL_DELAY, VOTE_WINDOW, AI_VOTE_LLM_FALLBACK_MS,
                          DEFENSE_VOTE_WINDOW, NIGHT_ACTION_WINDOW,
                          AI_REACT_MAX_REPLIES, AI_REACT_SKIP_PROB,
                          AI_PILE_ON_LIMIT_RATIO, AI_PILE_ON_REDIRECT_PROB)
from mafia_ai import AIDirector, host_llm_cached, clean_llm_dialect, sanitize_player_names, split_chat_tags
from netutils import resource_dir
import emoji_render

# 마피아 전용 색상
M_HOST = "#7c3aed"        # 사회자 말풍선(바이올렛)
M_DAY = "#f59e0b"
M_NIGHT = "#6366f1"
M_AI_COLORS = {p["name"]: p["color"] for p in ALL_PERSONAS}
# v1.57 — 후보 버튼류에서 반복되던 하드코딩 회색톤을 이름 붙여 한 곳에서 관리
# (디자인 통일성 지적 — constants.py의 C_ROWSEL/C_SEARCHBG와는 의도적으로 다른
# 톤이라 재사용 대신 마피아 전용 상수로 명명).
M_BTN_BG = "#2a2f3a"      # 후보 선택 버튼 기본 배경(비활성 상태)
M_INPUT_BG = "#1a1d24"    # 입력창/스테퍼 등 어두운 배경
M_TEXT_LIGHT = "#e5e7eb"  # 밝은 본문 텍스트

# v1.57 — (FONT_FAM, 숫자, ...) 튜플을 직접 여러 곳에 반복 타이핑하던 것 중
# 가장 자주 쓰이던 4가지 크기에 이름을 붙임(디자인 통일성 지적 — 규칙 없이
# 8~32pt가 파편화돼 있었음). 나머지(28/32pt 아이콘, 12/14/16/18pt 제목류)는
# 각자 한두 곳뿐인 1회성 강조라 그대로 둠.
M_FONT_HELP = (FONT_FAM, 9)             # 보조 안내문(팝업 소제목, 힌트 등)
M_FONT_BODY = (FONT_FAM, 10)            # 버튼/본문 기본 크기
M_FONT_BODY_B = (FONT_FAM, 10, "bold")  # 버튼/소제목 강조
M_FONT_EMPH_B = (FONT_FAM, 11, "bold")  # 확인 버튼 등 조금 더 큰 강조

# v1.57 — 역할 아이콘/한글명이 역할 팝업(META)과 상단 바 버튼 두 곳에 각각
# 따로 하드코딩돼 있어서(디자인 통일성 지적), 하나를 바꾸면 다른 쪽을 잊기 쉬웠다.
# 단일 출처로 통일.
ROLE_ICON = {"mafia": "🔪", "doctor": "💉", "police": "🕵", "citizen": "🧑‍🌾"}
ROLE_LABEL_KR = {"mafia": "마피아", "doctor": "의사", "police": "경찰", "citizen": "시민"}


def _role_icon_label(role):
    """'마피아 🔪' 같은 '한글명 + 아이콘' 조합 — 상단 바 '내 직업' 버튼 등에서 사용."""
    return f"{ROLE_LABEL_KR.get(role, role)} {ROLE_ICON.get(role, '')}".strip()


def _role_kr(role):
    """직업 공개 멘트용 — core.reveal_role()이 돌려주는 내부 영문 키("citizen" 등)를
    화면에 그대로 노출하지 말고 한글명으로 바꾼다(실측 지적: "citizen이었습니다"로
    영문이 그대로 나갔음)."""
    return ROLE_LABEL_KR.get(role, role) if role else "미확인"


def _role_was(role):
    """직업 공개 멘트 — 받침 유무에 맞춰 '마피아였습니다' / '시민이었습니다'."""
    label = _role_kr(role)
    last = label[-1]
    has_batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return f"{label}{'이었' if has_batchim else '였'}습니다"


# ---- 팝업 글자 크기 통일 ----
# 알약 버튼(emoji_render)은 PIL로 글자를 그려 font_size가 '픽셀'인데, 같은 팝업의 Tk 라벨/제목은 '포인트'
# (emoji_render.apply가 pt→px로 환산)라서, 예전에는 버튼 글자(9~10px)가 라벨(12px)·제목(16px)보다
# 유독 작고 얇아 보였다(경찰 조사 팝업 등). 팝업의 버튼 글자는 아래 상수로 포인트 기준에 맞춘다.
def pt_px(pt):
    """포인트 → 픽셀(96dpi). emoji_render.apply와 같은 환산."""
    return round(pt * 96 / 72)


POPUP_BTN_PX = pt_px(10)         # 후보 선택·찬반 같은 주 버튼 (M_FONT_BODY 10pt와 같은 크기)
POPUP_SMALL_BTN_PX = pt_px(9)    # 기권·닫기·채팅 보내기 같은 보조 버튼 (M_FONT_HELP 9pt와 같은 크기)


__all__ = [
    "applog",
    "os",
    "re",
    "random_mod",
    "threading",
    "time",
    "tk",
    "C_MAIN",
    "C_CARD",
    "C_TEXT",
    "C_MUTE",
    "C_BORDER",
    "C_ME",
    "C_SIDEBAR",
    "FONT_FAM",
    "FONT_XS_PAD",
    "FONT_MSG",
    "FONT_HEAD",
    "FONT_NAME",
    "FONT_SM",
    "PAD_TOP",
    "PEER_TIMEOUT",
    "GameCore",
    "Phase",
    "_rndm",
    "mafia_config",
    "ALL_PERSONAS",
    "AI_PERSONAS",
    "GAME_ROOM_NAME",
    "MIN_PLAYERS",
    "MAX_PLAYERS",
    "DAY_CYCLE_SECONDS",
    "NIGHT_SOLVE_SECONDS",
    "VOTE_REVEAL_DELAY",
    "VOTE_WINDOW",
    "AI_VOTE_LLM_FALLBACK_MS",
    "DEFENSE_VOTE_WINDOW",
    "NIGHT_ACTION_WINDOW",
    "AI_REACT_MAX_REPLIES",
    "AI_REACT_SKIP_PROB",
    "AI_PILE_ON_LIMIT_RATIO",
    "AI_PILE_ON_REDIRECT_PROB",
    "AIDirector",
    "host_llm_cached",
    "clean_llm_dialect",
    "sanitize_player_names",
    "split_chat_tags",
    "resource_dir",
    "emoji_render",
    "M_HOST",
    "M_DAY",
    "M_NIGHT",
    "M_AI_COLORS",
    "M_BTN_BG",
    "M_INPUT_BG",
    "M_TEXT_LIGHT",
    "M_FONT_HELP",
    "M_FONT_BODY",
    "M_FONT_BODY_B",
    "M_FONT_EMPH_B",
    "ROLE_ICON",
    "ROLE_LABEL_KR",
    "_role_icon_label",
    "_role_kr",
    "_role_was",
    "pt_px",
    "POPUP_BTN_PX",
    "POPUP_SMALL_BTN_PX",
]
