# -*- coding: utf-8 -*-
"""colors_compat.py — mafia_config.py가 constants.py의 색상 체계를 쓰도록 하는 간접 계층.

LAN Talk의 constants.py를 그대로 재사용하되, 랜톡 자체를 고치지 않고 마피아 전용
추가 색상만 정의한다."""
from constants import *  # noqa: F401,F403

# 마피아 전용 액센트
C_MAFIA_ACCENT = "#b91c1c"        # 마피아(레드) 팀 액센트
C_MAFIA_NIGHT = "#1e1b4b"        # 밤 배경 톤(참고용)
C_HOST_BUBBLE = "#7c3aed"        # 사회자 말풍선 (바이올렛)
C_AI_BUBBLE = "#0d9488"          # AI 플레이어 말풍선 액센트
C_DAY_BTN = "#f59e0b"            # 낮 버튼
C_NIGHT_BTN = "#6366f1"          # 밤 버튼
