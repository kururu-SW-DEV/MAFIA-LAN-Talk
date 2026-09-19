# -*- coding: utf-8 -*-
"""app_mafia_patch.py — app.py에 마피아 통합 patch를 안전하게 적용하는 재사용 스크립트.

< == 와 같은 유실 복구시: python app_mafia_patch.py  → app.py에 12건 patch를 다시 적용.
(python patch 없이 파일 직접 수정 안전화)
"""
import os
import re
import sys

P = r"D:\DEV\MAFIA\app.py"

def main():
    src = open(P, encoding="utf-8").read()
    changed = []

    # 이미 패치 되었으면 건너뜀
    if "MafiaUIMixin" in src and "mafia_ready()" in src:
        print("ALREADY PATCHED")
        return

    # 1) imports
    m0 = re.search(r"^from chat_renderer import .*?ChatRendererMixin.*?$", src, re.M)
    assert m0, "chat_renderer import anchor"
    src = src.replace(m0.group(0), m0.group(0) +
        "\nfrom mafia_config import (AI_PERSONAS, GAME_ROOM_NAME, MIN_PLAYERS,"
        "\n                          DAY_CYCLE_SECONDS, NIGHT_SOLVE_SECONDS,"
        "\n                          VOTE_REVEAL_DELAY)"
        "\nfrom mafia_ui import MafiaUIMixin", 1)
    changed.append("imports")

    # 2) 상속
    m1 = re.search(r"class App\(((?:[A-Za-z]+, 强?)+)+\):", src) if False else None
    prev = re.search(r"class App\([^)]*\):", src)
    assert prev, "class App anchor"
    existing = prev.group(0)
    if "MafiaUIMixin" not in existing:
        newcls = existing[:-2] + ", MafiaUIMixin):"
        src = src.replace(existing, newcls, 1)
        changed.append("inherit")

    # 3) __init__ mafia_ready — pump 예약 라인 근처
    m2 = re.search(r"(\s*self\.root\.after\(80, self\._pump\))", src)
    assert m2, "pump line"
    if "self.mafia_ready()" not in src:
        src = src.replace(m2.group(0), "self.mafia_ready()\n" + m2.group(0), 1)
        changed.append("mafia_ready call")

    # 4) mafia_bar 구성 — chat_wrap 생성 직전
    m3 = re.search(r"(        # 중앙 대화 캔버스.*?\n\s*chat_wrap = tk\.Frame\(body, bg=C_MAIN\)\n\s*chat_wrap\.pack\(fill=\"both\", expand=True\)\n)", src, re.S)
    assert m3, "chat_wrap anchor"
    if "self.mafia_bar" not in src:
        bar_block = '''        # 마피아 게임룸: 헤더 아래 상태 라벨 + [게임 시작]/[설정] (게임룸에서만 노출)
        self.mafia_bar = tk.Frame(body, bg=C_CARD, height=0)
        self.mafia_phase_lbl = tk.Label(self.mafia_bar, text="", fg="#c4b5fd",
                                        bg=C_CARD, font=FONT_XS_PAD, anchor="w")
        self.mafia_cfg_btn = self._btn(self.mafia_bar, "⚙ 게임 설정", self.mafia_settings_dialog,
                                       "#4c1d95", "white", "#6d28d9",
                                       font=FONT_XS_PAD, padx=8, pady=4)
        self.mafia_role_btn = self._btn(self.mafia_bar, "🃏 내 직업 확인", self._reopen_role_popup,
                                        "#0f766e", "white", "#115e59",
                                        font=FONT_XS_PAD, padx=8, pady=4)
        self.mafia_cancel_recruit_btn = self._btn(self.mafia_bar, "❌ 모집 취소", self.mafia_cancel_recruit_clicked,
                                                  "#4b5563", "white", "#374151",
                                                  font=FONT_XS_PAD, padx=8, pady=4)
        self.mafia_join_btn = self._btn(self.mafia_bar, "🙋 참가 신청", self.mafia_toggle_join_clicked,
                                        "#059669", "white", "#047857",
                                        font=FONT_XS_PAD, padx=8, pady=4)
        self.mafia_start_btn = self._btn(self.mafia_bar, "📢 참가자 모집", self.mafia_start_clicked,
                                         "#b91c1c", "white", "#7f1d1d",
                                         font=FONT_XS_PAD, padx=8, pady=4)
        self.mafia_bar_is_game = False

'''
        src = src.replace(m3.group(0), bar_block + m3.group(0), 1)
        changed.append("mafia_bar widgets")

    # 5) _quit 종료 훅
    m4 = re.search(r"(def _quit\(self\):\s*.*?)(\n        self\.root\.destroy\(\))", src, re.S)
    assert m4, "_quit anchor"
    if "mafia_shutdown" not in src:
        src = src.replace(m4.group(0), m4.group(0) +
            "\n        self.mafia_shutdown()\n" + m4.group(2), 1)
        changed.append("mafia_shutdown in _quit")

    open(P, "w", encoding="utf-8").write(src)
    print("PATCH RESULT:", changed)
    import py_compile
    py_compile.compile(P, doraise=True)
    print("COMPILE OK")


if __name__ == "__main__":
    main()
