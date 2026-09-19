# -*- coding: utf-8 -*-
"""mafia_ui.py — MAFIA의 게임방 UI 믹스인.

App(DialogsMixin, ChatRendererMixin, ChatSearchMixin, DndMixin, MafiaUIMixin).
게임방은 가상 키 ("mgame",)로 식별된다. engine의 실제 1:1·그룹 채팅 로직은
전혀 건드리지 않고, 게임룸 안에서만 발언·투표·밤낮을 가로채 처리한다.
"""
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
                          VOTE_REVEAL_DELAY, VOTE_WINDOW,
                          DEFENSE_VOTE_WINDOW, NIGHT_ACTION_WINDOW)
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


class MafiaUIMixin:

    # ==================== 사회자/AI 아바타 색 ====================
    def _avacolor(self, name):
        """랜톡 _avacolor 위임 — 사회자는 고정 보라, AI 페르소나는 전용 색."""
        label = str(name or "")
        base = None
        try:
            base = MafiaUIMixin._super_avacolor(self, name)
        except Exception:
            base = None
        if label.startswith("🖥") or "사회자" in label:
            return M_HOST
        if label in M_AI_COLORS:
            return M_AI_COLORS[label]
        return base or "#94a3b8"


    def _super_avacolor(self, name):
        # 앱의 원래 _avacolor와 동일한 CRC 로직(C_AVA 팔레트)을 따라간다
        import zlib
        from constants import C_AVA
        s = (name or "?").encode("utf-8", "replace")
        return C_AVA[zlib.crc32(s) % len(C_AVA)]

    # ==================== 초기화 ====================
    def mafia_ready(self):
        self.core = GameCore("mafia-room")
        self.ai = AIDirector()
        self.ai.on_utt = self._on_ai_utt
        self._mafia_timer = None
        self.mafia_active = False
        self.mafia_history = []           # 게임방 기록(가상방 — 랜톡 로그 파일에 저장 안 함)
        self.mafia_bar_is_game = False
        self._my_mafia_role = None
        self._defense_entry_locked = False
        self._defense_ticker = None
        self._mafia_active_splash_close = None
        # --- v1.05: 설정 파일 위치 지정(datadir는 app args로 옴) — 저장된 AI API 키 로드 ---
        try:
            _datadir = getattr(getattr(self, "args", None), "datadir", None)
            # v1.61 — datadir가 없어도(exe는 인자 없이 실행됨) 저장된 AI 설정을
            # 시작할 때 항상 읽는다. 예전엔 datadir가 있을 때만 읽어서, 재시작하면
            # 설정창이 빈 칸으로 보여 "저장이 안 된다"고 오해하기 쉬웠다(실제 파일엔
            # 저장돼 있었음 — AI 호출 때 뒤늦게 lazy load만 됐음).
            mafia_config.set_overrides_file(_datadir)   # None이면 %LOCALAPPDATA%\MAFIA 폴백
            mafia_config.load_overrides()
        except Exception:
            pass

    def mafia_room_key(self):
        return ("mgame",)

    # ==================== 게임방 선택 ====================

    # ── 임베디드 팝업(Toplevel 대신 메인창 내부 오버레이) ──
    def _mafia_overlay_open(self, title_text, w=380, h=None):
        """팝업 — 앱 표준 임베드 다이얼로그(dialogs.py _make_embed_dialog) 재사용.
        별도 OS 창을 만들지 않고 본창 중앙 오버레이 위에 패널로 뜬다."""
        prev = getattr(self, "_mafia_overlay_close", None)
        if callable(prev):
            try: prev()
            except Exception: pass
        panel, body, close = self._make_embed_dialog(title_text, w, h)
        def _on_close():
            self._mafia_overlay = None
            close()
        self._mafia_overlay = panel
        self._mafia_overlay_close = _on_close
        return body

    def _mafia_overlay_close(self, *a):
        f = getattr(self, "_mafia_overlay", None)
        if f is not None and f.winfo_exists():
            f.destroy()
        self._mafia_overlay = None
        # v1.17 — 유령방 UI 큐 폴러 정지(유령방 닫힘)
        self._ghost_ui_open = False

    def _reopen_role_popup(self):
        """상단 바 버튼 클릭 시 직업 안내 팝업을 다시 띄운다."""
        role = getattr(self, "_my_mafia_role", None)
        if not role:
            me = getattr(self.engine, "name", None)
            if hasattr(self, "core") and self.core and me in self.core.players:
                role = self.core.players[me].get("role")
        if role:
            self._show_role_popup(role)
        else:
            self.add_mafia_system("ℹ 아직 배정된 직업 정보가 없습니다.")

    def _mafia_maximize_window(self):
        """v1.49 — 마피아 게임이 시작되면 창을 1400x800(가로형)으로 띄운다.
        기존 앱 기본값(450x800, 9:16 세로형)은 참가자·채팅·팝업이 많은 게임
        진행 중엔 너무 좁아서, 마피아 게임 전용 기본 해상도를 요청받음
        (v1.44의 전체화면 대신 고정 크기로 변경)."""
        try:
            self.root.state("normal")   # 이미 최대화 상태면 먼저 풀어야 geometry가 먹는다
        except Exception:
            pass
        try:
            w, h = 1400, 800
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _show_role_popup(self, role):
        """게임 시작 시 화면 중앙에 눈에 띄는 대형 카드 스타일의 직업 안내 팝업을 표시."""
        if not role:
            return
        self._my_mafia_role = role

        # 상단 마피아 바 '내 직업 확인' 버튼 갱신 및 표시
        if hasattr(self, "mafia_role_btn"):
            self.mafia_role_btn.configure(text=f"내 직업: {_role_icon_label(role)}")
            if self._is_mafia_room_active() or getattr(self, "mafia_bar_is_game", False):
                self.mafia_role_btn.pack(side="right", padx=(6, 6), pady=8)

        # 페이즈 라벨 갱신
        self.refresh_mafia_phase_label()

        # 직업별 메타데이터 (아이콘, 이름, 색상, 진영, 설명, 행동 지침)
        META = {
            "mafia": {
                "icon": ROLE_ICON["mafia"],
                "name": "마  피  아",
                "color": "#ef4444",
                "bg_card": "#3f1313",
                "border": "#b91c1c",
                "team": "마피아 진영",
                "team_color": "#f87171",
                "desc": "당신은 어둠 속의 암살자 마피아입니다.\n밤마다 동료와 함께 무고한 시민을 암살하고,\n낮에는 정체를 숨겨 투표로 처형당하지 마세요.",
                "tips": [
                    "밤이 되면 누구를 살해할지 지목하는 팝업이 뜹니다.",
                    "낮 토론 때는 시민인 척 자연스럽게 대화하며 의심을 피하세요.",
                    "시민 진영 인원을 마피아 수 이하로 줄이면 최종 승리합니다!"
                ]
            },
            "doctor": {
                "icon": ROLE_ICON["doctor"],
                "name": "의      사",
                "color": "#10b981",
                "bg_card": "#063025",
                "border": "#059669",
                "team": "시민 진영",
                "team_color": "#34d399",
                "desc": "당신은 생명을 살리는 의사입니다.\n밤마다 마피아의 표적이 될 것 같은 사람 1명을 치료하여\n죽음의 위기에서 구해낼 수 있습니다.",
                "tips": [
                    "밤마다 누구를 구조할지 선택하는 팝업이 뜹니다.",
                    "자기 자신도 치료 대상(자가 치료)으로 선택할 수 있습니다.",
                    "단, 동일한 대상을 2일 연속 치료할 수는 없습니다."
                ]
            },
            "police": {
                "icon": ROLE_ICON["police"],
                "name": "경      찰",
                "color": "#3b82f6",
                "bg_card": "#122347",
                "border": "#2563eb",
                "team": "시민 진영",
                "team_color": "#60a5fa",
                "desc": "당신은 진실을 파헤치는 경찰입니다.\n밤마다 의심스러운 용의자 1명을 지목하여\n그가 마피아인지 시민인지 은밀히 조사할 수 있습니다.",
                "tips": [
                    "밤마다 조사 대상을 선택하는 팝업이 뜹니다.",
                    "조사 결과는 다음 날 아침 당신에게만 비밀리에 보고됩니다.",
                    "결정적인 순간에 마피아의 정체를 밝혀 처형을 이끄세요!"
                ]
            },
            "citizen": {
                "icon": ROLE_ICON["citizen"],
                "name": "시      민",
                "color": "#f59e0b",
                "bg_card": "#382307",
                "border": "#d97706",
                "team": "시민 진영",
                "team_color": "#fbbf24",
                "desc": "당신은 무고하고 선량한 시민입니다.\n특수 능력은 없지만 예리한 관찰력과 추리,\n그리고 단결된 투표권으로 마을을 지켜내야 합니다.",
                "tips": [
                    "낮 토론 때 다른 사람과 AI들의 발언 속 모순을 찾아내세요.",
                    "투표를 통해 숨어있는 마피아를 찾아내 처형대에 세우세요.",
                    "모든 마피아를 찾아내 처형하면 시민 진영이 승리합니다!"
                ]
            }
        }

        info = META.get(role, META["citizen"])

        # 팝업 열기
        body = self._mafia_overlay_open("🃏 당신의 비밀 역할 안내", w=420, h=None)

        # 상단 메인 카드 프레임
        card = tk.Frame(body, bg=info["bg_card"], highlightthickness=2, highlightbackground=info["border"])
        card.pack(fill="x", padx=16, pady=(12, 8))

        # 큰 아이콘 + 큰 직업명
        top_box = tk.Frame(card, bg=info["bg_card"])
        top_box.pack(fill="x", padx=12, pady=(14, 4))

        _lbl_icon = tk.Label(top_box, text=info["icon"], bg=info["bg_card"], fg="white",
                 font=(FONT_FAM, 28))
        _lbl_icon.pack()
        emoji_render.apply(_lbl_icon, (FONT_FAM, 28))
        tk.Label(top_box, text=f"[ {info['name']} ]", bg=info["bg_card"], fg=info["color"],
                 font=(FONT_FAM, 18, "bold")).pack(pady=(2, 2))
        tk.Label(top_box, text=f"소속: {info['team']}", bg=info["bg_card"], fg=info["team_color"],
                 font=M_FONT_BODY_B).pack()

        # 구분선
        tk.Frame(card, bg=info["border"], height=1).pack(fill="x", padx=20, pady=8)

        # 직업 설명
        # v1.44 — wraplength 없으면 긴 줄이 420px 고정폭 패널 밖으로 삐져나가
        # 글자가 잘려 보인다(실측). 줄바꿈이 패널 폭 안에서 되도록 명시.
        tk.Label(card, text=info["desc"], bg=info["bg_card"], fg="#f3f4f6",
                 font=M_FONT_BODY, justify="center", wraplength=360).pack(padx=14, pady=(0, 12))

        # 행동 지침 (플레이 팁) 박스
        tip_box = tk.Frame(body, bg="#1e293b", highlightthickness=1, highlightbackground="#334155")
        tip_box.pack(fill="x", padx=16, pady=(0, 10))

        _lbl_tips_hdr = tk.Label(tip_box, text="💡 핵심 행동 지침", bg="#1e293b", fg="#94a3b8",
                 font=(FONT_FAM, 9, "bold"), anchor="w")
        _lbl_tips_hdr.pack(fill="x", padx=12, pady=(6, 3))
        emoji_render.apply(_lbl_tips_hdr, (FONT_FAM, 9, "bold"))
        for tip in info["tips"]:
            tk.Label(tip_box, text=f"• {tip}", bg="#1e293b", fg="#cbd5e1",
                     font=M_FONT_HELP, anchor="w", justify="left",
                     wraplength=360).pack(fill="x", padx=12, pady=1)
        tk.Frame(tip_box, bg="#1e293b", height=6).pack()

        # 비밀 안내
        if role == "mafia":
            me_nm = getattr(self.engine, "name", None)
            mates = [n for n in self.core.mafias() if n != me_nm] or list(getattr(self, "_my_mafia_mates", None) or [])
            if mates:
                _lbl_mates = tk.Label(body, text="🔪 동료 마피아: " + ", ".join(mates),
                                      fg="#fca5a5", bg=C_CARD, font=(FONT_FAM, 10, "bold"), wraplength=380)
                _lbl_mates.pack(pady=(0, 6))
                emoji_render.apply(_lbl_mates, (FONT_FAM, 10, "bold"))
        _lbl_secret = tk.Label(body, text="🔒 본인의 직업은 다른 사람에게 절대 노출되지 않습니다.",
                 fg=info["color"] if role == "mafia" else "#9ca3af",
                 bg=C_CARD, font=(FONT_FAM, 8), wraplength=380)
        _lbl_secret.pack(pady=(0, 8))
        emoji_render.apply(_lbl_secret, (FONT_FAM, 8))

        # 확인 버튼 — v1.57: 이 팝업만 fill="x"로 폭 100% 늘어나 다른 팝업의
        # 확인/취소 버튼(자동 크기, 가운데 정렬)과 스타일이 달랐다(실측 지적).
        # 다른 팝업들과 동일하게 자동 크기로 통일.
        btn_box = tk.Frame(body, bg=C_CARD)
        btn_box.pack(fill="x", padx=16, pady=(0, 14))

        b_ok = emoji_render.make_pill_button(
            btn_box, "확인 (비밀 유지)", self._mafia_overlay_close,
            bg=info["border"], fg="white", hover_bg=info["color"],
            font_path=emoji_render.FONT_PATH_BOLD, font_size=15,
            radius=10, pad_x=28, pad_y=12
        )
        b_ok.pack()


    # ==================== 드라마틱 시네마틱 연출 시스템 (v1.39) ====================
    # v1.41 — sounds/*.wav 전용 효과음(밤·아침·재판·유죄·무죄·초읽기·승패)을 재생.
    # 파일이 없거나 재생 실패 시에만 기존 윈도우 기본 비프로 폴백한다.
    _MAFIA_SOUND_FILES = {
        "night": "night.wav", "day": "day.wav", "trial": "trial.wav",
        "guilty": "guilty.wav", "innocent": "innocent.wav", "tick": "tick.wav",
        "citizen_win": "citizen_win.wav", "mafia_win": "mafia_win.wav",
    }

    def _play_mafia_sound(self, snd_type):
        """전용 효과음(sounds/*.wav)을 비동기로 안전하게 재생(UI 멈춤 방지, 헤드리스 안전).
        번들 파일이 없으면 윈도우 기본 비프로 폴백."""
        def _worker():
            try:
                import winsound
                fname = self._MAFIA_SOUND_FILES.get(snd_type)
                path = os.path.join(resource_dir(), "sounds", fname) if fname else None
                if path and os.path.isfile(path):
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                if snd_type == "night":
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                elif snd_type == "day":
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                elif snd_type == "trial":
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                elif snd_type == "guilty":
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                elif snd_type == "innocent":
                    winsound.MessageBeep(winsound.MB_OK)
                elif snd_type == "tick":
                    winsound.Beep(1200, 80)
                elif snd_type in ("citizen_win", "mafia_win"):
                    winsound.MessageBeep(winsound.MB_OK if snd_type == "citizen_win" else winsound.MB_ICONHAND)
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _mafia_show_splash(self, title, subtitle, icon="🎭", color="#ffffff",
                           bg_color="#18182f", border_color="#6366f1",
                           duration_ms=1800, sound_type=None):
        """화면 중앙에 1~2초간 웅장하게 나타났다 자동으로 닫히는 시네마틱 컷신 배너."""
        if sound_type:
            self._play_mafia_sound(sound_type)

        # 이전 스플래시가 있으면 즉시 정리
        prev = getattr(self, "_mafia_active_splash_close", None)
        if callable(prev):
            try: prev()
            except Exception: pass

        panel = tk.Frame(self.root, bg=bg_color, highlightthickness=2, highlightbackground=border_color)
        # v1.51 — chat_wrap 기준 정렬(v1.48)을 되돌림 — 사이드바 유무와 무관하게
        # 창 자체의 정중앙에 뜨도록.
        panel.place(relx=0.5, rely=0.42, anchor="center", width=460)
        panel.lift()

        closed = {"done": False}
        def _close():
            if closed["done"]: return
            closed["done"] = True
            self._mafia_active_splash_close = None
            if panel.winfo_exists():
                panel.destroy()

        self._mafia_active_splash_close = _close
        panel.bind("<Button-1>", lambda e: _close())

        box = tk.Frame(panel, bg=bg_color)
        box.pack(fill="both", expand=True, padx=24, pady=18)
        box.bind("<Button-1>", lambda e: _close())

        lbl_icon = tk.Label(box, text=icon, font=(FONT_FAM, 32), bg=bg_color, fg=color)
        lbl_icon.pack(pady=(2, 2))
        lbl_icon.bind("<Button-1>", lambda e: _close())
        emoji_render.apply(lbl_icon, (FONT_FAM, 32))

        lbl_title = tk.Label(box, text=title, font=(FONT_FAM, 16, "bold"), bg=bg_color, fg=color,
                             wraplength=400, justify="center")
        lbl_title.pack(pady=(0, 6))
        lbl_title.bind("<Button-1>", lambda e: _close())
        if emoji_render.has_emoji(title):
            emoji_render.apply(lbl_title, (FONT_FAM, 16, "bold"))

        if subtitle:
            # v1.44 — wraplength 없으면 긴 줄(특히 긴 이름 포함 시)이 460px 고정폭
            # 패널 밖으로 삐져나가 글자가 잘려 보인다 — 역할 팝업과 동일한 문제.
            # 자막은 wraplength로 여러 줄 개행이 필요할 수 있어(컬러 이모지로
            # 바꾸면 통짜 이미지가 되어 개행이 깨짐) 이모지가 없을 때만 그대로 둔다.
            lbl_sub = tk.Label(box, text=subtitle, font=M_FONT_BODY, bg=bg_color, fg="#e2e8f0",
                               justify="center", wraplength=400)
            lbl_sub.pack(pady=(0, 4))
            lbl_sub.bind("<Button-1>", lambda e: _close())

        # duration_ms 후 자동 닫힘
        self.root.after(duration_ms, _close)
        return _close

    def _refresh_mafia_bar_pill_bg(self):
        """게임바(mafia_bar) 배경색이 바뀔 때마다(밤/낮, 변론 스포트라이트 등)
        둥근 버튼들의 '투명해 보이는' 네 귀퉁이도 새 배경색에 맞춰 다시
        그린다 — 안 하면 예전 배경색이 모서리에 네모나게 남아 보인다."""
        for name in ("mafia_cfg_btn", "mafia_role_btn", "mafia_cancel_recruit_btn",
                     "mafia_join_btn", "mafia_start_btn"):
            w = getattr(self, name, None)
            redraw = getattr(w, "_pill_redraw", None)
            if callable(redraw):
                try:
                    redraw()
                except Exception:
                    pass

    def _set_night_theme(self, is_night):
        """밤/낮에 따라 대화 캔버스 및 상단 바 배경 분위기 전환."""
        bg_chat = "#0c101a" if is_night else C_MAIN
        bg_bar = "#111827" if is_night else C_CARD
        if hasattr(self, "chat"):
            try: self.chat.configure(bg=bg_chat)
            except Exception: pass
        if hasattr(self, "chat_wrap"):
            try: self.chat_wrap.configure(bg=bg_chat)
            except Exception: pass
        if hasattr(self, "mafia_bar") and not getattr(self, "_defense_in_progress", False):
            try: self.mafia_bar.configure(bg=bg_bar)
            except Exception: pass
            # mafia_phase_lbl은 mafia_bar의 자식이지만 자기 bg를 따로 갖고 있어서
            # (실측 지적: 밤이 되면 게임바는 어두워지는데 이 라벨만 예전 밝은
            # 카드색 네모가 그대로 남아 있었음) 부모와 같이 맞춰줘야 한다.
            if hasattr(self, "mafia_phase_lbl"):
                try: self.mafia_phase_lbl.configure(bg=bg_bar)
                except Exception: pass
            self._refresh_mafia_bar_pill_bg()

    def _start_defense_visuals(self, name):
        """최후 변론 시작 시 재판정 스포트라이트, 발언권 잠금 및 60초 프로그레스 바 개시."""
        self._mafia_show_splash(
            title=f"재판 개시 — 피고인 [{name}]",
            subtitle=f"'{name}' 님이 최다 득표로 법정 최후 변론대에 출석했습니다.\n60초간 피고인의 목숨이 걸린 최후 변론이 진행됩니다.",
            icon="⚖",
            color="#fca5a5",
            bg_color="#450a0a",
            border_color="#dc2626",
            duration_ms=2200,
            sound_type="trial"
        )
        if hasattr(self, "mafia_bar"):
            self.mafia_bar.configure(bg="#7f1d1d")
            self._refresh_mafia_bar_pill_bg()
        if hasattr(self, "mafia_phase_lbl"):
            self.mafia_phase_lbl.configure(bg="#7f1d1d", fg="#fecaca")

        me_name = getattr(self.engine, "name", None)
        if me_name == name:
            self._defense_entry_locked = False
            if hasattr(self, "status"):
                self.status.set("🎙 [당신은 피고인입니다!] 목숨을 걸고 결백을 증명하세요 (60초)")
            self.add_mafia_system("🔥 [경고 - 당신은 피고인입니다] 지금 채팅으로 결백을 증명하지 못하면 처형당합니다! (60초)")
        else:
            self._defense_entry_locked = True
            if hasattr(self, "entry"):
                self.entry.configure(state="disabled")
            if hasattr(self, "send_btn"):
                self.send_btn.configure(state="disabled")
            if hasattr(self, "status"):
                self.status.set(f"⚖ [피고인 '{name}' 변론 시간] 재판정의 침묵을 유지하세요 (관전자 발언 제한)")

        self._defense_tick_sec = 60
        self._tick_defense_loop(name)

    def _tick_defense_loop(self, name):
        """최후 변론 60초 동안 상단 라벨에 프로그레스 바 실시간 갱신 및 10초 비프."""
        if not self.mafia_active or not getattr(self, "_defense_in_progress", False):
            return
        sec = getattr(self, "_defense_tick_sec", 0)
        if sec < 0:
            return
        blocks = max(0, min(10, int(sec / 60 * 10)))
        bar_str = "■" * blocks + "□" * (10 - blocks)
        color = "#ef4444" if sec <= 10 else "#fecaca"
        if hasattr(self, "mafia_phase_lbl"):
            try:
                self.mafia_phase_lbl.configure(
                    text=f"⚖ 피고인 '{name}' 변론 {sec}초 [{bar_str}]",
                    fg=color
                )
            except Exception:
                pass
        if 0 < sec <= 10:
            self._play_mafia_sound("tick")
        self._defense_tick_sec = sec - 1
        self._defense_ticker = self.root.after(1000, lambda: self._tick_defense_loop(name))

    def _unlock_defense_entry(self):
        """변론 종료 시 발언권 잠금 해제 및 상단 바 복원."""
        self._defense_entry_locked = False
        t = getattr(self, "_defense_ticker", None)
        if t:
            try: self.root.after_cancel(t)
            except Exception: pass
            self._defense_ticker = None
        if hasattr(self, "entry"):
            try: self.entry.configure(state="normal")
            except Exception: pass
        if hasattr(self, "send_btn"):
            try: self.send_btn.configure(state="normal")
            except Exception: pass
        if hasattr(self, "mafia_bar"):
            try: self.mafia_bar.configure(bg=C_CARD)
            except Exception: pass
            self._refresh_mafia_bar_pill_bg()
        if hasattr(self, "mafia_phase_lbl"):
            try: self.mafia_phase_lbl.configure(bg=C_CARD, fg="#c4b5fd")
            except Exception: pass
        self.refresh_mafia_phase_label()

    def _show_verdict_visuals(self, result, name, role2, yes, no):
        """찬반 투표 결과 확정 시 유죄 처형 vs 무죄 방면 시네마틱 컷신."""
        self._unlock_defense_entry()
        if result == "executed":
            self._mafia_show_splash(
                title="[유죄 확정] 단두대 처형 집행",
                subtitle=f"찬성 {yes}표 vs 반대 {no}표로 피고인 '{name}' 님의 처형이 확정되었습니다.\n🎭 피고인의 진짜 정체: [{_role_kr(role2)}]",
                icon="🩸",
                color="#f87171",
                bg_color="#450a0a",
                border_color="#dc2626",
                duration_ms=2500,
                sound_type="guilty"
            )
        else:
            self._mafia_show_splash(
                title="[무죄 방면] 처형 부결",
                subtitle=f"찬성 {yes}표 vs 반대 {no}표로 피고인 '{name}' 님은 혐의를 벗었습니다.\n피고인은 즉시 석방되어 마을로 복귀합니다.",
                icon="🕊",
                color="#6ee7b7",
                bg_color="#064e3b",
                border_color="#059669",
                duration_ms=2200,
                sound_type="innocent"
            )


    # ==================== P2P 배포 — 호스트가 이벤트 전송 ====================
    def _mafia_broadcast(self, ev_type, **kw):
        """게임 이벤트를 모든 접속 피어에게 DM 프로토콜로 전송(호스트 모드에서만)."""
        try:
            from mafia_net import encode
            pkt = encode(ev_type, **kw)
            if not pkt:
                return
            eng = getattr(self, "engine", None)
            if eng is None:
                return
            with eng.plock:
                peers = list(eng.peers.keys())
            for ip_port in peers:
                ip, port = ip_port
                try:
                    eng.send_message(ip, port, pkt)
                except Exception:
                    pass
        except Exception:
            pass

    def _mafia_send_private(self, target_name, ev_type, **kw):
        """특정 플레이어에게만 DM 전송(역할 통보 등 개인 이벤트)."""
        try:
            from mafia_net import encode
            pkt = encode(ev_type, **kw)
            if not pkt:
                return
            eng = getattr(self, "engine", None)
            if eng is None:
                return
            ip_port = self._mafia_peer_of(target_name)
            if ip_port:
                eng.send_message(ip_port[0], ip_port[1], pkt)
        except Exception:
            pass

    def _mafia_peer_of(self, name):
        """별칭 이름에 대응하는 (ip, port) 찾기."""
        eng = getattr(self, "engine", None)
        if eng is None:
            return None
        with eng.plock:
            for (ip, port), p in eng.peers.items():
                alias = eng.get_alias((ip, port)) or ""
                pname = p.get("name", "")
                if alias == name or pname == name:
                    return (ip, port)
        return None

    def _apply_mafia_mates(self):
        """v1.61 — 내가 마피아일 때 동료 마피아를 내 core에 반영(밤 살해 후보에서
        동료를 미리 제외하기 위함). 명단이 아직 안 왔으면 start 수신 때 다시 호출."""
        for m in getattr(self, "_my_mafia_mates", None) or []:
            if m in self.core.players:
                self.core.players[m]["role"] = "mafia"

    def _client_game_end(self, winner, roles):
        """v1.61 — 원격 참가자 쪽 게임 종료 처리: 호스트와 같은 종료 안내를 띄우고
        상태를 로비로 되돌린다(안 그러면 mafia_active가 남아 다음 판 start
        수신 시 core가 LOBBY가 아니라 명단 동기화가 조용히 무시된다)."""
        label = "시민" if winner == "citizen" else "마피아"
        self._mafia_room_close()
        self._play_mafia_sound("citizen_win" if winner == "citizen" else "mafia_win")
        self.add_mafia_system(f"⚖ 게임 종료 — {label} 팀 승리!")
        if roles:
            reveals = ", ".join(f"{n}({ROLE_LABEL_KR.get(r, '?')})" for n, r in roles.items())
            self.add_mafia_system(f"🎭 정체 공개 — {reveals}")
        try:
            self._mafia_overlay_close()
        except Exception:
            pass
        self.mafia_active = False
        self._recruiting = False
        self._recruited_humans = []
        self._my_joined = False
        self._my_mafia_role = None
        self._my_mafia_mates = None
        self._recruiter_host = None
        self.core.lobby_reset()
        self._unlock_defense_entry()
        self._set_night_theme(False)
        if hasattr(self, "mafia_role_btn"):
            self.mafia_role_btn.pack_forget()
        if hasattr(self, "mafia_start_btn"):
            self.mafia_start_btn.configure(text="📢 참가자 모집", bg="#b91c1c",
                                           activebackground="#7f1d1d", state="normal")
        if hasattr(self, "mafia_join_btn"):
            self.mafia_join_btn.pack_forget()
        self.refresh_mafia_phase_label()

    # ---------- 마피아 전용 비밀방(자동 생성) ----------
    def _mafia_room_wanted(self):
        """지금 밤이고 내가 살아있는 마피아이며 사람 동료가 있으면 비밀방이 필요하다."""
        me = getattr(self.engine, "name", None)
        info = self.core.players.get(me) or {}
        role = info.get("role") or getattr(self, "_my_mafia_role", None)
        return (self.mafia_active and self.core.phase == Phase.NIGHT and role == "mafia"
                and info.get("alive", True) and bool(self._mafia_team_names(me)))

    def _maybe_open_mafia_room(self):
        if self._mafia_room_wanted():
            self._mafia_room_history = []
            self._mafia_room_open()

    def _mafia_room_open(self):
        f = getattr(self, "_mafia_room", None)
        try:
            if f is not None and f.winfo_exists():
                f.lift()
                return
        except Exception:
            pass
        mini = getattr(self, "_mafia_room_mini", None)
        self._mafia_room_mini = None
        try:
            if mini is not None and mini.winfo_exists():
                mini.destroy()
        except Exception:
            pass
        f = tk.Frame(self.root, bg=C_CARD, highlightthickness=1, highlightbackground="#b91c1c")
        f.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se", width=310, height=270)
        head = tk.Frame(f, bg=C_CARD)
        head.pack(fill="x", padx=10, pady=(8, 4))
        lbl = tk.Label(head, text="🔪 마피아 비밀방 — 마피아끼리만", bg=C_CARD, fg="#fca5a5",
                       font=(FONT_FAM, 9, "bold"), anchor="w")
        lbl.pack(side="left")
        emoji_render.apply(lbl, (FONT_FAM, 9, "bold"))
        tk.Button(head, text="✕", command=lambda: self._mafia_room_close(keep_reopen=True),
                  bg=C_CARD, fg=C_MUTE,
                  relief="flat", bd=0, highlightthickness=0, cursor="hand2").pack(side="right")
        txt = tk.Text(f, height=9, bd=0, bg="#111827", fg=M_TEXT_LIGHT, font=M_FONT_HELP,
                      wrap="word", state="disabled")
        txt.pack(fill="both", expand=True, padx=10)
        row = tk.Frame(f, bg=C_CARD)
        row.pack(fill="x", padx=10, pady=8)
        ent = tk.Entry(row, bg="#111827", fg=M_TEXT_LIGHT, relief="flat",
                       insertbackground=M_TEXT_LIGHT, font=M_FONT_HELP)
        ent.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        ent.bind("<Return>", self._mafia_room_send)
        emoji_render.make_pill_button(row, "보내기", self._mafia_room_send, bg="#b91c1c", fg="white",
                                      hover_bg="#ef4444", font_path=emoji_render.FONT_PATH_REGULAR,
                                      font_size=9, radius=6, pad_x=10, pad_y=3).pack(side="right")
        self._mafia_room, self._mafia_room_txt, self._mafia_room_ent = f, txt, ent
        for who, t in getattr(self, "_mafia_room_history", []):
            self._mafia_room_insert(who, t)
        f.lift()

    def _mafia_room_insert(self, who, text):
        txt = getattr(self, "_mafia_room_txt", None)
        try:
            txt.configure(state="normal")
            txt.insert("end", f"{who}: {text}\n")
            txt.configure(state="disabled")
            txt.see("end")
        except Exception:
            pass

    def _mafia_room_close(self, keep_reopen=False):
        f = getattr(self, "_mafia_room", None)
        self._mafia_room = None
        try:
            if f is not None and f.winfo_exists():
                f.destroy()
        except Exception:
            pass
        mini = getattr(self, "_mafia_room_mini", None)
        self._mafia_room_mini = None
        try:
            if mini is not None and mini.winfo_exists():
                mini.destroy()
        except Exception:
            pass
        if keep_reopen:
            # ✕로 직접 닫은 경우: 밤이 끝날 때까지 다시 열 수 있는 작은 버튼 유지
            b = tk.Button(self.root, text="🔪 마피아 밀담", command=self._mafia_room_open,
                          bg="#b91c1c", fg="white", relief="flat", bd=0,
                          highlightthickness=0, cursor="hand2", font=M_FONT_HELP)
            b.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
            emoji_render.apply(b, M_FONT_HELP)
            self._mafia_room_mini = b

    def _mafia_room_append(self, who, text):
        if not hasattr(self, "_mafia_room_history"):
            self._mafia_room_history = []
        self._mafia_room_history.append((who, text))
        f = getattr(self, "_mafia_room", None)
        try:
            alive = f is not None and f.winfo_exists()
        except Exception:
            alive = False
        if not alive:
            if self._mafia_room_wanted():
                self._mafia_room_open()      # 동료가 먼저 말을 걸면 자동으로 열린다
            return
        self._mafia_room_insert(who, text)
        f.lift()

    def _mafia_room_send(self, ev=None):
        ent = getattr(self, "_mafia_room_ent", None)
        if ent is None:
            return
        text = ent.get().strip()
        me = getattr(self.engine, "name", None)
        if not text or not me or not self._mafia_room_wanted():
            return
        ent.delete(0, "end")
        self._mafia_room_append("나", text)
        for mate in self._mafia_team_names(me):
            self._mafia_send_private(mate, "mafia_say", name=me, text=text)

    def _mafia_team_names(self, me):
        """내가 마피아일 때 비밀 채팅을 보낼 사람 동료(AI 제외, 나 제외)."""
        names = [n for n in self.core.mafias() if n != me]
        for n in getattr(self, "_my_mafia_mates", None) or []:
            if n not in names and n != me:
                names.append(n)
        # _my_mafia_mates는 게임 시작 때 받은 고정 명단이라 죽은 동료도 남아 있다 —
        # 생존자만 대상으로 해야 동료가 전멸했을 때 비밀방이 안 열린다.
        return [n for n in names
                if not (self.core.players.get(n) or {}).get("is_ai", False)
                and (self.core.players.get(n) or {}).get("alive", True)]

    def _client_start_day_countdown(self):
        """v1.61 — 원격 참가자도 낮 남은 시간을 볼 수 있게 표시 전용 카운트다운을
        로컬에서 돌린다(개표/개행 판정은 하지 않음 — 그건 호스트 몫)."""
        t = getattr(self, "_day_tick", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
            self._day_tick = None
        self._day_deadline = time.time() + DAY_CYCLE_SECONDS
        self._day_tick_loop()

    def _mafia_is_host(self):
        """v1.61 — 지금 이 인스턴스가 방장(호스트)인지. 호스트만 core를 권위
        있게 바꾸고 결과를 브로드캐스트한다 — 나머지는 전부 호스트에게 보내고
        받아서 반영만 하는 클라이언트."""
        return bool(getattr(self, "mafia_host_mode", False))

    def _mafia_send_to_host(self, ev_type, **kw):
        """v1.61 — 클라이언트 → 호스트 개인 전송(투표/밤행동/찬반 등). 내가
        호스트면 이미 로컬에 반영돼 있으니 아무것도 보내지 않는다."""
        if self._mafia_is_host():
            return
        host = getattr(self, "_recruiter_host", None)
        me = getattr(self.engine, "name", None)
        if not host or host == me:
            return
        self._mafia_send_private(host, ev_type, **kw)

    def _on_mafia_proto_msg(self, text, sender_name):
        """클라이언트 쪽 — [MAFIA1] 메시지 수신시. 호스트 권한 only."""
        try:
            from mafia_net import decode
            ev = decode(text)
            if not ev:
                return False
        except Exception:
            return False
        t = ev.get("t")
        if t == "hsay":
            self.add_mafia_bubble(ev.get("text", ""), ev.get("host", "🖥 사회자"))
        elif t == "asay":
            self.add_mafia_bubble(ev.get("text", ""), ev.get("name", "?"))
        elif t == "mafia_say":
            # 마피아 팀 비밀 채팅 — 내가 마피아일 때만 표시(개인 쪽지로만 오지만 이중 방어)
            me_r = getattr(self.engine, "name", None)
            my_role = (self.core.players.get(me_r) or {}).get("role") or getattr(self, "_my_mafia_role", None)
            if my_role == "mafia" and ev.get("text"):
                self._mafia_room_append(ev.get("name") or "?", ev.get("text", ""))
        elif t == "user_say":
            # v1.61 — 게임 시작 후(낮/밤) 다른 사람의 발언 수신. 호스트는 AI가
            # 이 발언을 실제로 기억하도록 observe_all에도 넣어줘야 한다(이전엔
            # 원격 발언을 AI가 전혀 인식 못했음).
            name = ev.get("name") or "?"
            say_text = ev.get("text", "")
            if say_text:
                self.add_mafia_bubble(say_text, name)
                if self._mafia_is_host() and getattr(self, "ai", None):
                    self.ai.observe_all(name, say_text)
        elif t == "sys":
            self.add_mafia_system(ev.get("text", ""))
        elif t == "hdm":
            # 개인 쪽지 — target==내 이름일 때만 표시
            me = getattr(self.engine, "name", None)
            if ev.get("target") == me:
                msg_txt = ev.get("text", "")
                self.add_mafia_host_dm(msg_txt)
                role = ev.get("role")
                if not role:
                    for r_key, r_kr in [("mafia", "마피아"), ("doctor", "의사"), ("police", "경찰"), ("citizen", "시민")]:
                        if f"'{r_kr}'" in msg_txt or f"'{r_key}'" in msg_txt:
                            role = r_key
                            break
                if role:
                    self._my_mafia_role = role
                    if hasattr(self, "core") and self.core and me in self.core.players:
                        self.core.players[me]["role"] = role
                    mates = ev.get("mates")
                    if role == "mafia" and mates:
                        self._my_mafia_mates = list(mates)
                        self._apply_mafia_mates()
                    self.root.after(100, lambda r=role: self._show_role_popup(r))
        elif t == "start":
            self.mafia_active = True
            self._recruiting = False
            self._recruited_humans = []
            self._my_joined = False
            if hasattr(self, "mafia_start_btn"):
                self.mafia_start_btn.configure(text="[게임 진행 중]", state="disabled")
            if hasattr(self, "mafia_join_btn"):
                self.mafia_join_btn.pack_forget()
            if hasattr(self, "mafia_cancel_recruit_btn"):
                self.mafia_cancel_recruit_btn.pack_forget()
            # v1.61 — 원격 참가자 명단 동기화. 이전엔 이 이벤트가 UI 갱신만 하고
            # self.core.players를 전혀 채우지 않아, 원격 참가자의 core는 게임
            # 시작 후에도 계속 빈 채로 남아 생존자 조회·투표·밤 행동 렌더링이
            # 전부 불가능했다(복수 인간 플레이 전수 검토 지적).
            if not self._mafia_is_host() and getattr(self, "core", None):
                roster = ev.get("players", [])
                with self.core.lock:
                    if self.core.phase == Phase.LOBBY:
                        for entry in roster:
                            if isinstance(entry, dict):
                                nm, is_ai = entry.get("name"), bool(entry.get("is_ai"))
                            else:
                                nm, is_ai = entry, False   # 구버전 호환(문자열 명단)
                            if nm and nm not in self.core.players:
                                self.core.join(nm, is_ai=is_ai)
                        self.core.phase = Phase.DAY
                        self.core.day_no = 1
                        self.root.after(0, self._client_start_day_countdown)
                    me = getattr(self.engine, "name", None)
                    my_role = getattr(self, "_my_mafia_role", None)
                    if my_role and me in self.core.players:
                        self.core.players[me]["role"] = my_role
                    self._apply_mafia_mates()
            self.refresh_mafia_phase_label()
        elif t == "night":
            self.core.phase_placeholder = None
            self._unlock_defense_entry()
            self._set_night_theme(True)
            self._mafia_show_splash(
                title="밤이 찾아왔습니다",
                subtitle="모두 고개를 숙여주세요…\n어둠 속에서 마피아가 눈을 뜨고 활동을 시작합니다.",
                icon="🌙",
                color="#c4b5fd",
                bg_color="#131525",
                border_color="#6366f1",
                duration_ms=1800,
                sound_type="night"
            )
            try:
                self.core.phase = Phase.NIGHT
                self.refresh_mafia_phase_label()
                # v1.61 — 원격 참가자도 자기 직업이 마피아/의사/경찰이면 밤 행동
                # 패널이 떠야 한다(이전엔 이 이벤트를 아예 안 보내서 원격 밤
                # 행동 자체가 불가능했음 — 복수 인간 플레이 전수 검토 지적).
                self.root.after(1800, self._show_night_panel)
                self.root.after(2200, self._maybe_open_mafia_room)
            except Exception:
                pass
        elif t == "day":
            self._unlock_defense_entry()
            self._set_night_theme(False)
            # v1.61 — 밤 결과(사망자/역할 공개) 동기화 + 남아있는 내 밤 행동
            # 패널 정리(호스트가 이미 밤을 끝냈는데 원격 화면엔 패널이 계속
            #떠 있던 문제 방지).
            try:
                self._mafia_overlay_close()
            except Exception:
                pass
            self._mafia_room_close()
            victim = ev.get("victim")
            role = ev.get("role")
            if victim and victim in self.core.players:
                self.core.players[victim]["alive"] = False
                if role:
                    self.core.players[victim]["role"] = role
            # v1.61 — 호스트와 같은 아침 시네마틱 + 본인 사망 시 유령방
            if victim:
                self._mafia_show_splash(
                    title=f"간밤의 비극 — '{victim}' 사망",
                    subtitle=f"마피아의 잔혹한 습격으로 '{victim}' 님이 사망했습니다.\n🎭 정체: [{_role_kr(role)}]",
                    icon="🕯", color="#f87171", bg_color="#3b0d0d",
                    border_color="#ef4444", duration_ms=2500, sound_type="trial")
                if victim == getattr(self.engine, "name", None):
                    self.root.after(2600, self._open_ghost_chat)
            else:
                self._mafia_show_splash(
                    title="새로운 아침이 밝았습니다",
                    subtitle="의사의 신속한 치료로 오늘 밤은 아무도 희생되지 않았습니다!\n평화로운 아침 토론을 시작하세요.",
                    icon="☀", color="#fde047", bg_color="#2b2308",
                    border_color="#eab308", duration_ms=2000, sound_type="day")
            try:
                self.core.day_no += 1
                self.core.phase = Phase.DAY
                self.refresh_mafia_phase_label()
                self._client_start_day_countdown()
            except Exception:
                pass
        elif t == "vote_open":
            # v1.61 — 호스트가 낮 투표를 개시하면 원격 화면에도 투표 팝업을 연다.
            if not self._mafia_is_host() and self.mafia_active:
                self.core.votes.clear()
                self.core.abstains.clear()
                self.core.phase = Phase.DAY
                self._show_vote_popup()
        elif t == "revote_open":
            if not self._mafia_is_host() and self.mafia_active:
                self._open_revote_popup(ev.get("tied") or [])
        elif t == "defense_vote_open":
            if not self._mafia_is_host() and self.mafia_active:
                self.core.defense_yes = {}
                self._show_defense_vote_popup(ev.get("name"))
        elif t == "end":
            if not self._mafia_is_host():
                self._client_game_end(ev.get("winner"), ev.get("roles") or {})
        elif t == "defense_start":
            self.core.defendant = ev.get("name")
            self._start_defense_visuals(ev.get("name", "피고인"))
        elif t == "verdict":
            # v1.61 — 처형 확정이면 원격 core에서도 사망 처리 + 본인이면 유령방
            vname, vrole = ev.get("name"), ev.get("role")
            if ev.get("result") == "executed" and vname in self.core.players:
                self.core.players[vname]["alive"] = False
                if vrole:
                    self.core.players[vname]["role"] = vrole
                if vname == getattr(self.engine, "name", None):
                    self._open_ghost_chat()
            self.core.defendant = None
            self._show_verdict_visuals(
                ev.get("result", ""),
                ev.get("name", ""),
                ev.get("role", ""),
                ev.get("yes", 0),
                ev.get("no", 0)
            )
        elif t == "vote":
            # v1.61 — 호스트가 뿌리는 투표 진행 재동기화(누가 찬성/기권했는지
            # 자체가 아니라 '접수됐다'만 미러링 — 다른 원격 참가자들의 진행률
            # 표시가 항상 정확하도록). target이 없으면 기권.
            v = ev.get("voter")
            if v and v in self.core.players:
                if "target" in ev and ev.get("target"):
                    tg = ev.get("target")
                    if tg in self.core.players:
                        self.core.cast_vote(v, tg)
                else:
                    self.core.cast_abstain(v)
                self._refresh_vote_progress_label()
        elif t == "vote_cast":
            # 클라이언트 → 호스트: 원격 참가자의 실제 낮 투표 선택.
            self._host_receive_vote_cast(ev.get("voter"), ev.get("target"))
        elif t == "defense_vote_cast":
            # 클라이언트 → 호스트: 원격 참가자의 최후 변론 찬반 표.
            self._host_receive_defense_vote(ev.get("voter"), ev.get("name"), ev.get("yes"))
        elif t == "night_action":
            # 클라이언트 → 호스트: 원격 참가자의 밤 행동(살해/치료/조사).
            self._host_receive_night_action(ev.get("actor"), ev.get("role"), ev.get("target"))
        elif t == "death":
            # v1.61 — 접속 끊김 등으로 인한 사망 처리 동기화(호스트가 판정).
            nm = ev.get("name")
            if nm and nm in self.core.players:
                self.core.players[nm]["alive"] = False
        elif t == "tally":
            self.add_mafia_system(ev.get("text", ""))
            try:
                self.core.phase = Phase.DAY
            except Exception:
                pass
        elif t == "recruit_start":
            host = ev.get("host", "방장")
            self._recruiting = True
            self._recruiter_host = host
            self._recruited_humans = list(ev.get("players", []))
            me = getattr(self.engine, "name", None)
            self._my_joined = (me in self._recruited_humans)
            if host != me:
                if hasattr(self, "mafia_start_btn"):
                    self.mafia_start_btn.pack_forget()
                if hasattr(self, "mafia_cancel_recruit_btn"):
                    self.mafia_cancel_recruit_btn.pack_forget()
                if hasattr(self, "mafia_join_btn"):
                    self.mafia_join_btn.config(
                        text="✋ 참가 취소" if self._my_joined else "🙋 참가 신청",
                        bg="#dc2626" if self._my_joined else "#059669",
                        activebackground="#b91c1c" if self._my_joined else "#047857"
                    )
                    self.mafia_join_btn.pack(side="right", padx=(10, 6), pady=8)
                self.mafia_phase_lbl.config(text=f"📢 {host}님 방 참가 모집 중…")
                self.add_mafia_system(
                    f"📢 [마피아 참가자 모집] 방장 '{host}' 님이 게임 참가자를 모집합니다!\n"
                    f"👉 참여를 원하시면 상단 [🙋 참가 신청] 버튼을 누르거나 채팅에 '/참가'를 입력하세요.\n"
                    f"현재 참가자: {', '.join(self._recruited_humans)} ({len(self._recruited_humans)}명)"
                )
        elif t == "recruit_join":
            pname = ev.get("name")
            me = getattr(self.engine, "name", None)
            if getattr(self, "_recruiting", False) and getattr(self, "_recruiter_host", None) == me:
                if pname and pname not in self._recruited_humans:
                    self._recruited_humans.append(pname)
                    self.add_mafia_system(f"🙋 '{pname}' 님이 참가 신청했습니다! (현재 {len(self._recruited_humans)}명)")
                    self.mafia_start_btn.config(text=f"🎮 게임 시작 (인간 {len(self._recruited_humans)}명)")
                    self._mafia_broadcast("recruit_update", host=me, players=self._recruited_humans)
        elif t == "recruit_leave":
            pname = ev.get("name")
            me = getattr(self.engine, "name", None)
            if getattr(self, "_recruiting", False) and getattr(self, "_recruiter_host", None) == me:
                if pname in self._recruited_humans:
                    self._recruited_humans.remove(pname)
                    self.add_mafia_system(f"✋ '{pname}' 님이 참가를 취소했습니다. (현재 {len(self._recruited_humans)}명)")
                    self.mafia_start_btn.config(text=f"🎮 게임 시작 (인간 {len(self._recruited_humans)}명)")
                    self._mafia_broadcast("recruit_update", host=me, players=self._recruited_humans)
        elif t == "recruit_update":
            self._recruited_humans = list(ev.get("players", []))
            me = getattr(self.engine, "name", None)
            self._my_joined = (me in self._recruited_humans)
            if hasattr(self, "mafia_join_btn") and getattr(self, "_recruiter_host", None) != me:
                self.mafia_join_btn.config(
                    text="✋ 참가 취소" if self._my_joined else "🙋 참가 신청",
                    bg="#dc2626" if self._my_joined else "#059669",
                    activebackground="#b91c1c" if self._my_joined else "#047857"
                )
            self.add_mafia_system(f"📋 참가자 명단 갱신 ({len(self._recruited_humans)}명): {', '.join(self._recruited_humans)}")
        elif t == "recruit_cancel":
            self._recruiting = False
            self._recruited_humans = []
            self._my_joined = False
            self._recruiter_host = None
            if hasattr(self, "mafia_join_btn"):
                self.mafia_join_btn.pack_forget()
            if hasattr(self, "mafia_cancel_recruit_btn"):
                self.mafia_cancel_recruit_btn.pack_forget()
            if hasattr(self, "mafia_start_btn"):
                self.mafia_start_btn.pack(side="right", padx=(10, 6), pady=8)
                self.mafia_start_btn.config(text="📢 참가자 모집", bg="#b91c1c", activebackground="#7f1d1d", state="normal")
            self.mafia_phase_lbl.config(text="")
            self.add_mafia_system("📢 방장이 참가자 모집을 취소했습니다.")
        elif t == "lobby_chat":
            sender = ev.get("sender", "알 수 없음")
            msg_text = ev.get("text", "")
            me = getattr(self.engine, "name", None)
            if sender != me and msg_text:
                self.add_mafia_bubble(msg_text, sender)
        return True

    def _select_mafia_room(self, key):
        # v1.50 — "게임 시작" 버튼을 눌러야만 1400x800이 적용되던 것을, 마피아
        # 게임방에 들어오는 시점(로비 포함)으로 앞당김 — 실측 지적: 방에 막 들어왔을
        # 때(아직 게임 시작 전)는 여전히 기존 450x800 그대로였음.
        if not getattr(self, "_mafia_resized_once", False):
            self._mafia_resized_once = True
            self._mafia_maximize_window()
        self.current = key
        self.unread[key] = 0
        self.ch_title.config(text="🎭 " + GAME_ROOM_NAME)
        self.ch_sub.config(text="AI 사회자 운영", fg="#c4b5fd")
        self.member_btn.pack_forget()
        self._show_room_buttons()
        # 게임바(상태 라벨 + 시작 버튼) 노출
        self.mafia_bar_is_game = True
        self.mafia_phase_lbl.pack(side="left", padx=(14, 8), pady=6)
        self.mafia_cfg_btn.pack(side="right", padx=(6, 6), pady=8)
        if hasattr(self, "mafia_role_btn"):
            if self.mafia_active and getattr(self, "_my_mafia_role", None):
                self.mafia_role_btn.configure(text=f"내 직업: {_role_icon_label(self._my_mafia_role)}")
                self.mafia_role_btn.pack(side="right", padx=(6, 6), pady=8)
            else:
                self.mafia_role_btn.pack_forget()
        me_check = getattr(self.engine, "name", None)
        if getattr(self, "_recruiting", False):
            if getattr(self, "_recruiter_host", None) == me_check:
                self.mafia_start_btn.pack(side="right", padx=(10, 6), pady=8)
                if hasattr(self, "mafia_cancel_recruit_btn"):
                    self.mafia_cancel_recruit_btn.pack(side="right", padx=(6, 6), pady=8)
                if hasattr(self, "mafia_join_btn"):
                    self.mafia_join_btn.pack_forget()
            else:
                self.mafia_start_btn.pack_forget()
                if hasattr(self, "mafia_cancel_recruit_btn"):
                    self.mafia_cancel_recruit_btn.pack_forget()
                if hasattr(self, "mafia_join_btn"):
                    self.mafia_join_btn.pack(side="right", padx=(10, 6), pady=8)
        else:
            self.mafia_start_btn.pack(side="right", padx=(10, 6), pady=8)
            if hasattr(self, "mafia_cancel_recruit_btn"):
                self.mafia_cancel_recruit_btn.pack_forget()
            if hasattr(self, "mafia_join_btn"):
                self.mafia_join_btn.pack_forget()
        self.mafia_bar.pack(fill="x", before=self.chat_wrap)
        self.entry.configure(state="normal", font=FONT_MSG)
        self.send_btn.configure(state="normal")
        self.attach_btn.configure(state="normal")
        self.burn_btn.configure(state="normal")
        self.emoji_btn.configure(state="normal")
        self._sync_burn_btn_visual()
        self.status.set("마피아 게임방 — [참가자 모집]을 누르세요")
        self.entry.focus_set()
        # 로비에서 이미 시작된 게임이 있어도 대화가 보이도록 임시 허용
        prev_active = self.mafia_active
        self.mafia_active = True
        self._mafia_rerender()
        self.mafia_active = prev_active
        self.refresh_mafia_phase_label()
        self._update_pin_banner()
        self._refresh_list()

    def _mafia_rerender(self):
        """게임방 기록을 말풍선으로 다시 그린다(_reload_chat과 동일 규약)."""
        self.chat.delete("all")
        self._chat_y = PAD_TOP
        self._last_day = None
        self._last_sender = None
        self._last_ts = None
        self._last_burn_sec = 0
        self._last_has_unread = False
        self._last_is_sticker = False
        self._peer_avatar_bottom = 0
        self._chat_images = []
        self._record_y_positions = {}
        self._record_y_end = {}
        self._last_rendered_width = None   # 폭 변경 감지 캐시 무효화
        self._active_chat_key = self.mafia_room_key()
        self._active_chat_records = []
        if not self.mafia_history:
            self._show_empty("🎮 마피아 게임 방\n[참가자 모집]을 누르면 인원을 모은 뒤\n[게임 시작]으로 AI 사회자가 역할을 배정합니다")
            self._finish_render()
            return
        self._hide_empty()
        for rec in self.mafia_history:
            self._render_mafia_record(rec)
        self._finish_render(force_bottom=True)

    def _render_mafia_record(self, rec):
        if rec.get("kind") == "system":
            self._draw_system_sep(rec.get("text", ""))
        else:
            text = rec.get("text", "")
            if rec.get("kind") == "host_dm":
                text = "🔒 [나에게만] " + text
            self._draw_record({"kind": "text", "mine": bool(rec.get("mine")),
                                "label": rec.get("label", "?"), "ts": rec.get("ts"),
                                "text": text, "reply": None,
                                "burn_sec": 0})
        self._active_chat_records.append(rec)

    def _mafia_append_live(self, rec):
        """살아있는 렌더(다시 그리지 않고 이어 그리기)."""
        self._render_mafia_record(rec)
        self._finish_render(force_bottom=True)

    # ==================== 목록 행과 헤더에서 게임방 보이게 ====================
    def _mafia_pump_list_inject(self, items):
        """_refresh_list의 items에 (mgame,)을 맨 위로 삽입(App._refresh_list에서 호출)."""
        items.insert(0, {"key": self.mafia_room_key(),
                          "kind": "mgame", "name": GAME_ROOM_NAME,
                          "online": True, "game_room": True})

    # ==================== 유저 입력 ====================
    def _mafia_handle_user_text(self, text):
        # --- v1.36: 모집 기간 중 채팅 명령어 (/참가, /취소) ---
        clean_cmd = text.strip()
        if clean_cmd in ("/참가", "/신청", "/join"):
            if getattr(self, "_recruiting", False):
                self._handle_chat_join()
                return
        elif clean_cmd in ("/취소", "/leave"):
            if getattr(self, "_recruiting", False):
                self._handle_chat_leave()
                return

        # --- v1.37: 게임 시작 전(로비/모집 중) 인간 전용 자유 단체 대화 ---
        if not self.mafia_active:
            self.add_mafia_bubble(text, "나", mine=True)
            me = getattr(self.engine, "name", None) or "나"
            self._mafia_broadcast("lobby_chat", sender=me, text=text)
            return

        # --- 보강(v1.02): 사망자 채팅 차단 — 관전만 가능, 유령 채팅방 안내 ---
        if getattr(self.core, "players", None):
            me_info = self.core.players.get(getattr(self.engine, "name", None)) or None
            if me_info is not None and not me_info.get("alive", True):
                self.add_mafia_system("👻 사망한 참가자는 게임방 채팅을 칠 수 없습니다 — 유령 채팅방에서만 수다")
                self._open_ghost_chat()
                return

        # --- v1.39: 최후 변론 중 관전자 발언권 제한 ---
        if getattr(self, "_defense_entry_locked", False):
            self.add_mafia_system("🚫 피고인의 최후 변론 시간입니다. 관전자는 발언할 수 없습니다.")
            return
        # 밤 행동 (역할자만)
        if self.core.phase == Phase.NIGHT:
            me = self.engine.name
            my_role = (self.core.players.get(me) or {}).get("role")
            m = re.match(r"^(?:살해|마피아|kill)\s+([^\s]+)$", text.strip())
            if m and my_role == "mafia" and not self._mafia_is_host():
                self._mafia_send_to_host("night_action", actor=me, role="mafia", target=m.group(1).strip())
                self.add_mafia_host_dm("⏳ 밤 행동을 호스트에게 전달했습니다 — 처리 결과는 곧 알려드립니다.")
                return
            if m and my_role == "mafia":
                t = m.group(1).strip()
                if t in self.core.alive_players() and t != me:
                    if self.core.set_night_target(t):
                        self.core.mafia_night_vote(me, t)
                        # 마피아 밤 행동은 비밀 — 개인 사회자 쪽지로만 확인 (게임방 공개 X)
                        self.add_mafia_host_dm(f"🔪 (마피아 신청 접수) {t} 살해 지시 — 밤이 끝나면 공개됩니다.")
                    else:
                        self.add_mafia_host_dm(f"'{t}'은(는) 마피아 동료이므로 살해할 수 없습니다.")
                else:
                    self.add_mafia_host_dm(f"'{t}'은(는) 살해 대상이 될 수 없습니다 (자기 자신 또는 생존자 아님).")
                return
            m = re.match(r"^(?:구조|의사|save|heal)\s+([^\s]+)$", text.strip())
            if m and my_role == "doctor" and not self._mafia_is_host():
                self._mafia_send_to_host("night_action", actor=me, role="doctor", target=m.group(1).strip())
                self.add_mafia_host_dm("⏳ 밤 행동을 호스트에게 전달했습니다 — 처리 결과는 곧 알려드립니다.")
                return
            if m and my_role == "doctor":
                t = m.group(1).strip()
                if t in self.core.alive_players():
                    # v1.29/v1.34 — doctor_protect로 연속 보호 금지 검증 (자신 포함 1회 허용, 연속 금지)
                    if self.core.doctor_protect(t):
                        target_lbl = f"{t} (자신)" if t == me else t
                        self.add_mafia_host_dm(f"💉 (의사 신청 접수) {target_lbl} 구조 지시")
                    else:
                        self.add_mafia_host_dm(
                            f"⚠ {t}은(는) 어제 밤에 이미 보호했습니다 — 연속 보호 금지. "
                            f"다른 대상을 입력하세요 (예: '구조 이름').")
                else:
                    self.add_mafia_host_dm(f"'{t}'은(는) 구조 대상이 될 수 없습니다 (생존자 아님).")
                return
            m = re.match(r"^(?:조사|경찰|investigate|check)\s+([^\s]+)$", text.strip())
            if m and my_role == "police" and not self._mafia_is_host():
                self._mafia_send_to_host("night_action", actor=me, role="police", target=m.group(1).strip())
                self.add_mafia_host_dm("⏳ 밤 행동을 호스트에게 전달했습니다 — 처리 결과는 곧 알려드립니다.")
                return
            if m and my_role == "police":
                t = m.group(1).strip()
                if t in self.core.alive_players() and t != me:
                    res = self.core.police_investigate(t)
                    if res == "mafia":
                        self.add_mafia_host_dm(f"🕵 [조사 결과 — 나에게만] {t}님은 마피아입니다!")
                    elif res == "citizen":
                        self.add_mafia_host_dm(f"🕵 [조사 결과 — 나에게만] {t}님은 마피아가 아닙니다.")
                    else:
                        self.add_mafia_host_dm(f"🕵 ({me} 경찰 신청) {t} 조사 접수 — 아침에 결과 통보")
                else:
                    self.add_mafia_host_dm(f"'{t}'은(는) 조사 대상이 될 수 없습니다 (자기 자신 또는 생존자 아님).")
                return
            # 밤에는 일반 발언 제한 없음(로비 발언 역할 없음)
            self.add_mafia_bubble(text, "나", mine=True)
            self._mafia_broadcast("user_say", name=me, text=text)
            if getattr(self, "ai", None):
                self.ai.observe_all(self.engine.name, text)
            # 마피아/의사/경찰 역할자가 아직 신청 안 했으면 안내
            if my_role == "mafia" and not self.core.night_target:
                self.add_mafia_system("(안내) 마피아는 '살해 이름'으로 밤 행동을 알려야 합니다.")
            elif my_role == "doctor" and not self.core.night_saved:
                self.add_mafia_system("(안내) 의사는 '구조 이름'으로 밤 행동을 알려야 합니다.")
            elif my_role == "police" and not self.core.police_report:
                self.add_mafia_system("(안내) 경찰은 '조사 이름'으로 밤 행동을 알려야 합니다.")
            return
        if self.core.phase == Phase.DAY:
            m = re.match(r"^(?:투표|지목|vote)\s+([^\s]+)$", text.strip()) or \
                re.match(r"^@([^\s]+)\s*$", text.strip())
            if m:
                target = m.group(1).strip()
                me = self.engine.name
                ok = self.core.cast_vote(me, target)
                if ok:
                    # v1.47 — 대상 이름을 내 말풍선에도 남기지 않는다(익명 개표와 완전히 일치시켜
                    # '정말 비공개 맞나' 하는 의심을 원천 차단). 자기 자신은 로컬에서만 보이는
                    # 화면이라 실제로 새어나간 적은 없지만, 눈에 보이는 문구부터 통일한다.
                    self.add_mafia_bubble("투표 완료 (익명 개표)", "나", mine=True)
                    _pd, _pt = self._vote_progress_counts()
                    self.add_mafia_system(f"🗳 {me}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                    self._refresh_vote_progress_label()
                    if self._mafia_is_host():
                        self._mafia_broadcast("vote", voter=me, target=target)
                    else:
                        self._mafia_send_to_host("vote_cast", voter=me, target=target)
                    # v1.18 — 투표 팝업이 열려 있으면 즉시 닫기 + 잔여 버튼 잠금
                    try:
                        if getattr(self, "_vote_lbl", None):
                            self._cancel_vote_popup()
                        if getattr(self, "_mafia_overlay", None) is not None:
                            self._mafia_overlay_close()
                        self._update_vote_btn_state()
                    except Exception:
                        pass
                else:
                    self.add_mafia_system(f"투표 불가 — '{target}'은(는) 생존 참가자가 아닙니다.")
                if self.core.all_voted():
                    self.add_mafia_host("전원이 투표했습니다. 개표하겠습니다.")
                    self._schedule_tally(300)
                return
        # 일반 발언 → 게임방 말풍선
        self.add_mafia_bubble(text, "나", mine=True)
        # v1.61 — 게임 시작 후(낮/밤) 사람 발언은 로비 채팅과 달리 네트워크로
        # 전혀 전송되지 않고 있었다(복수 인간 플레이 전수 검토 지적) — 로비
        # 채팅(lobby_chat)과 동일한 방식(전원 메쉬 브로드캐스트)으로 통일.
        self._mafia_broadcast("user_say", name=getattr(self.engine, "name", None), text=text)
        if getattr(self, "ai", None):
            self.ai.observe_all(self.engine.name, text)   # 모든 AI가 내 말을 기억
        # v1.30 — 협박 발언 즉시 AI 불쾌 반응 (톤 감지 1회)
        import re as _re2
        _threat_kw = ("죽", "해치", "죽여", "처형", "무조건", "필사", "협박", "봉인")
        if self.core.phase in (Phase.DAY, Phase.VOTE) and any(k in text for k in _threat_kw):
            import random as _rq
            live = [pl for pl in getattr(self, "ai", None) and self.ai.players or []
                    if pl.alive and getattr(pl, "booted", False)]
            if live:
                pick = _rq.choice(live)
                self.root.after(600, lambda p=pick: self._ai_threat_reaction(p))
        if self.core.phase == Phase.DAY:
            self._ai_chain_count = 0
            # 이름 직접 호출 → 불린 AI만 대답, 아니면 일반 상황 반응 1회
            mentioned = self._detect_mention(text)
            if mentioned:
                self._trigger_mentions_reply(mentioned, text)
            else:
                self._trigger_ai_reactions(
                    context=f"플레이어 '{self.engine.name}'의 발언: \"{text}\"",
                    min_interval=5)

    # ==================== 이름 멘션 직접 대답 ====================
    def _detect_mention(self, text):
        """유저 메시지에 AI 이름이 직접 포함되면 해당 AI 목록. @이름/'OOO아' 포함."""
        if not getattr(self, "ai", None):
            return None
        hits = []
        for pl in self.ai.players:
            if not pl.alive or not pl.booted:
                continue
            nm = pl.name
            if nm in text or (nm and nm[:-1] + "아") in text or (nm and nm[:-1] in text):
                hits.append(pl)
        return hits or None

    def _trigger_mentions_reply(self, mentioned, user_text):
        """이름 불린 AI는 반드시 그 사람에게 대답(순차 지연 틈 후 비동기)."""
        alive = ", ".join(self.core.alive_players())
        for idx, pl in enumerate(mentioned):
            factory = (lambda pl=pl: (
                f"[직접 지목] '{self.engine.name}'님이 당신('{pl.name}')의 이름을 불렀습니다.\n"
                f"발언: \"{user_text}\"\n"
                f"그 사람에게 직접 대답하세요: 이름을 부르며 질문에 답하거나 태도를 밝히세요. "
                f"2문장 이내.\n[참고] 현재 낮 {self.core.day_no}, 생존: {alive}"))
            delay = 700 + idx * 900
            self.root.after(delay, lambda f=factory: self.ai.say_async(f))
        others = [pl for pl in self.ai.players
                  if pl.alive and pl.booted and pl not in mentioned]
        if others and random_mod.random() < 0.5:
            pick = random_mod.choice(others)
            factory2 = (lambda pl=pick: (
                f"[게임 상황] '{self.engine.name}' 님이 '{mentioned[0].name}'님에게 "
                f"말했습니다: \"{user_text}\"\n"
                f"당신('{pl.name}')은 그 대화에 곁에서 한마디만 보태세요. 2문장 이내."))
            self.root.after(1900, lambda f=factory2: self.ai.say_async(f))

    # ==================== 밤 AI 행동 ====================
    def _trigger_night_actions(self):
        """마피아 AI에게 '살해 대상' 추천을, 의사 AI에게 '구조 대상' 추천을 받아
        core.night_target / night_saved에 반영."""
        mafia_ais = [pl for pl in self.ai.players
                     if pl.alive and pl.role == "mafia" and pl.booted]
        doctor_ais = [pl for pl in self.ai.players
                      if pl.alive and pl.role == "doctor" and pl.booted]
        alive = self.core.alive_players()
        results = {"multi": [], "multi_pairs": []}

        import random as _rr
        all_mafias = set(self.core.mafias())
        for pl in mafia_ais:
            # 인간 마피아까지 포함해 동료 살해 제외 + 자투 금지
            cand = [n for n in alive if n != pl.name and n not in all_mafias]
            if cand:
                t = _rr.choice(cand)
                results.setdefault("kill", t)
                results["multi"].append(t)
                results["multi_pairs"].append((pl.name, t))
        police_ais = [pl for pl in self.ai.players
                      if pl.alive and pl.role == "police" and pl.booted]
        for pl in police_ais:
            already = [t for t in self.core.police_invest]  # 중복 조사 방지
            cand = [n for n in alive if n != pl.name and n not in already]
            if cand:
                results["invest"] = _rr.choice(cand)
        last_saved = getattr(self.core, "last_protect", None)
        for pl in doctor_ais:
            # 의사 구조 후보: 자투 금지 + 마피아 제외 + 어젯밤 연속 보호 제외
            cand = [n for n in alive if n != pl.name and n not in all_mafias and n != last_saved]
            if not cand:
                cand = [n for n in alive if n != pl.name and n not in all_mafias]
            if results.get("kill") in cand and _rr.random() < 0.30 and len(cand) > 1:
                cand.remove(results["kill"])
            if cand:
                results["save"] = _rr.choice(cand)
        self.root.after(0, lambda: self._apply_night_actions(results))

    def _apply_night_actions(self, results):
        if not self.mafia_active:
            return
        with self.core.lock:
            if results.get("kill") and not self.core.night_target:
                self.core.set_night_target(results["kill"])
                self.add_mafia_system("(무인 밤 행동 적용) 마피아의 선택이 접수되었습니다.")
            # 다수 마피아 합의: 각 마피아의 개별 지목 1:1 반영
            multi = results.get("multi", [])
            multi_pairs = results.get("multi_pairs", [])
            counts = {}
            for m_target in multi:
                counts[m_target] = counts.get(m_target, 0) + 1
            top = None; top_n = 0
            for target, n_count in counts.items():
                if n_count > top_n:
                    top, top_n = target, n_count
            if top:
                self.core.set_night_target(top)
            for m_name, m_target in multi_pairs:
                self.core.mafia_night_vote(m_name, m_target)
            # 경찰 AI 조사
            inv = results.get("invest")
            if inv:
                res = self.core.police_investigate(inv)
                pl_police = next((pl for pl in self.ai.players
                                  if pl.role == "police" and pl.alive), None)
                if res and pl_police:
                    verdict = "마피아" if res == "mafia" else "마피아가 아님"
                    pl_police.memory.append(
                        {"role": "user",
                         "content": f"[사회자 밤 비밀 통보 — 절대 채팅에 노출 금지] "
                                    f"조사 결과: {inv} = {verdict}"})
            save_t = results.get("save")
            if save_t and not self.core.night_saved:
                if self.core.doctor_protect(save_t):
                    self.add_mafia_system("(무인 밤 행동 적용) 의사의 선택이 접수되었습니다.")
                else:
                    self.add_mafia_system("(무인 밤 행동 적용) 의사의 연속 보호 시도가 규칙에 따라 제한되었습니다.")

    # ==================== AI 반응 트리거 ====================
    def _ai_threat_reaction(self, pl):
        """v1.30 — 유저 협박 발언에 대한 AI 불쾌 반응 발화(1명, 1회).
        LLM 없이 인격 톤 문구 즉결 + AI 기억에 '협박' 사실 적립(투표/찬반 참조)."""
        import random as _rr2
        try:
            me_u = getattr(self.engine, "name", "")
            lines = [
                f"갑자기 협박은 무슨 말이야? 차분히 얘기하죠, {me_u}님.",
                f"말투가 좀 심하네… 겁먹겠다. 우리 진짜 투표는 진지하게 하쟈랑요.",
                f"{me_u}님, 목에 힘 주시네요. 근거 없는 압박은 무효예요.",
                f"윽, 협박 냄새 진하게 나는데요? 이거 오히려 마피아 같은 태도야.",
                f"겁으로 말리려는 거면 됐어요. 우리 마음은 안 움직여요.",
            ]
            t = _rr2.choice(lines)
            self.add_mafia_bubble(t, pl.name)
            # AI 기억 적립 — 이후 발화/투표 토대로 사용
            if not hasattr(pl, "memory") or pl.memory is None:
                pl.memory = []
            pl.memory.append({
                "role": "user",
                "content": f"[사회자 메모] 플레이어 {me_u}님이 방금 협박 톤으로 말했습니다. "
                           f"'{pl.name}'는 이를 불쾌하게 느꼈습니다. 다음 발화에서 이 반감을 자연스럽게 참고."})
        except Exception:
            pass

    def _show_ai_typing_hint(self):
        """AI 발화 대기 표시 — 애니메이션 '입력 중' 말풍선 (직접 API라 4~6초 내 응답)."""
        who = random_mod.choice([pl.name for pl in self.ai.players
                                 if pl.alive and pl.booted] or ["AI"])
        self.add_mafia_system(f"💭 {who}님이 생각 중… (약 4~6초)")

    def _trigger_ai_reactions(self, context, min_interval=0, prefix=""):
        now = time.time()
        if now - getattr(self, "_last_ai_trigger", 0) < min_interval:
            return
        self._last_ai_trigger = now

        def factory(pl):
            alive = ", ".join(self.core.alive_players())
            return (f"{prefix}[게임 상황] 방금 이 말이 나왔습니다: {context}\n"
                    f"[참고] 현재 낮 {self.core.day_no}, 생존: {alive}\n"
                    f"'{pl.name}'로서 방금 그 말에 **직접 대답**하세요:\n"
                    f"  1) 말 건 사람의 내용을 인용하거나 질문에 답하고\n"
                    f"  2) 반박/동의/의심 중 하나로 태도를 명확히 하세요.\n"
                    f"혼잣말/게임 규칙 설명은 금지. 2문장 이내.")
        self.ai.say_async(factory)
        self._show_ai_typing_hint()
        # 사회자 실시간 한마디(3초 LLM) — 40% 확률로 개입해 티키타카 보강
        if random_mod.random() < 0.40:
            threading.Thread(target=self._host_quick_comment_bg, daemon=True,
                             kwargs={"context": context}).start()

    def _host_quick_comment_bg(self, context=""):
        text = host_llm_cached(
            "너는 마피아 게임 사회자다. 한국어로 1~2문장, 다정하고 능글맞은 톤. "
            "룰 재설명 금지. 플레이어 발언에 딱 한마디 반응만.",
            f"직전 내용: {context}. 낮 {getattr(self.core, 'day_no', 1)} 토론 중.")
        text = sanitize_player_names(clean_llm_dialect(text), list(self.core.players.keys()))
        if text:
            self.root.after(0, lambda: self.add_mafia_host(text))

    def _trigger_ai_chain_replier(self, exclude="", context=""):
        """티키타카 2막 — 직전 발화에 이어 다른 AI 1명이 반응(연쇄)."""
        if not (self.mafia_active and self.core.phase == Phase.DAY):
            return
        candidates = [pl for pl in self.ai.players
                      if pl.alive and pl.booted and pl.name != exclude]
        if not candidates:
            return
        pick = random_mod.choice(candidates)
        alive = ", ".join(self.core.alive_players())
        factory = (lambda pl: (
            f"[게임 상황] 직전에 다른 참가자가 말했습니다: {context}\n"
            f"[참고] 현재 낮 {self.core.day_no}, 생존: {alive}\n"
            f"'{pl.name}'는 그 말에 **직접 대답**하세요:\n"
            f"  1) 상대 말을 인용하거나 답하고\n"
            f"  2) 동의/반박/농담 중 태도를 명확히 하세요. "
            f"혼잣말 금지, 2문장 이내."))
        self.ai.say_one_async(pick, factory)

    # ==================== 게임 시작 ====================
    # ==================== 설정 다이얼로그 ====================
    def mafia_settings_dialog(self):
        """인간/AI 참가자 수 + AI API 설정(모던 카드형 임베드 모달)."""
        if self.mafia_active:
            self.add_mafia_system("게임 설정은 게임 시작 전(로비)에만 가능합니다.")
            return
        panel, win, close = self._make_embed_dialog("⚙ 마피아 게임 설정", 400, None, rely=0.44)
        win.configure(bg=C_CARD)
        prev_close = getattr(self, "_mafia_overlay_close", None)
        def _on_close():
            self._mafia_overlay = None
            close()
        self._mafia_overlay = panel
        self._mafia_overlay_close = _on_close

        # ── 섹션 헬퍼 ──
        def section(parent, title_text):
            tk.Label(parent, text=title_text, fg="#a78bfa", bg=C_CARD,
                     font=M_FONT_BODY_B).pack(fill="x", padx=22, pady=(14, 6))
            return tk.Frame(parent, bg="#232630", highlightthickness=1,
                            highlightbackground=C_BORDER)
        def row(parent, label):
            r = tk.Frame(parent, bg="#232630")
            r.pack(fill="x", padx=12, pady=6)
            tk.Label(r, text=label, fg="#9ca3af", bg="#232630", font=FONT_SM,
                     width=10, anchor="w").pack(side="left")
            return r
        def flat_entry(parent, width=24, show=""):
            ent = tk.Entry(parent, width=width, bg=M_INPUT_BG, fg=C_TEXT,
                           insertbackground=M_TEXT_LIGHT, relief="flat",
                           highlightthickness=1, highlightbackground="#3a4152",
                           highlightcolor=M_HOST, font=FONT_SM, show=show)
            ent.pack(side="right", fill="x", expand=True)
            return ent
        def mod_btn(parent, text, cmd, primary=False, w=10):
            bg = M_HOST if primary else M_BTN_BG
            fg = "white" if primary else "#cbd5e1"
            hb = "#8b5cf6" if primary else "#343a48"
            return emoji_render.make_pill_button(
                parent, text, cmd, bg=bg, fg=fg, hover_bg=hb,
                font_path=emoji_render.FONT_PATH_BOLD, font_size=10,
                radius=6, pad_x=14, pad_y=5, min_w=68
            )

        # ── 1. 참가자 카드 ──
        pc = section(win, "참가자 구성")
        pc.pack(fill="x", padx=18)
        conn_peers = max(0, len([p for p in self.engine.peers.values() if p.get("last", 0)]))
        r1 = row(pc, "인간 참가자")
        tk.Label(r1, text=f"{conn_peers + 1}명 (나 + 연결 {conn_peers}명)", fg=M_TEXT_LIGHT,
                 bg="#232630", font=FONT_SM).pack(side="right")
        # v1.13 — 사람 초대 안내(문서 표준)
        tk.Label(pc, text="사람 초대: 같은 사무실 네트워크에 있는 상대는 프로그램 실행 시 "
                          "자동 연결됩니다. 다른 네트워크의 상대는 상단 ⚙ 대화방 설정 → "
                          "'상대 연결' 창에서 IP(IP:포트)를 등록하세요.",
                 fg="#9ca3af", bg="#232630", font=FONT_SM,
                 wraplength=320, justify="left").pack(fill="x", padx=26, pady=(0, 4))
        r2 = row(pc, "AI 참가자")
        ai_var = tk.IntVar(value=getattr(self, "mafia_ai_count", 4))
        stepper = tk.Frame(r2, bg="#232630"); stepper.pack(side="right")
        # v1.25 — AI 인원 상한: 인격 풀(ALL_PERSONAS 25인)과 전체 10인(MAX_PLAYERS)
        # 기준. 연결 인간이 늘어나면 AI 상한은 자동으로 줄어든다.
        # v1.56 — 상한을 다이얼로그 오픈 시점에 한 번만 계산해두면, 창을 열어둔 채
        # 다른 사람이 참가 신청을 해도 반영이 안 돼 인원을 초과 배정할 수 있었다.
        # 클릭할 때마다 현재 연결 인원 기준으로 다시 계산.
        def _ai_cap_now():
            conn_now = max(0, len([p for p in self.engine.peers.values() if p.get("last", 0)]))
            return min(len(ALL_PERSONAS), MAX_PLAYERS - 1 - conn_now)
        def _bump(d):
            v = min(_ai_cap_now(), max(1, ai_var.get() + d))
            ai_var.set(v); lbl_v.config(text=str(v))
        # v1.56 — 라벨 3개를 만든 뒤 winfo_children() 순서(인덱스 0/2)로 클릭을
        # 재배정하던 방식은 위젯 생성 순서가 바뀌면 조용히 깨지는 취약한 구조였다.
        # 진짜 tk.Button + 앱 공용 호버 효과(_hover)로 바꿔 다른 버튼들과 스타일도 통일.
        minus_btn = tk.Button(stepper, text="−", command=lambda: _bump(-1),
                              bg=M_INPUT_BG, fg="#9ca3af", activebackground=M_BTN_BG,
                              activeforeground="white", relief="flat", bd=0,
                              highlightthickness=0, font=M_FONT_EMPH_B,
                              width=3, cursor="hand2")
        minus_btn.pack(side="left")
        self._hover(minus_btn, M_INPUT_BG, M_BTN_BG)
        lbl_v = tk.Label(stepper, text=str(ai_var.get()), fg="white", bg="#111319",
                         font=M_FONT_EMPH_B, width=4)
        lbl_v.pack(side="left", padx=2)
        plus_btn = tk.Button(stepper, text="+", command=lambda: _bump(+1),
                             bg=M_INPUT_BG, fg="#9ca3af", activebackground=M_BTN_BG,
                             activeforeground="white", relief="flat", bd=0,
                             highlightthickness=0, font=M_FONT_EMPH_B,
                             width=3, cursor="hand2")
        plus_btn.pack(side="left")
        self._hover(plus_btn, M_INPUT_BG, M_BTN_BG)
        tk.Label(pc, text="(부트 시간이 길어 3~4명 권장)", fg="#565d6b", bg="#232630",
                 font=FONT_XS_PAD).pack(fill="x", padx=12, pady=(0, 8))

        # ── 2. AI API 카드 ──
        from mafia_config import LLM_BASE_URL as DEF_URL, LLM_MODEL as DEF_MODEL, RUNTIME_OVERRIDES
        apic = section(win, "AI API (비워두면 기본값)")
        apic.pack(fill="x", padx=18)
        # v1.55 — 기본값을 입력창에 아예 채우지 않는다(회색 placeholder였던
        # v1.45도 "채워져 있는 것처럼" 보여 혼동을 준다는 지적) — 저장된 값이
        # 있을 때만 입력창을 채우고, 없으면 완전히 비워둔 채 기본값은 아래
        # 작은 안내 라벨로만 알려준다.
        def _saved_or_empty_entry(parent, saved_value):
            ent = flat_entry(parent, width=24)
            if saved_value:
                ent.insert(0, saved_value)
            return ent

        r3 = row(apic, "서버 URL")
        url_ent = _saved_or_empty_entry(r3, RUNTIME_OVERRIDES.get("base_url"))
        tk.Label(apic, text=(f"(비워두면 기본값 사용: {DEF_URL})" if DEF_URL else "(서버 URL을 입력해야 AI가 발언합니다)") if not RUNTIME_OVERRIDES.get("base_url")
                 else "✔ 저장된 값을 사용 중입니다.",
                 fg="#565d6b" if not RUNTIME_OVERRIDES.get("base_url") else "#22c55e",
                 bg="#232630", font=FONT_XS_PAD).pack(fill="x", padx=12)
        r4 = row(apic, "모델명")
        mod_ent = _saved_or_empty_entry(r4, RUNTIME_OVERRIDES.get("model"))
        tk.Label(apic, text=f"(비워두면 기본값 사용: {DEF_MODEL})" if not RUNTIME_OVERRIDES.get("model")
                 else "✔ 저장된 값을 사용 중입니다.",
                 fg="#565d6b" if not RUNTIME_OVERRIDES.get("model") else "#22c55e",
                 bg="#232630", font=FONT_XS_PAD).pack(fill="x", padx=12)
        r5 = row(apic, "API Key")
        key_ent = flat_entry(r5, width=24, show="*")
        saved_key = RUNTIME_OVERRIDES.get("api_key") or ""
        if saved_key:
            # v1.14 — 기존 저장 표시(마스킹). 유저가 재입력 없이도 '저장됨'을 확인
            mask_str = (saved_key[:4] + "…" + saved_key[-3:]) if len(saved_key) > 8 else "•••••"
            key_ent.insert(0, mask_str)
            key_ent._mafia_key_placeholder = True
            # 값 placeholder — 유저가 탭하면 자동 지워짐
            def _clear_placeholder(ev=None):
                if getattr(key_ent, "_mafia_key_placeholder", False):
                    key_ent.delete(0, "end")
                    key_ent._mafia_key_placeholder = False
            key_ent.bind("<FocusIn>", _clear_placeholder)
            _clear_placeholder.tag = True
        key_saved_lbl = tk.Label(apic, fg="#22c55e", bg="#232630", font=FONT_XS_PAD)
        key_saved_lbl.pack(fill="x", padx=12)
        if saved_key:
            key_saved_lbl.config(text="✔ 기존 키가 저장되어 있습니다 — 비워두면 유지됩니다.")
        else:
            key_saved_lbl.config(text="(환경변수로 키를 쓰고 있으면 비워둬도 됩니다)", fg="#565d6b")

        # ── 하단 버튼 바 ──
        bar = tk.Frame(win, bg=C_CARD)
        bar.pack(fill="x", padx=18, pady=(14, 0))
        def _apply():
            # v1.56 — 저장 시점에도 다시 한 번 상한으로 clamp(값 자체는 안 건드렸어도
            # 다이얼로그가 열려있는 동안 다른 사람이 참가해 상한이 줄었을 수 있음).
            self.mafia_ai_count = min(_ai_cap_now(), max(1, int(ai_var.get())))
            import mafia_config
            # v1.55 — 입력창이 비어 있으면(기본값 텍스트를 아예 안 채워두므로,
            # 비어 있다는 건 사용자가 정말 입력을 안 했다는 뜻) 저장하지 않고
            # 기존 값(또는 기본값)을 그대로 유지.
            if url_ent.get().strip():
                mafia_config.RUNTIME_OVERRIDES["base_url"] = url_ent.get().strip()
            if mod_ent.get().strip():
                mafia_config.RUNTIME_OVERRIDES["model"] = mod_ent.get().strip()
            key_raw = key_ent.get().strip()
            # placeholder 마스크('aaaa…xyz')는 새 키 아님 — 저장 생략
            is_placeholder = (getattr(key_ent, "_mafia_key_placeholder", False) or "…" in key_raw)
            if key_raw and not is_placeholder:
                mafia_config.RUNTIME_OVERRIDES["api_key"] = key_raw
            # --- v1.05: 설정 파일로 저장 — 프로그램 재시작 후에도 유지 ---
            if mafia_config.save_overrides():
                self.add_mafia_system("⚙ 게임 설정 파일 저장 완료 (재시작 후에도 유지)")
            else:
                self.add_mafia_system("⚠ 게임 설정 파일을 저장하지 못했습니다 — 이번 실행 동안만 적용됩니다.")
            self.add_mafia_system(f"⚙ 게임 설정 저장 — AI {self.mafia_ai_count}명 / 인간 {conn_peers + 1}명")
            # v1.45 — 실제로 무엇이 저장됐는지 명확히 보여줘 '저장이 안 되는 것
            # 같다'는 혼동을 없앤다.
            cur_url = mafia_config.RUNTIME_OVERRIDES.get("base_url") or DEF_URL
            cur_model = mafia_config.RUNTIME_OVERRIDES.get("model") or DEF_MODEL
            self.add_mafia_system(f"🌐 AI API 서버: {cur_url} / 모델: {cur_model}")
            key_raw2 = key_ent.get().strip()
            if key_raw2 and not ("…" in key_raw2 or getattr(key_ent, "_mafia_key_placeholder", False)):
                self.add_mafia_system("🔑 AI API 키 등록 완료 (저장됨)")
            _on_close()
        # v1.57 — padx=(6,0)이 취소 버튼의 '왼쪽'에 여백을 줘서, 정작 저장/취소
        # 버튼 사이(취소의 오른쪽)는 0px로 서로 붙어 보이는 버그였다(실측 지적).
        # dialogs.py의 확인/취소 쌍 관례(padx=(0, N))와 동일하게 맞춤.
        save_btn = mod_btn(bar, "저장", _apply, primary=True, w=9)
        save_btn.pack(side="right")               # 맨 오른쪽
        close_btn = mod_btn(bar, "취소", _on_close, primary=False, w=9)
        close_btn.pack(side="right", padx=(0, 8))  # 저장과의 사이에 여백
        tk.Frame(win, bg=C_CARD, height=10).pack()

    def mafia_start_clicked(self):
        if self.mafia_active:
            self.add_mafia_system("게임이 이미 진행 중입니다.")
            return
        if not getattr(self, "_recruiting", False):
            # 1단계: 참가자 모집 시작
            self._start_recruitment()
            return
        # 2단계: 모집 완료 후 게임 시작
        self._launch_game_with_recruits()

    def _start_recruitment(self):
        self._recruiting = True
        me = getattr(self.engine, "name", None) or "방장"
        self._recruiter_host = me
        self._recruited_humans = [me]
        self._my_joined = True

        self.mafia_start_btn.config(
            text="🎮 게임 시작 (인간 1명)",
            bg="#16a34a", activebackground="#15803d"
        )
        if hasattr(self, "mafia_cancel_recruit_btn"):
            self.mafia_cancel_recruit_btn.pack(side="right", padx=(6, 6), pady=8)
        self.mafia_phase_lbl.config(text="📢 참가자 모집 중…")

        self.add_mafia_system(
            f"📢 [마피아 참가자 모집] 방장 '{me}' 님이 게임 참가자를 모집합니다!\n"
            f"👉 참여를 원하시는 분은 상단 [🙋 참가 신청] 버튼을 누르거나 채팅창에 '/참가'를 입력하세요.\n"
            f"현재 참가자(1명): {me}(방장)"
        )
        self._mafia_broadcast("recruit_start", host=me, players=self._recruited_humans)

    def mafia_cancel_recruit_clicked(self):
        me = getattr(self.engine, "name", None)
        self._recruiting = False
        self._recruited_humans = []
        self._recruiter_host = None
        self._my_joined = False

        self.mafia_start_btn.config(
            text="📢 참가자 모집",
            bg="#b91c1c", activebackground="#7f1d1d", state="normal"
        )
        if hasattr(self, "mafia_cancel_recruit_btn"):
            self.mafia_cancel_recruit_btn.pack_forget()
        self.mafia_phase_lbl.config(text="")
        self.add_mafia_system("📢 마피아 게임 참가자 모집이 취소되었습니다.")
        self._mafia_broadcast("recruit_cancel", host=me)

    def mafia_toggle_join_clicked(self):
        me = getattr(self.engine, "name", None)
        if not me or not getattr(self, "_recruiting", False):
            return
        if not getattr(self, "_my_joined", False):
            self._my_joined = True
            if hasattr(self, "mafia_join_btn"):
                self.mafia_join_btn.config(
                    text="✋ 참가 취소", bg="#dc2626", activebackground="#b91c1c"
                )
            self.add_mafia_system("🙋 마피아 게임 참가 신청을 완료했습니다!")
            self._mafia_broadcast("recruit_join", name=me)
        else:
            self._my_joined = False
            if hasattr(self, "mafia_join_btn"):
                self.mafia_join_btn.config(
                    text="🙋 참가 신청", bg="#059669", activebackground="#047857"
                )
            self.add_mafia_system("✋ 마피아 게임 참가를 취소했습니다.")
            self._mafia_broadcast("recruit_leave", name=me)

    def _handle_chat_join(self):
        me = getattr(self.engine, "name", None)
        if getattr(self, "_recruiting", False):
            if getattr(self, "_recruiter_host", None) == me:
                self.add_mafia_system("이미 방장으로서 참가 명단에 포함되어 있습니다.")
            else:
                if not getattr(self, "_my_joined", False):
                    self.mafia_toggle_join_clicked()
                else:
                    self.add_mafia_system("이미 참가 신청 상태입니다. 취소하려면 '/취소'를 입력하세요.")

    def _handle_chat_leave(self):
        me = getattr(self.engine, "name", None)
        if getattr(self, "_recruiting", False):
            if getattr(self, "_recruiter_host", None) == me:
                self.mafia_cancel_recruit_clicked()
            else:
                if getattr(self, "_my_joined", False):
                    self.mafia_toggle_join_clicked()
                else:
                    self.add_mafia_system("참가 신청 상태가 아닙니다. 참가하려면 '/참가'를 입력하세요.")

    def _launch_game_with_recruits(self):
        self._recruiting = False
        if hasattr(self, "mafia_cancel_recruit_btn"):
            self.mafia_cancel_recruit_btn.pack_forget()
        if hasattr(self, "mafia_join_btn"):
            self.mafia_join_btn.pack_forget()

        me = getattr(self.engine, "name", None) or "나"
        humans = list(getattr(self, "_recruited_humans", []))
        if me not in humans:
            humans.insert(0, me)

        with self.core.lock:
            self.core.lobby_reset()
            # 오직 참가 신청한 실제 인간들만 join! (강제 납치 제거)
            for nm in humans:
                self.core.join(nm, is_ai=False)

            ai_count = max(1, getattr(self, "mafia_ai_count", 4))
            need = max(ai_count, MIN_PLAYERS - len(self.core.players))
            need = min(need, len(ALL_PERSONAS))
            chosen_personas = ALL_PERSONAS[:need]
            self._session_ai_personas = chosen_personas
            for p in chosen_personas:
                self.core.join(p["name"], is_ai=True)

        self.mafia_active = True
        self.mafia_host_mode = True
        self.mafia_start_btn.configure(text="[게임 진행 중]", state="disabled")
        self._mafia_maximize_window()   # v1.49 — 게임 시작 시 창을 1400x800으로 설정
        self.add_mafia_system(
            f"🎲 게임 시작 — 확정 참가자({len(self.core.players)}명): " + ", ".join(self.core.players.keys()))

        ok, assigned = self.core.start_game()
        if not ok:
            self.add_mafia_system(f"게임 시작 실패 — 참가자 {MIN_PLAYERS}명 미만")
            self.mafia_start_btn.configure(text="📢 참가자 모집", state="normal")
            self.mafia_active = False
            return

        self._mafia_disconnected = set()   # v1.42 — 새 판 시작, 접속 상태 추적 초기화
        self._mafia_start_disconnect_watch()

        self.ai.assign_roles(assigned)
        role_names = ROLE_LABEL_KR
        # v1.61 — 명단(roster) 브로드캐스트를 역할 개인 쪽지보다 먼저 보낸다.
        # 순서가 반대였을 때는 원격 참가자의 core.players가 아직 비어있는
        # 상태에서 "hdm" 역할 통보가 먼저 도착해, 그 안에서 하던
        # `core.players[me]["role"] = role` 반영이 조용히 무시되고 있었다
        # (실측 지적 — 복수 인간 플레이 전수 검토).
        self._mafia_broadcast("start", players=[
            {"name": n, "is_ai": p.get("is_ai", False)} for n, p in self.core.players.items()
        ])
        # 사회자가 '개인 쪽지'로 전원에게 직업 통보 — P2P DM(리모트) / 로컬 host_dm(나)
        for pname, prole in assigned.items():
            if pname not in self.core.players:
                continue
            if self.core.players[pname].get("is_ai"):
                continue
            rn = role_names.get(prole, "알 수 없음")
            msg = f"🃏 {pname}님, 당신의 직업은 '{rn}'입니다. 다른 사람에게 알리지 마세요."
            if pname == self.engine.name:
                self.add_mafia_host_dm(msg)
                self._my_mafia_role = prole
                self.root.after(100, lambda r=prole: self._show_role_popup(r))
            else:
                # v1.61 — 마피아에게만 동료 명단을 함께 보낸다(다른 직업에겐 절대 안 감)
                mates = [n for n, r in assigned.items() if r == "mafia" and n != pname]                     if prole == "mafia" else None
                if mates:
                    self._mafia_send_private(pname, "hdm", target=pname, text=msg, role=prole, mates=mates)
                else:
                    self._mafia_send_private(pname, "hdm", target=pname, text=msg, role=prole)
        self.add_mafia_system("(AI 참가자가 순차 입장합니다…)")
        threading.Thread(target=self._host_then_bootstrap_bg, daemon=True).start()

    def _host_then_bootstrap_bg(self):
        """(사회자 즉시 개회) → AI 부트(느림, 백그라운드) → 완료 알림."""
        self._host_opening_now()
        players_desc = ", ".join(self.core.players.keys())
        need = max(1, getattr(self, "mafia_ai_count", 4))
        try:
            with open(os.path.join(os.environ["LOCALAPPDATA"], "Temp",
                                   "mafia_boot_trace.txt"), "a", encoding="utf-8") as tf:
                tf.write("spawn-all-start\n")
        except Exception:
            pass
        ai_names = [n for n, p in self.core.players.items() if p.get("is_ai")]
        personas_to_spawn = getattr(self, "_session_ai_personas", None) or ALL_PERSONAS[:len(ai_names)]
        oks = self.ai.spawn_all(personas_to_spawn, self.engine.name,
                                players_desc, names=ai_names)
        # AI 스폰 완료 후 core의 역할 정보를 AI 객체들에게 배정!
        self.ai.assign_roles({n: p["role"] for n, p in self.core.players.items()})
        try:
            with open(os.path.join(os.environ["LOCALAPPDATA"], "Temp",
                                   "mafia_boot_trace.txt"), "a", encoding="utf-8") as tf:
                tf.write(f"spawn-all-done {oks}\n")
        except Exception:
            pass
        fail = [n for n, ok in oks if not ok]
        if fail:
            self.root.after(0, lambda: self.add_mafia_system(
                f"⚠ AI 부트 실패: {', '.join(fail)} — 해당 AI는 관전만 합니다."))


    def _host_opening_now(self):
        try:
            with open(os.path.join(os.environ["LOCALAPPDATA"], "Temp",
                                   "mafia_boot_trace.txt"), "a", encoding="utf-8") as tf:
                tf.write("opening-start\n")
        except Exception:
            pass
        text = host_llm_cached(
            "너는 마피아 게임 사회자다. 한국어로 2문장 이내, 경쾌하고 담백한 톤.\n"
            "절대 다른 주제로 샘지 마시오. **개회 선언만** 하세요. 룰 설명 금지.\n"
            "일상 회원 탈퇴 같은 말 절대 금지. 마피아 게임 개회사만!",
            f"참가자: {', '.join(self.core.players.keys())}. 이 명단에 '없는' 사람 이름을 "
            "만들어 말하지 마시오(환각 금지 — 명단에 있는 이름만 말할 것). 1일차 낮 개회 선언.")
        text = clean_llm_dialect(text) or "1일차 낮이 되었습니다. 서로를 관찰하며 의심스러운 사람을 찾아보세요."
        text = sanitize_player_names(text, list(self.core.players.keys()))
        # Tk 객체는 메인 스레드에서만 조작 — after로 메인스레드에 넘김
        self.root.after(0, lambda: self.add_mafia_host(text))
        self.root.after(30, lambda: self.finish_host_opening())

    def finish_host_opening(self):
        if not self.mafia_active:
            return
        self.start_day_timer()
        self._trigger_ai_reactions(
            context="사회자가 개회를 선언했습니다. 자기소개 격 한마디.", min_interval=0)

    # ==================== 타이머/투표/밤/종료 ====================
    def _kick_silent_room(self):
        """v1.12 — 25초 침묵 시 AI 자율 발화 킥스타트. 유저 침묵 게임 정지 방지."""
        if not (self.mafia_active and self.core.phase == Phase.DAY):
            return
        alive = ", ".join(self.core.alive_players())
        import random as _rr
        suspect = [n for n in self.core.alive_players()
                   if n != getattr(self.engine, "name", None)]
        focus = _rr.choice(suspect) if suspect else ""
        context = (f"아무도 말하지 않아 분위기가 잠잠합니다. "
                   f"'{focus or '살아있는 사람'}' 참가자가 조금 수상해 보였습니다.")
        def factory(pl):
            return (f"[침묵 킥스타트] 아무도 말하지 않으니 당신('{pl.name}')이 먼저 "
                    f"입을 열어 주세요. 가볍게 누구를 의심해볼지 물어보거나, "
                    f"방 분위기에 대해 짧게 던지세요. "
                    f"2문장 이내, 혼잣말/게임 규칙 설명 금지.")
        # 모든 살아있는 AI에 발화 — freq 제한 해제(honor_freq=False)로 1~2명 강제 응답
        self.ai.say_async(factory, honor_freq=False)

    def start_day_timer(self):
        self._cancel_mafia_timer()
        self._last_any_talk_ts = time.time()   # v1.12 — 침묵 감지 기준점
        self._day_deadline = time.time() + DAY_CYCLE_SECONDS
        self._mafia_timer = self.root.after(
            int(DAY_CYCLE_SECONDS * 1000), self._on_day_timeout)
        # v1.40 — 토론 중 조기 자동투표 제거: 실제 투표는 개표 팝업에서만 집계한다
        # (유저보다 AI가 먼저 투표를 확정해버리는 문제의 원인이었음)
        self._day_tick_loop()

    def _day_tick_loop(self):
        """투표 카운트다운 — 1초 틱으로 게임바 라벨에 남은 시간 표시."""
        if not (self.mafia_active and self.core.phase == Phase.DAY):
            return
        remain = int(getattr(self, "_day_deadline", time.time()) - time.time())
        if remain <= 0:
            return
        m, s = divmod(remain, 60)
        try:
            self.mafia_phase_lbl.config(
                text=f"☀ 낮 {self.core.day_no} · 토론 중 — 남은 {m}:{s:02d} (자유롭게 토론하세요)")
            if getattr(self, "current", None) and self.current[0] == "mgame":
                self.ch_sub.config(text=f"토론 중 — 개표까지 {m}:{s:02d}", fg="#c4b5fd")
        except Exception:
            pass
        # v1.12 — 유저/AI 침묵 25초 경과 시 자율 토론 킥스타트(게임 정지 방지)
        if self._mafia_is_host() and remain > 3 and (time.time() - getattr(self, "_last_any_talk_ts", time.time())) >= 25:
            self._last_any_talk_ts = time.time()
            self._kick_silent_room()
        self._day_tick = self.root.after(1000, self._day_tick_loop)

    # ==================== 접속 끊김 감지 (v1.42) ====================
    def _mafia_start_disconnect_watch(self):
        """게임 시작 시 1회 호출 — 이후 mafia_active인 동안 스스로 재예약되며 계속 돈다."""
        self._mafia_poll_disconnects()

    def _mafia_poll_disconnects(self):
        """실제 인간 참가자(LAN 상대)의 접속 상태를 주기적으로 확인해 끊김/재접속을 알린다.
        밤 행동·투표는 어차피 기존 타이머로 계속 진행되므로(멈추지 않음), 여기선
        '왜 조용한지/왜 아무도 안 죽었는지' 알 수 있게 알림만 준다 — 역할 정보는 노출 안 함."""
        if not self.mafia_active:
            return
        try:
            me = getattr(self.engine, "name", None)
            known = getattr(self, "_mafia_disconnected", None)
            if known is None:
                known = self._mafia_disconnected = set()
            for name, p in list(self.core.players.items()):
                if p.get("is_ai") or name == me:
                    continue
                peer_key = self._mafia_peer_of(name)
                online = False
                if peer_key:
                    info = self.engine.get_peer(peer_key[0], peer_key[1])
                    if info and (time.time() - info.get("last", 0)) < PEER_TIMEOUT:
                        online = True
                was_disconnected = name in known
                if not online and not was_disconnected:
                    known.add(name)
                    self.add_mafia_system(
                        f"⚠ {name}님과의 연결이 끊긴 것 같습니다(응답 없음) — "
                        "투표·밤 시간은 그대로 진행되며, 재접속하면 자동으로 다시 인식됩니다.")
                    # v1.61 — 끊긴 채로 두면 그 사람 표를 기다리느라 매 페이즈
                    # 최대 타임아웃(15~30초)까지 게임이 멈추고, 승패 판정에서도
                    # 계속 생존자로 잘못 집계됐다(복수 인간 플레이 전수 검토
                    # 지적) — 사망 처리해서 즉시 제외한다.
                    if p.get("alive", True):
                        p["alive"] = False
                        self.add_mafia_system(f"💀 {name}님이 접속 끊김으로 게임에서 제외되었습니다(사망 처리).")
                        self._mafia_broadcast("death", name=name)
                        winner = self.core.check_winner()
                        if winner:
                            self._on_game_end(winner)
                            return
                elif online and was_disconnected:
                    known.discard(name)
                    self.add_mafia_system(f"✅ {name}님이 다시 연결되었습니다.")
        except Exception:
            pass
        self._disconnect_watch_timer = self.root.after(4000, self._mafia_poll_disconnects)

    def _cancel_mafia_timer(self):
        for attr in ("_mafia_timer", "_day_tick", "_night_tick", "_ai_vote_timer",
                     "_force_tally_timer", "_revote_deadline", "_defense_deadline",
                     "_defense_end_timer", "_defense_fallback_timer", "_defense_popup10",
                     "_tick_vote", "_defense_vote_tick", "_night_pick_tick"):
            t = getattr(self, attr, None)
            if t:
                try:
                    self.root.after_cancel(t)
                except Exception:
                    pass
                setattr(self, attr, None)

    # ==================== AI 투표 ====================
    # v1.40 — 토론 중 '투표 X' 조기 자동투표/파싱 로직 제거.
    # 실제 투표는 개표 팝업(_show_vote_popup)에서만 집계되며, 유저가
    # 먼저 투표하거나 유예시간이 지난 뒤에야 AI 표가 반영된다.

    def _cancel_tally_safety_timers(self):
        """투표 집계 관련 안전망 지연 타이머 전원 취소."""
        ft = getattr(self, "_force_tally_timer", None)
        if ft:
            try:
                self.root.after_cancel(ft)
            except Exception:
                pass
            self._force_tally_timer = None
        pvc = getattr(self, "_pending_vote_close", None)
        if pvc:
            try:
                self.root.after_cancel(pvc)
            except Exception:
                pass
            self._pending_vote_close = None

    def _schedule_tally(self, delay_ms=300):
        """v1.34: 개표 중복 호출 및 사회자 최후변론 멘트 이중 출력 방지 단일 스케줄러."""
        if not self._mafia_is_host():
            # v1.61 — 개표는 호스트 전용 권한. 원격 참가자는 시간이 다 되면 자기
            # 투표 팝업만 정리한다(불완전한 로컬 core로 임의 개표 방지).
            try:
                self._cancel_vote_popup()
            except Exception:
                pass
            return
        if getattr(self, "_tally_scheduled", False) or getattr(self, "_tally_in_progress", False):
            return
        self._tally_scheduled = True
        self._cancel_mafia_timer()
        self._cancel_tally_safety_timers()
        try:
            self._cancel_vote_popup()
        except Exception:
            pass
        if delay_ms <= 0:
            self._tally_and_reveal()
        else:
            self.root.after(delay_ms, self._tally_and_reveal)

    def _on_day_timeout(self):
        self._mafia_timer = None
        if not self.mafia_active or self.core.phase != Phase.DAY:
            return
        self.add_mafia_system("⏰ 낮 시간 종료 — 개표합니다.")
        self.open_the_vote()

    def open_the_vote(self):
        self._tally_scheduled = False
        self._tally_in_progress = False
        self._defense_in_progress = False
        self._cancel_mafia_timer()
        self._cancel_tally_safety_timers()
        # v1.61 — 원격 참가자 화면에도 투표 팝업이 뜨도록 개시를 알린다
        if self._mafia_is_host():
            self._mafia_broadcast("vote_open")
        # 개표 전 12초 카운트다운 팝업 — 각자 팝업에서 대상 클릭 투표
        self._show_vote_popup()

    # ==================== 투표 팝업 ====================
    def _grid_candidates_centered(self, frame, names, make_btn, padx=3, pady=3):
        """v1.57 — 후보 버튼을 3열 그리드로 배치. 인원수가 3의 배수가 아니면
        마지막 줄이 왼쪽으로 붕 떠 보이던 문제(실측 지적) — 꽉 찬 줄까지는
        기존처럼 그리드로 늘려 채우고, 남는 마지막 줄만 별도 프레임으로 빼서
        가운데 정렬한다.
        make_btn(parent, name) -> 아직 grid/pack 되지 않은 Button.
        반환: {name: widget}."""
        n = len(names)
        full_rows = n // 3
        remainder = n % 3
        widgets = {}
        for i in range(full_rows * 3):
            name = names[i]
            b = make_btn(frame, name)
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=padx, pady=pady)
            widgets[name] = b
        for c in range(3):
            frame.columnconfigure(c, weight=1)
        if remainder:
            last_row = tk.Frame(frame, bg=frame.cget("bg"))
            last_row.grid(row=full_rows, column=0, columnspan=3, pady=pady)
            for name in names[full_rows * 3:]:
                b = make_btn(last_row, name)
                b.pack(side="left", padx=padx)
                widgets[name] = b
        return widgets

    def _show_vote_popup(self):
        if not self.mafia_active:
            return
        self._vote_popup_open_ts = time.time()   # v1.40 — 유저 우선 유예시간 기준점
        # v1.13/v1.34 — 사망자는 투표 팝업 자체가 열리지 않게(유령방 안내로 대체), AI 투표는 정상 진행
        me_check = getattr(self.engine, "name", None)
        if me_check and not (self.core.players.get(me_check) or {}).get("alive", True):
            self.add_mafia_system("👻 사망자는 투표할 수 없습니다 — AI 투표 참관 모드로 진행됩니다")
            self._open_ghost_chat()
            # AI 전원 투표 접수 — 사망자 관전 모드에서도 AI끼리 투표 진행
            alive_cands = self.core.alive_players()
            ai_players = [pl for pl in getattr(self, "ai", None) and self.ai.players or []
                          if pl.alive and pl.booted and pl.name in alive_cands]
            for idx, ai in enumerate(ai_players):
                self.root.after(300 + idx * 350, lambda a=ai: self._ai_vote_in_popup(a))
            self._force_tally_timer = self.root.after(15000, self._silent_tally_if_pending)
            return
        alive = [n for n in self.core.alive_players()
                 if n != getattr(self.engine, "name", None)]   # 자투 방지 — 나는 후보 제외
        body = self._mafia_overlay_open("🗳 투표", w=396, h=None)
        self._vote_remaining = VOTE_WINDOW   # 15초 카운트다운
        head = tk.Frame(body, bg=C_CARD)
        head.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(head, text="지목할 대상을 선택하세요", fg="#a78bfa", bg=C_CARD,
                 font=M_FONT_BODY_B).pack(anchor="w")
        lbl = tk.Label(body, text="", fg=M_TEXT_LIGHT, bg=C_CARD,
                       font=(FONT_FAM, 14, "bold"))
        lbl.pack(pady=(2, 4))
        prog_lbl = tk.Label(body, text="", fg="#a78bfa", bg=C_CARD, font=M_FONT_HELP)
        prog_lbl.pack(pady=(0, 8))
        emoji_render.apply(prog_lbl, M_FONT_HELP)
        self._vote_progress_lbl = prog_lbl
        frame = tk.Frame(body, bg=C_CARD)
        frame.pack(fill="x", padx=18, pady=(0, 8))
        self._user_pending_notified = False   # v1.21 — '유저 표 대기' 안내 1회만

        def _mk_vote_btn(parent, n):
            return emoji_render.make_pill_button(
                parent, n, lambda nn=n: self._popup_vote(nn),
                bg=M_BTN_BG, fg=M_TEXT_LIGHT, hover_bg="#374151",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=10,
                radius=8, pad_x=10, pad_y=6, min_w=96
            )
        self._vote_btns = self._grid_candidates_centered(frame, alive, _mk_vote_btn)
        # v1.09 — 기권 버튼(문서 '기권 허용' 표준)
        ab = emoji_render.make_pill_button(
            body, "기권", lambda: self._popup_vote(None),
            bg="#111827", fg="#9ca3af", hover_bg="#374151",
            font_path=emoji_render.FONT_PATH_REGULAR, font_size=9,
            radius=6, pad_x=14, pad_y=4
        )
        ab.pack(pady=(0, 10))
        self._vote_abstain_btn = ab
        self._vote_lbl = lbl
        self._refresh_vote_progress_label()
        self._tick_vote = self.root.after(1000, self._vote_popup_tick)
        self._vote_popup_tick()
        # AI 전원 실측 투표 — LLM으로 '투표 이름' 물어 core에 반영, 팝업에 태그 표시
        ai_players = [pl for pl in getattr(self, "ai", None) and self.ai.players or []
                      if pl.alive and pl.booted and pl.name in alive]
        for idx, ai in enumerate(ai_players):
            # v1.15 — AI 표 접수 대기 단축(1.5초 → 첫 AI 300ms, 이후 350ms 간격)
            self.root.after(300 + idx * 350, lambda ai=ai: self._ai_vote_in_popup(ai))

    def _vote_progress_counts(self):
        """v1.42 — 익명 투표 진행률: 몇 명이 '완료'했는지만(누구에게인지는 절대 없이) 집계."""
        core = getattr(self, "core", None)
        if not core:
            return 0, 0
        alive = core.alive_players()
        done = sum(1 for n in alive if n in core.votes or n in core.abstains)
        return done, len(alive)

    def _vote_progress_text(self):
        done, total = self._vote_progress_counts()
        return f"🗳 투표 진행률 {done}/{total}"

    def _defense_progress_text(self):
        """v1.47 — 최후 변론 찬반 투표 진행률(익명 — 누가 찬성/반대인지는 노출 안 함)."""
        core = getattr(self, "core", None)
        if not core:
            return "진행률 0/0"
        defendant = getattr(core, "defendant", None)
        voters = [n for n in core.alive_players() if n != defendant]
        done = sum(1 for n in voters if n in getattr(core, "defense_yes", {}))
        return f"진행률 {done}/{len(voters)}"

    def _refresh_vote_progress_label(self):
        """열려있는 투표/재투표 팝업의 진행률 라벨을 즉시 갱신(표가 들어올 때마다 호출)."""
        lbl = getattr(self, "_vote_progress_lbl", None)
        if lbl is None:
            return
        try:
            lbl.config(text=self._vote_progress_text())
        except Exception:
            pass

    def _ai_vote_user_should_wait(self):
        """v1.40 — 유저 우선: 유저가 아직 투표 전이고 유예시간(5초) 안 지났으면 True(AI는 대기)."""
        me_name = getattr(self.engine, "name", None)
        if not me_name or not getattr(self, "core", None):
            return False
        if not (self.core.players.get(me_name) or {}).get("alive", True):
            return False   # 유저 사망 — 관전 모드, 대기 불필요
        if me_name in self.core.votes or me_name in self.core.abstains:
            return False   # 유저가 이미 투표함
        opened = getattr(self, "_vote_popup_open_ts", 0)
        return (time.time() - opened) < 5.0

    def _ai_vote_in_popup(self, ag):
        import random as _r
        # v1.15 — 팝업이 이미 닫혀도(유저가 먼저 투표해 overlay close) AI 표는
        # 계속 접수해야 한다. 기존 'btns 없으면 return'이 집계 정지의 진원지.
        if not self.mafia_active:
            return
        if getattr(self, "core", None) and self.core.phase not in (Phase.DAY, Phase.VOTE):
            return
        if getattr(self, "core", None) and ag.name in self.core.votes:   # 이미 표 낸 AI 스킵
            return

        def _bg_vote_worker():
            target = None
            try:
                alive = self.core.alive_players() if getattr(self, "core", None) else []
                alive = [n for n in alive if n != ag.name]
                prompt = (
                    f"[투표] 누구에게 투표할까요? 생존 후보: {', '.join(alive)}. "
                    f"답은 오직 '투표 이름' 한 줄.")
                target_text = (ag.say(prompt) or "").strip()
                m = re.search(r"투표\s*([^\s]+)", target_text)
                target = m.group(1) if m else target_text
                # 후보 목록에 정확 있으면 승인 (LLM이 '투표 이름' 형식 생략해도 인정)
                if target not in alive:
                    m2 = re.search(r"([\w가-힣]+)", target_text)
                    cand = m2.group(1) if m2 else None
                    if cand in alive:
                        target = cand
                    else:
                        target = None
            except Exception:
                target = None

            if not target:
                alive = self.core.alive_players() if getattr(self, "core", None) else []
                alive = [n for n in alive if n != ag.name]
                target = random_mod.choice(alive) if alive else None

            # v1.30 — 유저 발화 톤 반영(낮 본투표): 유저가 최근 발화에서 협박하면
            # AI들이 유저(협박자)를 지목할 확률 상승(협박 불이익). 설득이면 소폭 되려 감소.
            if target is not None:
                tone, _tone_txt = self._barometer_last_user_tone()
                me_u = getattr(self.engine, "name", None)
                alive = self.core.alive_players() if getattr(self, "core", None) else []
                if tone == "threat" and me_u and me_u in alive:
                    # 유저 협박 → AI가 유저를 지목할 확률 +25%p (반감/공포 반영)
                    if _r.random() < 0.25:
                        target = me_u
                elif tone == "persuade" and me_u and me_u in alive:
                    # 설득 → 유저 지목 확률 소폭 감소(-10%p)
                    if _r.random() < 0.08:
                        pass
                    elif _r.random() < 0.10 and target == me_u:
                        target = random_mod.choice([n for n in alive if n != me_u])

            def _apply():
                if not self.mafia_active or not getattr(self, "core", None):
                    return
                if self.core.phase not in (Phase.DAY, Phase.VOTE):
                    return
                if ag.name in self.core.votes:
                    return
                # v1.40 — 유저 우선: 유저가 아직 투표 전이면 잠시 대기 후 재시도
                if self._ai_vote_user_should_wait():
                    self.root.after(400, _apply)
                    return
                # 반영 (core) — v1.40: 대상은 비공개, 완료 여부만 알림(익명 개표)
                if target and ag.name in self.core.players:
                    self.core.cast_vote(ag.name, target)
                    _pd, _pt = self._vote_progress_counts()
                    self.add_mafia_system(f"🗳 {ag.name}(AI)님 투표 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                    self._refresh_vote_progress_label()
                    try:
                        self._update_vote_btn_state()
                    except Exception:
                        pass
                    # 전원 완료 체크 — v1.15: 즉시 조기개표
                    if self.core.all_voted():
                        self._schedule_tally(300)
                    else:
                        # v1.21 — AI끼리 다 냈고 유저 표만 남은 경우
                        me_name = getattr(self.engine, "name", None)
                        pending = [n for n in self.core.alive_players()
                                   if n not in self.core.votes and n not in self.core.abstains
                                   and n != getattr(self.core, "defendant", None)]
                        if me_name in pending and not getattr(self, "_user_pending_notified", False):
                            self._user_pending_notified = True
                            self.add_mafia_system("🗳 AI 투표 완료 — 당신의 표만 기다립니다 (8초 후 자동 개표)")
                            self._vote_remaining = min(getattr(self, "_vote_remaining", 15), 8)
                            if getattr(self, "_force_tally_timer", None):
                                try:
                                    self.root.after_cancel(self._force_tally_timer)
                                except Exception:
                                    pass
                            self._force_tally_timer = self.root.after(8_000, self._silent_tally_if_pending)

            try:
                self.root.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_bg_vote_worker, daemon=True).start()

    def _vote_popup_tick(self, initial=False):
        if not getattr(self, "_vote_lbl", None):
            return
        # DAY(낮타이머 만료 직후)/VOTE(개표전 window) 모두에서 유효.
        if getattr(self, "core", None) and self.core.phase not in (Phase.DAY, Phase.VOTE):
            self._cancel_vote_popup()
            return
        remain = max(0, int(self._vote_remaining))
        self._vote_lbl.config(text=f"⏳ 남은 시간: {remain}초")
        self._vote_remaining -= 1
        if remain <= 0:
            self._schedule_tally(0)
            return
        self._tick_vote = self.root.after(1000, self._vote_popup_tick)

    def _cancel_vote_popup(self):
        """v1.57 — 이전엔 타이머/참조만 정리하고 실제 팝업 위젯(_mafia_overlay)은
        안 닫아서, 유저 본인이 최후 변론 피고인이 되는 등 '유저가 직접 클릭'하지
        않은 경로로 개표가 진행되면 투표 팝업이 화면에 그대로 남아있었다(실측
        지적). 여기서 항상 실제로 닫는다 — _mafia_overlay_close는 열린 게
        없으면 조용히 아무 일도 안 하므로 중복 호출해도 안전."""
        t = getattr(self, "_tick_vote", None)
        if t:
            self.root.after_cancel(t)
            self._tick_vote = None
        # v1.61 — 유저 투표 직후 0.5초 피드백 지연 타이머(_pending_vote_close)를
        # 여기서도 취소해야 한다. 정상 개표 경로(_schedule_tally)에서는
        # _cancel_tally_safety_timers()가 정리해주지만, 시간 만료 등으로
        # _cancel_vote_popup()이 직접 호출되는 경로는 그걸 안 거쳐서, 살아남은
        # 이 타이머가 0.5초 뒤 엉뚱하게 떠 있는 다음 오버레이(밤 연출 등)를
        # 잘못 닫아버릴 수 있었다.
        pvc = getattr(self, "_pending_vote_close", None)
        if pvc:
            try:
                self.root.after_cancel(pvc)
            except Exception:
                pass
            self._pending_vote_close = None
        self._vote_lbl = None
        self._vote_btns = {}
        self._vote_abstain_btn = None
        self._vote_progress_lbl = None
        try:
            self._mafia_overlay_close()
        except Exception:
            pass

    def _host_receive_vote_cast(self, voter, target):
        """v1.61 — 호스트 전용: 원격 참가자가 보낸 낮 투표(vote_cast)를 실제
        core에 반영하고, 다른 모든 참가자에게 진행 상황을 재동기화한다."""
        if not self._mafia_is_host() or not voter or voter not in self.core.players:
            return
        if not (self.core.players.get(voter) or {}).get("alive", True):
            return
        revote = bool(getattr(self, "_revote_tied", None)) and not getattr(self, "_revote_tally_scheduled", True)
        if revote:
            # 재투표 중에는 core.phase가 DAY가 아니라 cast_vote가 거절하므로
            # 호스트 자신의 _cast_revote와 똑같이 직접 기록한다(동률 후보만 허용).
            if target and target not in self._revote_tied:
                return
            if target:
                self.core.votes[voter] = target
            else:
                self.core.cast_abstain(voter)
            ok = True
        else:
            ok = self.core.cast_vote(voter, target) if target else self.core.cast_abstain(voter)
        if not ok:
            return
        _pd, _pt = self._vote_progress_counts()
        if target:
            self.add_mafia_system(f"🗳 {voter}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
        else:
            self.add_mafia_system(f"🗳 {voter} 기권 접수 · 진행률 {_pd}/{_pt}")
        self._refresh_vote_progress_label()
        self._mafia_broadcast("vote", voter=voter, target=target)
        if revote:
            self._check_revote_done()
        elif self.core.all_voted():
            self._schedule_tally(300)

    def _popup_vote(self, name):
        me = getattr(self.engine, "name", None)
        if not me or not getattr(self, "core", None):
            return
        if name is None:
            if not (self.core.players.get(me) or {}).get("alive", True):
                self.add_mafia_system("👻 사망자는 기권할 수 없습니다 — 유령 채팅방에서 수다")
                self._open_ghost_chat()
                return
            ok = self.core.cast_abstain(me)
            if ok:
                # 즉시 모든 투표/기권 버튼 비활성화 (더블클릭/중복 클릭 방지)
                for _b in (getattr(self, "_vote_btns", {}) or {}).values():
                    try: _b.config(state="disabled")
                    except Exception: pass
                if getattr(self, "_vote_abstain_btn", None):
                    try: self._vote_abstain_btn.config(bg=M_HOST, fg="white", text="✓ 기권", state="disabled")
                    except Exception: pass
                self.add_mafia_bubble("투표 기권", "나", mine=True)
                _pd, _pt = self._vote_progress_counts()
                self.add_mafia_system(f"🗳 {me} 기권 접수 · 진행률 {_pd}/{_pt}")
                self._refresh_vote_progress_label()
                # v1.61 — 복수 인간 플레이: 내가 호스트면 다른 참가자들에게
                # 즉시 재동기화, 클라이언트면 호스트에게 실제 반영을 요청.
                if self._mafia_is_host():
                    self._mafia_broadcast("vote", voter=me, target=None)
                else:
                    self._mafia_send_to_host("vote_cast", voter=me, target=None)
        else:
            if not (self.core.players.get(me) or {}).get("alive", True):
                self.add_mafia_system("👻 사망자는 투표할 수 없습니다 — 유령 채팅방에서 수다")
                self._open_ghost_chat()
                return
            ok = self.core.cast_vote(me, name)
            if ok:
                # 즉시 모든 투표/기권 버튼 비활성화 (더블클릭/중복 클릭 방지)
                for _b in (getattr(self, "_vote_btns", {}) or {}).values():
                    try: _b.config(state="disabled")
                    except Exception: pass
                if getattr(self, "_vote_abstain_btn", None):
                    try: self._vote_abstain_btn.config(state="disabled")
                    except Exception: pass
                self.add_mafia_bubble("투표 완료 (익명 개표)", "나", mine=True)   # v1.47 — 대상 비노출
                _pd, _pt = self._vote_progress_counts()
                self.add_mafia_system(f"🗳 {me}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                self._refresh_vote_progress_label()
                # v1.61 — 복수 인간 플레이 동기화(위 기권 분기와 동일한 이유)
                if self._mafia_is_host():
                    self._mafia_broadcast("vote", voter=me, target=name)
                else:
                    self._mafia_send_to_host("vote_cast", voter=me, target=name)
                # v1.18 — 클릭 즉시 시각 피드백(눌린 버튼 보라색 + '✓ 접수' 라벨),
                # 0.25초 뒤 닫기 — '반응 없이 꺼진다' 체감 해소
                try:
                    b = (getattr(self, "_vote_btns", {}) or {}).get(name)
                    if b is not None:
                        b.config(bg=M_HOST, fg="white", text=f"✓ {name}", state="disabled")
                except Exception:
                    pass
        # v1.10/v1.18 — 접수 후 팝업 닫기(0.5초 피드백 노출 후)
        # v1.42 — 닫을 때 카운트다운 틱 타이머(_tick_vote)도 함께 취소해야 한다.
        # 안 그러면 이미 사라진 라벨을 계속 갱신하려다 매초 TclError가 난다.
        def _close_vote_popup_now():
            self._cancel_vote_popup()
            self._mafia_overlay_close()
        try:
            if getattr(self, "_pending_vote_close", None):
                self.root.after_cancel(self._pending_vote_close)
            self._pending_vote_close = self.root.after(500, _close_vote_popup_now)
        except Exception:
            pass
        # v1.24 — 유저 표 접수 직후 미투 AI 표 즉시 가속 접수(150ms 간격)
        # 집계 지연 최소화('프리징' 체감 원인 제거)
        try:
            if not self.core.all_voted():
                pend = [p for p in getattr(self, "ai", None) and self.ai.players or []
                        if getattr(p, "alive", False) and p.name not in self.core.votes
                        and self.core.players.get(p.name, {}).get("alive")]
                for k, p2 in enumerate(pend):
                    self.root.after(150 + k * 200, lambda pp=p2: self._ai_vote_in_popup(pp))
        except Exception:
            pass
        self._update_vote_btn_state()
        if ok and self.core.all_voted():
            self._schedule_tally(300)
        else:
            # v1.32 — 헌 소재 제거: 여기서 '8초 안전망'과 별개로 15.5초 타이머를
            # 또 예약하면 두 타이머가 경합해 중복 개표/프리징 체감 유발.
            # 8초 안전망(_update_vote_btn_state 경로)만 유지 — 등록 여부만 보증.
            if not getattr(self, "_force_tally_timer", None):
                self._force_tally_timer = self.root.after(
                    15_000, self._silent_tally_if_pending)

    def _silent_tally_if_pending(self):
        """v1.15 — 투표 window가 끝났는데 개표로 연결 안 된 상황의 안전망 개표.
        v1.32 — 중복 개표 방어: 이미 전원 표 접수 완료면 개표 1회 보증 후 무시."""
        self._force_tally_timer = None
        if not self._mafia_is_host():
            return
        if not self.mafia_active or self.core.phase not in (Phase.DAY, Phase.VOTE):
            return
        try:
            if getattr(self, "_tally_in_progress", False):
                return
            self._tally_in_progress = True
        except Exception:
            pass
        self._tally_and_reveal()
        self.root.after(2500, lambda: setattr(self, "_tally_in_progress", False))

    def _update_vote_btn_state(self):
        me = getattr(self.engine, "name", None)
        my_vote = self.core.votes.get(me) if (me and getattr(self, "core", None)) else None
        for n, b in getattr(self, "_vote_btns", {}).items():
            # votes={voter: target} — 내가 이미 투표하면 모든 버튼 잠금(익명 개표)
            if me and my_vote:
                # v1.22 — 내가 고른 버튼은 '✓' 보라 선택 표시 유지(피드백 보존)
                try:
                    if n == my_vote:
                        b.config(bg=M_HOST, fg="white", text=f"✓ {n}",
                                 state="disabled")
                    else:
                        b.config(bg="#111827", fg="#6b7280", state="disabled")
                except Exception:
                    pass   # 이미 닫힌 위젯 — 조용히 통과

    def _tally_and_reveal(self):
        """개표 진입점 — 표준 상태머신(동률 재투표/최후변론, `_tally_full`)으로 위임.
        v1.56 — VOTE_FULL_MACHINE은 상수로 고정된 True라 실제로 꺼진 적이 없어서,
        여기 있던 구 단일개표 경로(core.tally_votes/execute 직접 호출)는 도달
        불가능한 죽은 코드였다 — core.py의 구 메서드들과 함께 정리."""
        self._tally_scheduled = False
        if getattr(self, "_tally_in_progress", False):
            return
        self._tally_in_progress = True
        self._cancel_tally_safety_timers()
        self.core.phase = Phase.VOTE
        self.refresh_mafia_phase_label()
        return self._tally_full()

    # ---------- 보강: 문서 투표 상태 머신 (동률 재투표/최후변론/찬반) ----------
    def _tally_full(self):
        """문서 기획 개표 상태머신 — 동률 재투표 1회 → 최후 변론 → 찬반 투표.
        기존 _tally_and_reveal(단순 개표)은 유지, 설정창 '표준 상태머신' 켜면 이쪽 사용."""
        mode, data = self.core.tally_votes_full()
        if mode == "none":
            self.add_mafia_system("🗳 유효표 없음 — 전원 기권, 처형 무효.")
            return self.root.after(int(VOTE_REVEAL_DELAY * 1000), self._enter_night_sequence)
        if mode == "revote":
            if getattr(self, "_revote_used", False):
                self.add_mafia_system("🗳 재투표도 동률 — 처형 무효, 밤으로.")
                self._revote_used = False
                return self.root.after(int(VOTE_REVEAL_DELAY * 1000), self._enter_night_sequence)
            self._revote_used = True
            self.add_mafia_system(f"🗳 최다 득표 동률 ({', '.join(data['tied'])}) — 동률 후보만으로 1회 재투표!")
            self._open_revote_popup(data["tied"])
            return
        # 단독 최다 → 최후 변론
        self._revote_used = False
        self._open_defense_for(data["top"])

    def _open_revote_popup(self, tied):
        """동률 후보만 선택지로 제한한 재투표 팝업(기권 포함)."""
        # --- v1.06/v1.34: 재투표는 1차 표를 지우고 다시 수집(AI 투입 포함) ---
        self.core.votes.clear()
        self.core.abstains.clear()
        self._revote_tied = list(tied)
        self._revote_tally_scheduled = False
        self._vote_popup_open_ts = time.time()   # v1.40 — 유저 우선 유예시간 기준점(재투표)
        # v1.61 — 원격 참가자 화면에도 재투표 팝업이 뜨도록 알린다(이전엔 동률이
        # 나면 원격은 팝업 없이 호스트의 30초 강제 개표까지 기다렸다).
        if self._mafia_is_host():
            self._mafia_broadcast("revote_open", tied=list(tied))

        # v1.19 — 유저 사망 시 AI끼리 즉시 재투표(팝업 인터랙션 불필요):
        # 유령방 자동 오픈이 재투표 팝업을 가리는 것 + 유저 무응답 30초 대기 둘 다 제거
        me_check = getattr(self.engine, "name", None)
        if me_check and not (self.core.players.get(me_check) or {}).get("alive", True):
            self.add_mafia_system("👻 사망자 재투표 참관 모드 — AI끼리 진행합니다")
            # AI 전원 재투표 즉시 접수(기존 팝업 스케줄 유지) + 30초 대기 없음
            for i, pl in enumerate(getattr(self, "ai", None) and self.ai.players or []):
                if getattr(pl, "alive", False):
                    self.root.after(300 + i * 300,
                                    lambda p=pl, t=tied: self._ai_revote_fast(t, p))
            self._revote_deadline = self.root.after(25000, self._force_revote_tally)
            return

        # AI 전원 자동 재투표 — 동률 후보 중 (인격 성향 기반 즉시 결정)
        for i, pl in enumerate(getattr(self, "ai", None) and self.ai.players or []):
            if getattr(pl, "alive", False):
                self.root.after(500 + i * 350,
                                lambda p=pl, t=tied: self._ai_revote_fast(t, p))
        # v1.10 — 동률 후보가 '나'뿐이면 선택지 없음 → 즉시 기권+개표(정지 방지)
        me_check = getattr(self.engine, "name", None)
        if [n for n in tied if n != me_check] == []:
            self.add_mafia_system("🗳 동률 후보가 나뿐 — 유일 대상 제외, 기권 개표로 진행합니다.")
            self.root.after(300, self._force_revote_tally)
            return
        # 유저 무응답 대비 — 30초 후 미투자 기권 + 강제 개표(멈춤 원천 차단)
        self._revote_deadline = self.root.after(30000, self._force_revote_tally)
        body = self._mafia_overlay_open("🔄 재투표 — 동률 후보 중 지목", w=360, h=None)
        tk.Label(body, text="동률 후보 중에서만 선택 가능 (기권 허용)",
                 fg="#a78bfa", bg=C_CARD, font=M_FONT_BODY_B).pack(pady=(10, 6))
        prog_lbl = tk.Label(body, text="", fg="#a78bfa", bg=C_CARD, font=M_FONT_HELP)
        prog_lbl.pack(pady=(0, 6))
        self._vote_progress_lbl = prog_lbl
        self._refresh_vote_progress_label()
        row = tk.Frame(body, bg=C_CARD); row.pack(fill="x", padx=18, pady=(0, 8))
        me_now = getattr(self.engine, "name", None)
        # v1.10 — 재투표 후보에서 나(유저) 제외(자투 방지 — v1.03 원칙)
        cand_names = [n for n in tied if n != me_now]

        def _mk_revote_btn(parent, n):
            return emoji_render.make_pill_button(
                parent, n, lambda nn=n: self._cast_revote(nn),
                bg=M_BTN_BG, fg=M_TEXT_LIGHT, hover_bg="#374151",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=10,
                radius=8, pad_x=10, pad_y=6, min_w=96
            )
        self._revote_btns = self._grid_candidates_centered(row, cand_names, _mk_revote_btn)
        skip = emoji_render.make_pill_button(
            body, "기권", lambda: self._cast_revote(None),
            bg="#1f2937", fg="#9ca3af", hover_bg="#374151",
            font_path=emoji_render.FONT_PATH_REGULAR, font_size=9,
            radius=6, pad_x=14, pad_y=4
        )
        skip.pack(pady=(4, 10))
        self._revote_skip_btn = skip

    def _force_revote_tally(self):
        """유저가 재투표에 무응답 30초 — 기권 처리하고 개표."""
        if not self._mafia_is_host():
            self._mafia_overlay_close()   # 개표는 호스트 전용 — 원격은 팝업만 정리
            return
        me = getattr(self.engine, "name", None)
        alive = self.core.alive_players()
        if me and me in alive and me not in self.core.votes and me not in self.core.abstains:
            self.core.cast_abstain(me)
            self.add_mafia_system(f"🗳 {me} 재투표 무응답 — 기권 처리")
        for n in alive:
            if n not in self.core.votes and n not in self.core.abstains:
                self.core.cast_abstain(n)
        self._mafia_overlay_close()
        self._check_revote_done()

    def _ai_revote_fast(self, tied, pl):
        """v1.06 — AI 재투표 즉시 결정 (동률 후보 중 1명). 누락 없이 반영."""
        import random as _r
        # v1.40 — 유저 우선: 유저가 아직 재투표 전이면 잠시 대기 후 재시도
        if self._ai_vote_user_should_wait():
            self.root.after(400, lambda: self._ai_revote_fast(tied, pl))
            return
        try:
            cand = [t for t in tied if t != pl.name and t in self.core.alive_players()]
            if not cand:
                self.core.cast_abstain(pl.name)
            else:
                target = _r.choice(cand)
                self.core.votes[pl.name] = target
                _pd, _pt = self._vote_progress_counts()
                self.add_mafia_system(f"🗳 {pl.name}(AI)님 재투표 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
        except Exception:
            try:
                self.core.cast_abstain(pl.name)
            except Exception:
                pass
        self._refresh_vote_progress_label()
        self._check_revote_done()

    def _check_revote_done(self):
        if not self._mafia_is_host():
            return
        alive = self.core.alive_players()
        if all(n in self.core.votes or n in self.core.abstains for n in alive):
            if getattr(self, "_revote_tally_scheduled", False):
                return
            self._revote_tally_scheduled = True
            dl = getattr(self, "_revote_deadline", None)
            if dl:
                try:
                    self.root.after_cancel(dl)
                except Exception:
                    pass
                self._revote_deadline = None
            self.root.after(700, self._tally_full)   # 2차 개표

    def _cast_revote(self, name):
        me = getattr(self.engine, "name", None)
        if me:
            if name:
                self.core.votes[me] = name
            else:
                self.core.cast_abstain(me)
            # v1.47 — 대상 비노출(재투표도 본투표와 동일하게 익명 유지)
            self.add_mafia_bubble("재투표 완료" if name else "기권", "나", mine=True)
            _pd, _pt = self._vote_progress_counts()
            self.add_mafia_system(f"🗳 {me}님 재투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
            if self._mafia_is_host():
                self._mafia_broadcast("vote", voter=me, target=name)
            else:
                self._mafia_send_to_host("vote_cast", voter=me, target=name)
        self._mafia_overlay_close()
        self._check_revote_done()

    def _open_defense_for(self, name):
        """최후 변론 — 피고인 1명만 채팅 가능 + 찬반 투표. v1.02 예정."""
        if getattr(self, "_defense_in_progress", False):
            return
        self._defense_in_progress = True
        self.core.set_defendant(name)
        self.add_mafia_host(f"⚖ '{name}' 님이 최다 득표로 최후 변론대에 섰습니다. (60초)")
        self.add_mafia_system(f"⚖ 최후 변론(60초): 피고인만 변론합니다. 이후 찬반 투표({DEFENSE_VOTE_WINDOW}초).")
        self._start_defense_visuals(name)
        self._mafia_broadcast("defense_start", name=name)
        # --- v1.23 — 변론 기간 60초: AI 피고인도 60초 동안 변론 기회 (3회 분할 발화)
        import queue as _q
        self._defense_ui_q = _q.Queue()
        self._defense_has_spoken = False
        self._defense_voters = []
        defendant_ai = None
        for pl in getattr(self, "ai", None) and self.ai.players or []:
            if pl.name == name and pl.alive:
                defendant_ai = pl
            elif pl.alive:
                self._defense_voters.append(pl)
        # AI 피고인 변론 — 0.5초 / 20초 / 40초 시점, 각각 LLM
        if defendant_ai is not None:
            for idx, delay in enumerate((500, 20_000, 40_000)):
                self.root.after(delay, lambda p=defendant_ai, first=(idx == 0): self._ai_defense(p, first))
        # 나(유저)가 피고인이면 직접 타이핑 변론 — 팝업은 변론 종료 후 뜸.
        self._ai_defense_voted = False
        self._defense_popup_shown = False
        self._poll_defense_ui_queue()
        # v1.23 — 변론 종료(60초) 시: 찬반 팝업 + AI 찬반 투표 개시 + 30초 찬반 안전망
        self._defense_fallback_timer = self.root.after(6_000, lambda: self._defense_fallback(name))
        self._defense_end_timer = self.root.after(60_000, lambda: self._start_defense_votes(name))
        self._defense_deadline = self.root.after(
            (60 + DEFENSE_VOTE_WINDOW) * 1000, lambda: self._force_resolve_defense(name))
        # v1.24 — 개표 완료 안내: '60초 변론 진행 중'을 명시(멈춤 착각 방지)
        self.add_mafia_system("🗳 개표 완료 — 60초 변론 진행 중입니다 (멈춘 것 아님)")
        # (기존 '변론문이 채팅에 뜨면 찬반 개시' → '변론 60초 종료 후 개시'로 재정렬)

    def _start_defense_votes(self, name):
        """v1.23 — 변론 기간(60초) 종료 후 찬반 투표 개시(팝업+AI 표)."""
        self._unlock_defense_entry()
        self.add_mafia_system(f"⚖ 변론 종료 — 찬반 투표를 부탁합니다 ({DEFENSE_VOTE_WINDOW}초 이내)")
        self._defense_popup_shown = True
        if self._mafia_is_host():
            self._mafia_broadcast("defense_vote_open", name=name)
        me_name = getattr(self.engine, "name", None)
        if (self.core.players.get(me_name) or {}).get("alive", True):
            self._show_defense_vote_popup(name)
        # AI 찬반 표 — 변론 종료 후 350ms 간격 투입
        if not self._ai_defense_voted:
            self._ai_defense_voted = True
            for i, pl in enumerate(getattr(self, "_defense_voters", [])):
                self.root.after(300 + i * 350, lambda p=pl: self._ai_defense_vote_fast(p))

    def _defense_fallback(self, name):
        """v1.22 — 변론 LLM 무응답 6초 시 기본 변론문을 큐에 주입(UI 개시 트리거).
        v1.31 — 유저가 피고인이면 주입 금지: 유저 이름으로 AI가 미리 쓴 변론문이
        뜨는 '사칭' 결함 제거 (유저는 직접 타이핑 변론). 안내문만 표시.
        v1.34 — 이미 변론이 제출되었으면 중복 주입 스킵."""
        if getattr(self, "_defense_has_spoken", False):
            return
        import queue as _q
        me_u = getattr(self.engine, "name", None)
        if me_u and name == me_u:
            self.add_mafia_system("(안내) 변론 기간 중입니다 — 채팅으로 직접 변론하세요 (60초)")
            return
        q = getattr(self, "_defense_ui_q", None)
        if q is not None and getattr(self.core, "defendant", None) == name:
            self._defense_has_spoken = True
            q.put(("defense", name,
                   "이 문제를 다시 한번 생각해 주세요. 저는 마피아가 아니라고 확신합니다."))

    def _poll_defense_ui_queue(self):
        """v1.20 — 변론 UI 큐 드레인(메인스레드 전용). 채팅 반영 후 AI 찬반 개시."""
        try:
            while True:
                kind, name, t = self._defense_ui_q.get_nowait()
                if kind == "defense":
                    # v1.31 — 유저 본인 이름의 'AI 생성' 변론문 스킵(사칭 차단)
                    if name and name == getattr(self.engine, "name", None):
                        continue
                    # v1.23 — 변론 발화는 채팅 반영만 함(찬반 개시는 60초 후 별도)
                    self._defense_has_spoken = True
                    self.add_mafia_bubble(t, name)
        except Exception:
            pass
        if getattr(self, "_defense_ui_q", None) is not None and getattr(self.core, "defendant", None):
            self.root.after(300, self._poll_defense_ui_queue)

    def _barometer_last_user_tone(self):
        """v1.26 — 유저 최근 발화 톤 측정: 협박/설득/중립. 변론 토대 반영."""
        me = getattr(self.engine, "name", None)
        vals = []
        for rec in getattr(self, "mafia_history", [])[-5:]:   # v1.26 — 최근 5개만
            # v1.26 — 유저 발화는 mine=True('나')로 저장 — 라벨 조건 동시 인정
            if rec.get("kind") == "text" and (rec.get("mine") or rec.get("label") == me or rec.get("label") == "나"):
                vals.append(str(rec.get("text", "")))
        joined = " ".join(vals)
        threat_kw = ("죽", "해치", "죽여", "목", "처형", "무조건", "필사", "협박")
        pers_kw = ("근거", "논리", "증거", "생각", "아니", "의심")
        if any(k in joined for k in threat_kw):
            return "threat", vals[-1] if vals else ""
        if any(k in joined for k in pers_kw):
            return "persuade", vals[-1] if vals else ""
        return "neutral", vals[-1] if vals else ""

    def _ai_defense_vote_fast(self, pl):
        """AI 찬반 투표 — LLM 대기 없이 인격/역할 기반 즉시 결정(표 누락 방지).
        v1.26 — 유저 변론 기간 발화 톤(협박/설득) 반영."""
        import random as _r
        try:
            c = self.core
            defendant = c.defendant
            ver = c.players.get(defendant, {}).get("role")
            if pl.role == "police" and defendant in c.police_invest:
                known = c.police_invest[defendant]
                yes = (known == "mafia")
            elif pl.role == "mafia":
                if ver == "mafia":
                    # 동료 마피아 변론: 기본적으로 살리기 위해 반대(만류) 투표 (85% 반대)
                    yes = (_r.random() < 0.15)
                else:
                    # 시민 처형 찬성 몰아가기 (75% 찬성)
                    yes = (_r.random() < 0.75)
            else:
                if pl.role in ("doctor", "police"):
                    p_y = 0.45
                else:
                    p_y = 0.55
                # v1.26/v1.34 — 유저 톤 반영: 피고인이 유저 본인일 때만 발화 톤 보정 적용
                me_u = getattr(self.engine, "name", None)
                if me_u and defendant == me_u:
                    tone, _t = self._barometer_last_user_tone()
                    if tone == "threat":
                        p_y = min(0.95, p_y + 0.35)
                    elif tone == "persuade":
                        p_y = max(0.05, p_y - 0.20)
                yes = _r.random() < p_y
            ok = c.cast_defense_vote(pl.name, yes)
            if ok:
                # v1.47 — 대상(찬성/반대) 비공개 — 본투표 익명화와 동일 원칙 적용
                self.add_mafia_system(f"⚖ {pl.name}님 찬반 표 접수 (익명) · {self._defense_progress_text()}")
            self._maybe_resolve_defense(defendant)
        except Exception:
            pass

    def _maybe_resolve_defense(self, name):
        """표가 다 모이면 즉시 개표(500ms 딜레이 없이)."""
        if getattr(self.core, "defendant", None) and self.core.defense_all_voted():
            self._clear_defense_deadline()
            self.root.after(300, lambda: self._resolve_defense(name))

    def _clear_defense_deadline(self):
        for attr in ("_defense_deadline", "_defense_end_timer", "_defense_fallback_timer"):
            t = getattr(self, attr, None)
            if t:
                try:
                    self.root.after_cancel(t)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _force_resolve_defense(self, name):
        """60초 안전망(v1.23) — 미투자 기권 간주 강제 개표(멈춤 방지)."""
        try:
            if getattr(self.core, "defendant", None):
                c = self.core
                voters = [n for n, p in c.players.items()
                          if p["alive"] and n != c.defendant]
                for n in voters:
                    if n not in c.defense_yes:
                        c.defense_yes[n] = False   # 기권 = 반대 취급
                self._resolve_defense(name)
        except Exception:
            pass

    # ---------- 보강: 유령 채팅방(사망자 전용) ----------
    def _open_ghost_chat(self):
        """사망자끼리만 진실(직업) 알고 수다 떠는 비공개 유령 채팅방.
        생존자에게는 전혀 노출되지 않는 별도 overlay."""
        me = getattr(self.engine, "name", None)
        if not me or self.core.players.get(me, {}).get("alive", False):
            self.add_mafia_system("👻 유령 채팅방은 사망자 전용입니다 — 지금은 생존 중이라 들어올 수 없습니다")
            return
        # v1.17 — 유령방 UI 큐 준비(워커→메인스레드 안전 반영 경로)
        import queue as _q
        self._ghost_ui_q = getattr(self, "_ghost_ui_q", None) or _q.Queue()
        self._ghost_ui_open = True
        ghosts = self.core.ghost_room_members()
        if not ghosts:
            self.add_mafia_system("👻 사망자가 아직 없습니다.")
            return
        body = self._mafia_overlay_open("👻 유령 채팅방 — 사망자들만의 공간", w=420, h=None)
        # v1.17 — overlay_open 내부의 prev-close가 flag를 reset하므로 '뒤'에서 복원
        self._ghost_ui_open = True
        tk.Label(body, text="여기는 사망자만 보는 공간 — 모든 진실이 공개됩니다.",
                 fg="#a5b4fc", bg=C_CARD, font=M_FONT_HELP).pack(pady=(10, 6))
        listf = tk.Frame(body, bg=C_CARD); listf.pack(fill="x", padx=18)
        self._ghost_list = tk.Text(listf, height=8, bd=0, bg=C_CARD, fg=M_TEXT_LIGHT,
                                    font=M_FONT_BODY, wrap="word", state="disabled")
        self._ghost_list.pack(fill="x")
        # 이력 — 유령 + 직업 공개
        self._render_ghost_list(body)
        # 입력
        ent_f = tk.Frame(body, bg=C_CARD); ent_f.pack(fill="x", padx=18, pady=(6, 10))
        self._ghost_ent = tk.Entry(ent_f, bg="#111827", fg=M_TEXT_LIGHT, relief="flat",
                                   insertbackground=M_TEXT_LIGHT, font=M_FONT_BODY)
        self._ghost_ent.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._ghost_ent.bind("<Return>", self._ghost_send)
        emoji_render.make_pill_button(
            ent_f, "보내기", self._ghost_send, bg=M_HOST, fg="white",
            hover_bg="#9333ea", font_path=emoji_render.FONT_PATH_REGULAR,
            font_size=10, radius=6, pad_x=12, pad_y=4
        ).pack(side="right")
        # v1.13 — 사망 AI가 유령방에서 수다를 떠는 자동 발화(1~5초 후에 1~2건)
        self._kick_ghost_ai_chat()

    def _kick_ghost_ai_chat(self):
        """v1.13 — 유령방에 사망 AI가 자동으로 수다를 떠는 발화.
        사망 AI 1~2명에게 '유령방 잡담' 프롬프트, 완료 후 렌더 반영."""
        import random as _rr
        import threading as _th
        ghosts_live = [pl for pl in getattr(self, "ai", None) and self.ai.players or []
                       if not pl.alive and getattr(pl, "booted", False)]
        if not ghosts_live:
            return
        _rr.shuffle(ghosts_live)
        picks = ghosts_live[:2]
        for i, pl in enumerate(picks):
            def worker(p_, idx_):
                # v1.17 — LLM만 워커 스레드에서, UI 반영은 큐 → 메인스레드 폴러.
                # (기존: 워커에서 root.after 직접 호출 → 'main thread is not in
                #  main loop' RuntimeError → except로 무음 소멸 = 유령방 시 현상)
                try:
                    role_str = self.core.reveal_role(p_.name) or ""
                    others = [g for g in self.core.ghost_room_members() if g != p_.name]
                    txt = p_.say(
                        f"[유령 채팅방] 당신('{p_.name}')은 마피아 게임에서 사망해 유령방에 "
                        f"입장했습니다. 여기엔 생자 없음(직업 공개 자유). 당신 직업: {role_str}. "
                        f"함께 있는 유령: {', '.join(others) or '없음'}. "
                        f"돌아가서 실제 게임 채팅에 절대 겹치지 않게, 이 방에서만 "
                        f"사망자의 속닥임이나 부탁, 마지막 충고 2문장 이내로 해보세요.")
                    txt2 = (txt or "").strip()
                    if txt2:
                        t2 = split_chat_tags(clean_llm_dialect(txt2))
                        self._ghost_ui_q.put(("ai", p_.name, t2))
                except Exception:
                    pass
            _th.Thread(target=worker, args=(pl, i), daemon=True).start()
        # UI 큐 폴러 — 메인스레드에서만 위젯 접근
        self._poll_ghost_ui_queue()
        # LLM 실패/침묵 시 기본 문구 — 3~5초 후
        self.root.after(3000, self._ghost_fallback_lines)

    def _poll_ghost_ui_queue(self):
        """v1.17 — 유령방 UI 큐 드레인(메인스레드 전용). 0.3초 주기."""
        try:
            while True:
                kind, name, t = self._ghost_ui_q.get_nowait()
                self._append_ghost(f"👻 {name}: {t}", ai=True)
        except Exception:
            pass
        if getattr(self, "_ghost_ui_open", False):
            self.root.after(300, self._poll_ghost_ui_queue)

    def _ghost_whisper_ok(self, txt):
        """유령방 문구 검증 — 실제 게임 채팅에 겹치는 모양새 방지(느낌표/쓸데없이)."""
        if not txt:
            return False
        # 최대 160자 — 유령방 수단이기 때문에 짧게
        return len(txt) <= 160

    def _ghost_fallback_lines(self):
        import random as _rr
        ghosts_live = [pl for pl in getattr(self, "ai", None) and self.ai.players or []
                       if not pl.alive and getattr(pl, "booted", False)]
        if not ghosts_live:
            return
        _rr.shuffle(ghosts_live)
        mafia_lines = [
            "아 진짜, 내가 마피아인 걸 어떻게 알았지…",
            "우리 팀 마피아들, 끝까지 힘내라!",
            "하필 나를 찍다니… 그래도 후회는 없다.",
        ]
        citizen_lines = [
            "억울하다! 난 진짜 선량한 시민이었는데…",
            "내가 마피아 아니라고 그렇게 말했건만…",
            "남은 시민분들, 꼭 마피아 잡아주세요!",
        ]
        police_lines = [
            "조사 결과를 더 많이 공유했어야 했는데… 아쉽다.",
            "경찰인 날 이렇게 허무하게 보내다니…",
        ]
        doctor_lines = [
            "나 자신을 살렸어야 했나… 시민들을 지켜야 했는데.",
            "의사가 먼저 가버렸으니 남은 분들 조심하세요.",
        ]
        for i, pl in enumerate(ghosts_live[:2]):
            role = self.core.reveal_role(pl.name) if getattr(self, "core", None) else getattr(pl, "role", "citizen")
            if role == "mafia":
                t = _rr.choice(mafia_lines)
            elif role == "police":
                t = _rr.choice(police_lines)
            elif role == "doctor":
                t = _rr.choice(doctor_lines)
            else:
                t = _rr.choice(citizen_lines)
            self.root.after(int(200 + i * 900),
                lambda p_=pl, msg=t:
                    self._append_ghost(f"👻 {p_.name}: {msg}", ai=True))

    def _render_ghost_list(self, body):
        box = getattr(self, "_ghost_list", None)
        if not box:
            return
        box.configure(state="normal")
        box.delete("1.0", "end")
        for n in self.core.ghost_room_members():
            role = self.core.reveal_role(n)
            box.insert("end", f"👻 {n} — 직업 공개: {_role_kr(role) if role else '...'}\n")
        box.configure(state="disabled")

    def _ghost_send(self, ev=None):
        ent = getattr(self, "_ghost_ent", None)
        if not ent:
            return
        txt = (ent.get() or "").strip()
        if not txt:
            return
        ent.delete(0, "end")
        self._append_ghost(f"{self.engine.name}: {txt}")
        # 다른 유령들의 화면에 실제로도 전달될 수 있어나 현재 P2P 협재임으로 로컬만.
        # (문서 대로의 '유빙방'은 로컬 구현 — P2P 확장은 예정)

    def _append_ghost(self, text, ai=False):
        box = getattr(self, "_ghost_list", None)
        if not box:
            return
        box.configure(state="normal")
        box.insert("end", text + "\n")
        box.configure(state="disabled")
        box.see("end")

    def _show_defense_vote_popup(self, name):
        me = getattr(self.engine, "name", None)
        # v1.19 — 사망자는 찬반 투표권 없음: AI끼리 진행 (팝업 열지 않음)
        if me and not (self.core.players.get(me) or {}).get("alive", True):
            self.add_mafia_system("👻 사망자 찬반 투표권 없음 — AI끼리 진행합니다")
            # AI 표는 이미 예약돼 있고, 유저 표 없이도 30초 안전망이 개표 보장
            return
        body = self._mafia_overlay_open("⚖ 최후 변론 — 처형 찬/반", w=320, h=None)
        tk.Label(body, text=f"'{name}'을(를) 처형할까요?", fg=M_TEXT_LIGHT,
                 bg=C_CARD, font=(FONT_FAM, 12, "bold"),
                 wraplength=280, justify="center").pack(pady=(14, 10), padx=16)
        if me == name:
            # v1.32 — 유저 피고인: 투표권 없음 + 10초 후 팝업 자동 내려감
            # v1.61 — wraplength가 없어서 긴 문장이 팝업 폭(320px)보다 넓게
            # 한 줄로 그려져 좌우로 잘려 보이던 버그 수정(실측 지적).
            tk.Label(body, text="당신은 피고인입니다 — 투표권 없음 "
                                "(AI들의 찬반만 사용)", fg="#fbbf24",
                     bg=C_CARD, font=M_FONT_HELP,
                     wraplength=280, justify="center").pack(pady=(0, 4), padx=16)
            lbl10 = tk.Label(body, text="⏳ 10초 후 자동으로 닫힙니다 (10)", fg="#9ca3af",
                             bg=C_CARD, font=M_FONT_HELP)
            lbl10.pack(pady=(0, 6))
            emoji_render.apply(lbl10, M_FONT_HELP)
            state = {"n": 10}
            def _close_early():
                self._defense_popup10_cancelled = True
                try:
                    self._mafia_overlay_close()
                except Exception:
                    pass
            b_close = emoji_render.make_pill_button(
                body, "확인 (닫기)", _close_early,
                bg="#374151", fg="white", hover_bg="#4b5563",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=9,
                radius=6, pad_x=12, pad_y=4
            )
            b_close.pack(pady=(0, 10))
            def _tick10():
                if getattr(self, "_defense_popup10_cancelled", False):
                    return
                state["n"] -= 1
                if state["n"] <= 0:
                    try:
                        self._mafia_overlay_close()
                    except Exception:
                        pass
                    self.add_mafia_system("⚖ 피고인 화면 닫힘 — 찬반 투표는 AI들이 진행합니다")
                    return
                try:
                    lbl10.config(text=f"⏳ 10초 후 자동으로 닫힙니다 ({state['n']})")
                except Exception:
                    return
                self._defense_popup10 = self.root.after(1000, _tick10)
            self._defense_popup10_cancelled = False
            self._defense_popup10 = self.root.after(1000, _tick10)
            # v1.61 — 다른 팝업이 이 화면을 밀어내고 뜨는 경우(오버레이 교체)
            # 이 10초 타이머가 그대로 살아남아 있다가 나중에 엉뚱한 새 팝업의
            # lbl10을 건드리려 하는 경합을 막는다 — 어떤 이유로든 오버레이가
            # 닫히면 항상 같이 취소되도록 한다(실측 지적: "10초 후 자동으로
            # 사라지는 로직이 없다"고 보였던 것도 이 경합이 원인일 수 있음).
            self._wrap_overlay_close_with(self._cancel_defense_popup10)
            return
        lbl_dv = tk.Label(body, text=f"⏳ {DEFENSE_VOTE_WINDOW}초 이내에 투표하세요",
                          fg="#9ca3af", bg=C_CARD, font=M_FONT_HELP)
        lbl_dv.pack(pady=(0, 4))
        emoji_render.apply(lbl_dv, M_FONT_HELP)
        row = tk.Frame(body, bg=C_CARD); row.pack(pady=(0, 12))
        # 두 버튼 글자 길이가 달라("처형 찬성" vs "만류") 그냥 두면 알약 폭이
        # 서로 달라 좌우 비대칭으로 보인다(실측 지적) — 둘 중 더 넓은 쪽 폭을
        # 재서 min_w로 공통 적용해 항상 같은 크기로 맞춘다.
        _defense_pad_x = 14
        _defense_btn_w = _defense_pad_x * 2 + max(
            (emoji_render.render_mixed_text("🔪 처형 찬성", emoji_render.FONT_PATH_BOLD, 10) or
             emoji_render._plain_text_image("🔪 처형 찬성", emoji_render.FONT_PATH_BOLD, 10, "white")).width,
            (emoji_render.render_mixed_text("🕊 만류", emoji_render.FONT_PATH_BOLD, 10) or
             emoji_render._plain_text_image("🕊 만류", emoji_render.FONT_PATH_BOLD, 10, "white")).width,
        )
        _btn_yes = emoji_render.make_pill_button(
            row, "🔪 처형 찬성", lambda: self._cast_defense(name, True),
            bg="#dc2626", fg="white", hover_bg="#ef4444",
            font_path=emoji_render.FONT_PATH_BOLD, font_size=10,
            radius=8, pad_x=_defense_pad_x, pad_y=6, min_w=_defense_btn_w
        )
        _btn_yes.pack(side="left", padx=6)
        _btn_no = emoji_render.make_pill_button(
            row, "🕊 만류", lambda: self._cast_defense(name, False),
            bg="#15803d", fg="white", hover_bg="#16a34a",
            font_path=emoji_render.FONT_PATH_BOLD, font_size=10,
            radius=8, pad_x=_defense_pad_x, pad_y=6, min_w=_defense_btn_w
        )
        _btn_no.pack(side="left", padx=6)
        self._defense_btns = [_btn_yes, _btn_no]
        # 15초 안내 카운트다운 — 실제 '안 누르면 기권=반대(부결 쪽) 취급'은
        # 이미 있던 _force_resolve_defense(60+15초 절대 시각)가 그대로 담당하고,
        # 여기서는 화면에 남은 시간을 보여주고 시간이 다 되면 이 팝업만 닫는다
        # (기존에도 안 눌러도 언젠가 처리는 됐지만, 팝업이 화면에 계속 남아있는
        # 문제가 있었음 — 실측 지적으로 만든 게 아니라 겸사겸사 같이 정리).
        state = {"n": DEFENSE_VOTE_WINDOW}
        def _tick_defense_vote():
            self._defense_vote_tick = None
            if not getattr(self, "_mafia_overlay", None):
                return
            state["n"] -= 1
            if state["n"] <= 0:
                self.add_mafia_system("⏰ 찬반 투표 시간 초과 — 기권 처리(반대로 집계)")
                try:
                    self._mafia_overlay_close()
                except Exception:
                    pass
                return
            try:
                lbl_dv.config(text=f"⏳ {state['n']}초 이내에 투표하세요")
            except Exception:
                return
            self._defense_vote_tick = self.root.after(1000, _tick_defense_vote)
        self._defense_vote_tick = self.root.after(1000, _tick_defense_vote)
        self._wrap_overlay_close_with(self._cancel_defense_vote_tick)

    def _cancel_defense_popup10(self):
        self._defense_popup10_cancelled = True
        t = getattr(self, "_defense_popup10", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
            self._defense_popup10 = None

    def _cancel_defense_vote_tick(self):
        t = getattr(self, "_defense_vote_tick", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
            self._defense_vote_tick = None

    def _wrap_overlay_close_with(self, extra_cleanup):
        """마피아 오버레이(_mafia_overlay_open/_close)는 팝업마다 self._mafia_overlay_close를
        새 클로저로 통째로 갈아끼우는 구조라, 팝업 전용 타이머(카운트다운 등)를
        '오버레이가 어떤 이유로든 닫힐 때' 확실히 정리하려면 그 클로저를 한 겹
        더 감싸야 한다 — 그래야 유저가 직접 선택해서 닫든, 다른 팝업이 떠서
        기존 걸 밀어내든, 항상 같은 지점에서 타이머가 취소된다(다른 팝업 타이머가
        엉뚱하게 새 팝업을 건드리는 충돌을 막는 핵심 안전장치)."""
        prev_close = self._mafia_overlay_close
        def _wrapped(*a):
            try:
                extra_cleanup()
            except Exception:
                pass
            return prev_close(*a)
        self._mafia_overlay_close = _wrapped

    def _cast_defense(self, name, yes):
        me = getattr(self.engine, "name", None)
        for _b in getattr(self, "_defense_btns", []):
            try: _b.config(state="disabled")
            except Exception: pass
        if not me:
            return
        self.core.cast_defense_vote(me, yes)
        # v1.47 — 찬반 투표도 본투표와 동일하게 완전 익명(누가 찬성/반대인지 비공개).
        self.add_mafia_bubble("찬반 투표 완료 (익명)", "나", mine=True)
        self._mafia_overlay_close()
        if self._mafia_is_host():
            self.add_mafia_system(f"⚖ {me}님 찬반 표 접수 (익명) · {self._defense_progress_text()}")
            # 즉시 개표 체크(이전: 500ms after만 — AI 표 누락 시 멈춤)
            self._maybe_resolve_defense(name)
        else:
            # v1.61 — 복수 인간 플레이: 개표 판정은 호스트 전용 권한이라(다른
            # 클라이언트가 자기 로컬의 불완전한 core만 보고 "전원 표 완료"로
            # 착각해 제멋대로 개표를 내리는 사고를 막아야 한다) 클라이언트는
            # 로컬 표시만 하고 실제 반영·판정은 호스트에게 위임한다.
            self._mafia_send_to_host("defense_vote_cast", voter=me, name=name, yes=yes)

    def _host_receive_defense_vote(self, voter, name, yes):
        """v1.61 — 호스트 전용: 원격 참가자의 찬반 표를 실제 core에 반영하고,
        전원 완료면 호스트가 개표한다(개표 판정은 호스트 전용 권한)."""
        if not self._mafia_is_host() or not voter or voter not in self.core.players:
            return
        if getattr(self.core, "defendant", None) != name:
            return   # 이미 끝난 재판에 늦게 도착한 표
        if not (self.core.players.get(voter) or {}).get("alive", True):
            return
        self.core.cast_defense_vote(voter, bool(yes))
        self.add_mafia_system(f"⚖ {voter}님 찬반 표 접수 (익명) · {self._defense_progress_text()}")
        self._maybe_resolve_defense(name)

    def _resolve_defense(self, name):
        # v1.33 — 찬반 개표 이중 실행 차단: 이미 개표 완료(defendant=None 또는
        # 다른 진행)면 무시 — '찬성 3:0 처형' 직후 '찬성 0:0 부결' 재출력 방지.
        if getattr(self.core, "defendant", None) != name or getattr(self, "_defense_resolving", False):
            return
        self._defense_resolving = True
        try:
            self._resolve_defense_inner(name)
        finally:
            self.root.after(800, lambda: setattr(self, "_defense_resolving", False))

    def _resolve_defense_inner(self, name):
        self._clear_defense_deadline()
        # v1.32 — 피고인 10초 자동닫힘 타이머 정리
        self._defense_popup10_cancelled = True
        t10 = getattr(self, "_defense_popup10", None)
        if t10:
            try:
                self.root.after_cancel(t10)
            except Exception:
                pass
            self._defense_popup10 = None
        result, yes, no = self.core.execute_defense(name)
        self._sync_ai_alive()   # v1.11 — 최후변론 처형 시 AI 발화 차단
        role2 = self.core.reveal_role(name)
        if result == "executed":
            self.add_mafia_system(f"⚖ 찬성 {yes} : 반대 {no} — '{name}' 님 처형 확정!")
            if role2:
                self.add_mafia_system(f"🎭 직업 공개 — {name} ({_role_kr(role2)}였습니다)")
            if name == getattr(self.engine, "name", None):
                self._open_ghost_chat()
        else:
            self.add_mafia_system(f"⚖ 찬성 {yes} : 반대 {no} — 처형 부결, '{name}' 님은 살아남았습니다.")
        self._show_verdict_visuals(result, name, role2, yes, no)
        self._mafia_broadcast("verdict", result=result, name=name, role=role2, yes=yes, no=no)
        winner = self.core.check_winner()
        if winner:
            self._on_game_end(winner)
            return
        self.root.after(int(VOTE_REVEAL_DELAY * 1000), self._enter_night_sequence)

    def _ai_defense(self, pl, is_first=False):
        """v1.20: 변론 LLM을 워커 스레드로 + 응답은 UI 큐로 메인스레드 반영.
        (기존: 워커에서 root.after 직접 호출 → 스레드 오류 무음 소멸 →
          변론문이 채팅에 안 뜨고 찬반 팝업만 뜸 — 유저 신고 사유)
        실패 시 인격 기반 기본 변론문. 응답 도착적으로 AI 찬반 투표 개시.
        v1.56 — is_first(0.5초 슬롯)인데 LLM이 느려 6초 안전망(_defense_fallback)이
        이미 대신 발화한 뒤라면, 뒤늦게 도착한 이 응답은 버린다 — 안 그러면
        "제발 재고해주세요"(안전망) 다음에 뒤늦은 진짜 변론이 또 붙어 서로 안 맞는
        말을 두 번 하는 것처럼 보인다(20초/40초 슬롯은 원래도 여러 번 말하는
        설계라 항상 그대로 반영)."""
        import threading as _th

        def worker():
            txt = None
            try:
                txt = (pl.say("[최후 변론] 당신은 처형 직전이다. 억울함을 호소하거나 반박하여 살아남아라. 2~3줄.") or "").strip()
            except Exception:
                txt = None
            if not txt:
                import time as _sleep_mod
                _sleep_mod.sleep(0.4)
                try:
                    txt = (pl.say("[최후 변론] 남은 시간이 얼마 없다. 간절히 호소하라. 2~3줄.") or "").strip()
                except Exception:
                    txt = None
            if not txt:
                persona = getattr(pl, "persona", "") or "박 진하"
                txt = (f"이건 억울한 처형이에요. 저는 마피아가 아닙니다. "
                       f"이렇게 조용해야 되는 말은 안 나온다고 봐요. 다시 한번 생각해 주세요.")

            def _enqueue():
                if is_first and getattr(self, "_defense_has_spoken", False):
                    return   # 안전망이 이미 대신 발화함 — 뒤늦은 첫 슬롯 응답은 버림
                if getattr(self, "_defense_ui_q", None) is not None:
                    self._defense_ui_q.put(("defense", pl.name, (txt or "").strip()))
            self.root.after(0, _enqueue)

        _th.Thread(target=worker, daemon=True).start()

    def _ai_defense_vote(self, pl, rr):
        try:
            txt = (pl.say(f"[찬반 투표] {self.core.defendant} 님 처형에 찬성하는가? '찬성' 또는 '반대' 한 단어로.") or "").strip()
            yes = ("찬성" in txt) or ("반대" not in txt and rr.random() < .5)
            self.core.cast_defense_vote(pl.name, yes)
        except Exception:
            pass

    def _enter_night_sequence(self):
        if not self.mafia_active:
            return
        self._tally_scheduled = False
        self._tally_in_progress = False
        self._defense_in_progress = False
        self._unlock_defense_entry()
        self.core.enter_night()
        if self._mafia_is_host():
            self._mafia_broadcast("night")
        self._set_night_theme(True)
        self.refresh_mafia_phase_label()
        self._mafia_show_splash(
            title="밤이 찾아왔습니다",
            subtitle="모두 고개를 숙여주세요…\n어둠 속에서 마피아가 눈을 뜨고 활동을 시작합니다.",
            icon="🌙",
            color="#c4b5fd",
            bg_color="#131525",
            border_color="#6366f1",
            duration_ms=1800,
            sound_type="night"
        )
        self.add_mafia_host(
            "🌙 밤이 찾아왔습니다. 마피아는 '살해 이름'을, 의사는 '구조 이름'을 "
            "게임방에 적어 주세요. (밤 행동은 30초 안에)")
        self.mafia_start_btn.configure(text="[게임 진행 중]", state="disabled")
        self.root.after(1800, self._show_night_panel)
        self.root.after(2200, self._maybe_open_mafia_room)   # 사람 마피아 동료가 있으면 비밀방 자동 생성
        self._trigger_night_actions()      # AI 마피아/의사 밤 행동 백그라운드 접수
        # 밤 카운트다운은 thread가 아닌 after 루프로 — daemon 스레드와의 경합 제거
        self._night_resolve_bg()

    def _sync_ai_alive(self):
        """v1.11 — core의 alive 정보를 PlayerAgent.alive에 동기화.
        (저번 턴에 죽은 AI가 다음 턴에 말하는 것 방지.)"""
        try:
            core_alive = set(self.core.alive_players())
            for pl in getattr(self, "ai", None) and self.ai.players or []:
                if pl.name not in core_alive:
                    pl.alive = False
        except Exception:
            pass

    def _show_night_panel(self):
        """밤 행동 대상 선택 임베디드 패널(마피아=살해, 의사=구조). 역할 아니면 미 표출."""
        me = getattr(self.engine, "name", None)
        if not me:
            return
        info = self.core.players.get(me) or {}
        # v1.61 — 원격 참가자는 core.players[me]["role"] 반영이 "start"/"hdm"
        # 패킷 도착 순서에 따라 아직 안 됐을 수 있어 self._my_mafia_role로도
        # 보강한다(둘 다 갱신하도록 손봤지만 안전망으로 유지).
        role = info.get("role") or getattr(self, "_my_mafia_role", None)
        # --- 보강(문서 기획): 경찰 밤 조사 패널 추가 — 기존 마피아/의사 분기 유지 ---
        if role == "police" and info.get("alive", True):
            already = [t for t in getattr(self.core, "police_invest", {})]
            body = self._mafia_overlay_open("🕵 경찰 — 누구를 조사?", w=350, h=None)
            row = tk.Frame(body, bg="#1f2937"); row.pack(fill="x", padx=18, pady=10)
            cand_names = [n for n in self.core.alive_players() if n != me]

            def _mk_police_btn(parent, n):
                chosen = self.core.police_invest.get(n)
                txt = f"{n} ({'마피아' if chosen == 'mafia' else '시민'})" if chosen else n
                state_val = "disabled" if chosen else "normal"
                bg_val = "#374151" if not chosen else "#1f2937"
                fg_val = "white" if not chosen else "#6b7280"
                return emoji_render.make_pill_button(
                    parent, txt, lambda nn=n: self._apply_night_pick(nn, "police"),
                    bg=bg_val, fg=fg_val, hover_bg="#4b5563" if not chosen else None,
                    font_path=emoji_render.FONT_PATH_REGULAR, font_size=9,
                    radius=8, pad_x=10, pad_y=4, min_w=90, state=state_val
                )
            self._grid_candidates_centered(row, cand_names, _mk_police_btn, padx=4, pady=4)
            if already:
                tk.Label(body, text=f"이미 조사: {', '.join(already) or '없음'}",
                         fg="#6b7280", bg="#1f2937", font=FONT_SM).pack(pady=(0, 8))
            self._start_night_pick_countdown(body)
            return
        if role not in ("mafia", "doctor") or not info.get("alive", True):
            return
        act = "살해" if role == "mafia" else "구조"
        body = self._mafia_overlay_open(("🔪 마피아 — 누구를" if role == "mafia" else "💉 의사 — 누구를 ") + act + "?", w=350, h=None)
        row = tk.Frame(body, bg="#1f2937"); row.pack(fill="x", padx=18, pady=10)
        mafia_names = set(self.core.mafias()) if role == "mafia" else set()
        cands = [n for n in self.core.alive_players() if not (role == "mafia" and n in mafia_names)]

        def _mk_night_btn(parent, n):
            lbl = f"{n} (나)" if n == me else n
            return emoji_render.make_pill_button(
                parent, lbl, lambda nn=n, r=role: self._apply_night_pick(nn, r),
                bg="#374151", fg="white", hover_bg="#4b5563",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=9,
                radius=8, pad_x=10, pad_y=4, min_w=90
            )
        self._grid_candidates_centered(row, cands, _mk_night_btn, padx=4, pady=4)
        self._start_night_pick_countdown(body)

    def _start_night_pick_countdown(self, body):
        """마피아/의사/경찰 밤 행동 선택 패널 공통 카운트다운 — 15초 안에 안
        고르면 패널만 닫고 안내한다. '아무 행동도 안 한 걸로 처리'는 이미 밤
        30초 전체 타이머(core.resolve_night)가 늘 그렇게 해왔다(선택 안 하면
        애초에 core에 반영될 게 없음) — 여기서는 시간이 지났다고 알려주고
        화면에 무의미하게 남아있는 패널만 정리할 뿐, 개표/개행 로직을 새로
        만들지 않는다(기존 30초 밤 타이머와 이중으로 판정하면 충돌 위험)."""
        lbl_nt = tk.Label(body, text=f"⏳ {NIGHT_ACTION_WINDOW}초 이내에 선택하세요",
                          fg="#9ca3af", bg="#1f2937", font=FONT_SM)
        lbl_nt.pack(pady=(0, 8))
        emoji_render.apply(lbl_nt, FONT_SM)
        state = {"n": NIGHT_ACTION_WINDOW}

        def _tick():
            self._night_pick_tick = None
            if not getattr(self, "_mafia_overlay", None):
                return
            state["n"] -= 1
            if state["n"] <= 0:
                self._ghost_dm("⏰ 시간 초과 — 이번 밤은 행동하지 않은 걸로 처리됩니다")
                try:
                    self._mafia_overlay_close()
                except Exception:
                    pass
                return
            try:
                lbl_nt.config(text=f"⏳ {state['n']}초 이내에 선택하세요")
            except Exception:
                return
            self._night_pick_tick = self.root.after(1000, _tick)

        self._night_pick_tick = self.root.after(1000, _tick)
        self._wrap_overlay_close_with(self._cancel_night_pick_tick)

    def _cancel_night_pick_tick(self):
        t = getattr(self, "_night_pick_tick", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
            self._night_pick_tick = None

    def _apply_night_pick(self, name, role):
        me = getattr(self.engine, "name", None)
        if self._mafia_is_host():
            ok = self._night_action_apply(me, role, name, self._ghost_dm)
            if ok:
                self._mafia_overlay_close()
            # v1.29 — 실패(의사 연속보호 등)면 패널을 닫지 않고 유지: 유저가
            # 즉시 다른 대상을 다시 클릭할 수 있게.
        else:
            # v1.61 — 복수 인간 플레이: 클라이언트는 자기 로컬 core만 봐서는
            # 유효성(동료 마피아 여부 등, 남의 역할은 비밀이라 모름)을 정확히
            # 검증할 수 없다 — 호스트에게 보내고 결과는 개인 쪽지로 받는다.
            # 낙관적으로 패널은 바로 닫고(호스트가 거절하면 이번 밤은 행동을
            # 못 한 것으로 남을 뿐 — 다음 밤에 다시 시도 가능), 접수 안내만
            # 즉시 로컬에 띄운다.
            self._ghost_dm("⏳ 밤 행동을 호스트에게 전달했습니다 — 처리 결과는 곧 알려드립니다.")
            self._mafia_send_to_host("night_action", actor=me, role=role, target=name)
            self._mafia_overlay_close()

    def _night_action_apply(self, actor, role, target, notify):
        """호스트 전용 — 밤 행동(살해/조사/치료)을 실제 권위 core에 반영한다.
        notify(text)로 결과를 알린다(로컬 클릭이면 _ghost_dm, 원격 참가자면
        개인 쪽지 콜백). 반영 성공 여부를 반환."""
        if role == "mafia":
            if self.core.set_night_target(target):
                self.core.mafia_night_vote(actor, target)
                notify(f"🔪 ({actor} 마피아 신청) {target} 살해 지시 접수 — 밤이 끝나면 공개됩니다")
                return True
            notify(f"⚠ ({actor} 마피아) {target}은(는) 마피아 동료이거나 지목할 수 없습니다")
            return False
        if role == "police":
            # --- 보강: 경찰 조사. 결과는 경찰에게만 쪽지로(문서 — 전원 비공개) ---
            res = self.core.police_investigate(target)
            if res == "mafia":
                notify(f"🕵 [조사 결과 — 나에게만 보임] {target}님은 마피아입니다!")
            elif res == "citizen":
                notify(f"🕵 [조사 결과 — 나에게만 보임] {target}님은 마피아가 아닙니다.")
            else:
                notify(f"🕵 ({actor} 경찰 신청) {target} 조사 접수 — 아침에 결과 통보")
            return True
        # --- 보강: 의사 연속 보호 금지 검증 ---
        ok = self.core.doctor_protect(target)
        if ok:
            desc = f"{target} (자신)" if target == actor else target
            notify(f"💉 ({actor} 의사 신청) {desc} 구조 지시 접수")
            return True
        notify(f"⚠ ({actor} 의사) {target}은(는) 어제 밤에 이미 보호했습니다 — 연속 보호 금지! 다른 대상 선택")
        return False

    def _host_receive_night_action(self, actor, role, target):
        """v1.61 — 호스트 전용: 원격 참가자가 보낸 밤 행동을 실제 core에
        반영하고, 결과(성공/거절)를 그 참가자에게만 개인 쪽지로 회신한다."""
        if not self._mafia_is_host() or not actor or not target:
            return
        if not (self.core.players.get(actor) or {}).get("alive", True):
            return

        def _tell(text):
            if actor == getattr(self.engine, "name", None):
                self._ghost_dm(text)
            else:
                self._mafia_send_private(actor, "hdm", target=actor, text=text)

        self._night_action_apply(actor, role, target, _tell)

    def _ghost_dm(self, text):
        """나에게만 보이는 사회자 쪽지(기존 host_dm UI 재사용 — 없는 것이면 시스템 라인).
        경찰 조사 결과 같은 비밀 정보는 절대 전체 채팅에 노출 금지(문서 4-2.5).
        ※ 사실 구현: add_mafia_system은 채팅방 전원에게 보이므로, 폐쇄망 문서 기준
        '나에게만' 노출은 host_dm이 있어야 한다.mafia_ui에 host_dm이 존재하면 사용."""
        if getattr(self, "add_mafia_host_dm", None):
            self.add_mafia_host_dm(text)
        else:
            self.add_mafia_system(text)  # 폴백(비밀 DM 없는 환경)

    def _night_resolve_bg(self):
        """밤 30초 카운트다운 — 느린 time.sleep 대신 main-thread after 루프로 계산.

        이 스레드는 잠만 잔다. Tk 컨트롤은 전부 after 체인으로 main-thread에서 진행하므로
        재시작 대기 등의 이슈를 제거한다."""
        import mafia_config as _cfg
        self._night_tick_sec = int(getattr(_cfg, "NIGHT_SOLVE_SECONDS", NIGHT_SOLVE_SECONDS))
        self._tick_night_loop()

    def _tick_night_loop(self):
        if not self.mafia_active or self.core.phase != Phase.NIGHT:
            return
        sec = getattr(self, "_night_tick_sec", 0)
        self._set_night_count(max(0, sec))
        if sec in (10, 5):
            if sec == 5:
                self.add_mafia_system("🌙 밤이 곧 끝납니다…")
            elif sec == 10:
                self.add_mafia_system("🌙 밤 10초 남았습니다")
        # v1.08: 밤 무료 채팅 2건(무해 잡담 — 역할 노출 없음)
        if sec == 20:
            self._ai_night_chatter(2)
        elif sec == 11:
            self._ai_night_chatter(1)
        if sec <= 0:
            # --- 보강: 문서 기획 Grace Period 1.5초(네트워크 지연 대비) ---
            import mafia_config as _cfg
            grace_ms = int(getattr(_cfg, "NIGHT_GRACE_SECONDS", 1.5) * 1000)
            self._night_tick = self.root.after(grace_ms, self._night_grace_resolve)
            return
        self._night_tick_sec = sec - 1
        self._night_tick = self.root.after(1000, self._tick_night_loop)

    def _night_grace_resolve(self):
        if not self.mafia_active or self.core.phase != Phase.NIGHT:
            return
        self._reconcile_mafia_night()
        died, victim = self.core.resolve_night()
        self._after_night(died, victim)

    def _reconcile_mafia_night(self):
        """v1.61 — 마피아 팀 공모 조율(호스트 전용). 코어는 마피아 전원의 지목이
        정확히 같아야 살해를 인정하는데, AI 마피아는 밤 시작 때 각자 무작위로
        골라 두므로 마피아가 둘 이상이면 거의 항상 갈려 살해가 무효였다.
        규칙: ① 사람 마피아가 고른 게 있으면 그중 가장 먼저 고른 대상이 팀 결정,
        ② 없으면 AI들이 고른 것 중 최다(동률이면 그중 무작위)로 AI 전원 통일.
        결정된 대상은 모든 생존 마피아의 지목으로 기록하고 사람 마피아에게 알린다."""
        import random as _rr
        core = self.core
        with core.lock:
            mafias = [n for n in core.mafias()]
            if len(mafias) < 2:
                return
            valid = lambda t: bool(t) and core.players.get(t, {}).get("alive") and t not in mafias
            humans = [n for n in mafias if not core.players[n].get("is_ai")]
            human_picks = [core.night_targets[h] for h in humans
                           if h in core.night_targets and valid(core.night_targets[h])]
            if human_picks:
                team = human_picks[0]
                who = "사람 마피아의 먼저 고른 선택"
            else:
                picks = [t for m, t in core.night_targets.items() if m in mafias and valid(t)]
                if not picks:
                    return
                top = max(picks.count(t) for t in set(picks))
                team = _rr.choice([t for t in set(picks) if picks.count(t) == top])
                who = "AI 마피아의 합의"
            for m in mafias:
                core.night_targets[m] = team
            core.night_target = team
        for h in humans:
            msg = f"🔪 마피아 팀 최종 결정: {team} ({who})"
            if h == getattr(self.engine, "name", None):
                self._ghost_dm(msg)
            else:
                self._mafia_send_private(h, "hdm", target=h, text=msg)

    # 밤 잡담용 분위기 씨앗 — 매번 다른 방향으로 말하게 해서 같은 말 반복을 막는다.
    _NIGHT_MOODS = (
        "밤이 조용해서 괜히 긴장되거나 무서운 기분",
        "졸리거나 피곤한데 잠들면 안 될 것 같은 기분",
        "낮에 나온 이야기 중 마음에 걸렸던 장면을 혼잣말로 되짚기(특정인을 확정적으로 지목하지는 말 것)",
        "누가 오늘 밤 당할지 불안해하며 주변 분위기를 살피기",
        "밤에 들리는 소리나 방 안의 공기를 농담처럼 묘사하기",
        "내일 낮에 어떻게 토론해야 할지 은근히 다짐하기(전략·역할은 절대 노출 금지)",
        "다른 참가자에게 가볍게 말 걸기(이름을 부르며 안부나 농담)",
    )
    # LLM을 못 쓸 때(서버 미설정/오류)만 쓰는 예비 문구 — 예전엔 이것 6개가 전부였다.
    _NIGHT_FALLBACK_LINES = (
        "밤이라 좀 무섭다 야", "여기 방 분위기 완전 싸늘해", "다들 잠은 자고 왔어?",
        "왠지 오늘 밤 누가 사라질 것 같은 느낌이…", "조용하니까 더 불안하네", "눈 감으면 안 될 것 같아 ㅋㅋ",
        "아 밖에서 무슨 소리 난 것 같은데", "내일 낮엔 꼭 마피아 찾아야지…", "다들 살아서 아침에 보자",
        "이 시간이 제일 길게 느껴져", "소름 돋았어 방금", "낮에 그 말이 자꾸 생각나네",
        "오늘 밤은 제발 평화롭게…", "긴장돼서 손에 땀나 ㅠㅠ", "누가 마피아일까 계속 생각 중이야",
        "커튼 뒤에 누가 있는 것 같아 ㅋㅋㅋ", "심장 소리 크게 들리는 거 나만 그래?",
    )

    def _ai_night_chatter(self, count=2):
        """밤에도 살아있는 AI 몇 명의 무해한 잡담(역할·전략 노출 금지) — '밤에 AI 채팅 안 침' 해소.
        v1.61 — 고정 문구 6개를 돌려쓰던 것을, AI별 성격과 대화 기억을 반영한 LLM 발언으로
        교체했다(LLM을 못 쓰면 늘려 둔 예비 문구에서 겹치지 않게 뽑는다)."""
        import random as _rr
        live = [pl for pl in self.ai.players
                if pl.alive and getattr(pl, "booted", False)]
        if not live:
            return
        _rr.shuffle(live)
        for i, pl in enumerate(live[:count]):
            mood = _rr.choice(self._NIGHT_MOODS)
            threading.Thread(target=self._night_chatter_worker, args=(pl, mood, i), daemon=True).start()
        # 유저(사람)는 침묵 — 사람이 쓰지 않으면 잡담도 없이 조용.

    def _night_chatter_worker(self, pl, mood, order):
        try:
            others = [n for n in self.core.alive_players() if n != pl.name]
        except Exception:
            others = []
        names_rule = (f"이 방의 다른 참가자는 {', '.join(others)} 뿐입니다 — 이름을 부를 땐 이 중에서만, 없는 이름을 지어내지 마세요. "
                      if others else "")
        prompt = (names_rule + "[사회자] 지금은 밤입니다. 마피아·의사·경찰이 몰래 행동하는 중이고 다들 채팅으로 잡담만 할 수 있습니다. "
                  "당신의 성격대로, 이런 분위기로 딱 한 문장만 말하세요: " + mood + ". "
                  "규칙: 자기 역할/정체나 밤 행동, 특정인을 마피아라고 단정하는 말은 절대 금지. 한국어 구어체만. "
                  "앞서 다른 사람이 한 말과 다르게, 자연스럽고 짧게.")
        text = None
        try:
            text = pl.say(prompt)
        except Exception:
            text = None
        text = (text or "").strip().strip('"').strip("'")
        if not text or len(text) > 120:
            text = self._pick_night_fallback()

        def _post():
            # 그새 아침이 됐거나 게임이 끝났으면 밤 잡담을 올리지 않는다.
            if not self.mafia_active or self.core.phase != Phase.NIGHT or not pl.alive:
                return
            self.add_mafia_ai(pl.name, text)
            if getattr(self, "ai", None):
                self.ai.observe_all(pl.name, text)
        self.root.after(int(400 + order * 900), _post)

    def _pick_night_fallback(self):
        import random as _rr
        used = getattr(self, "_night_fallback_used", None)
        if used is None:
            used = self._night_fallback_used = set()
        pool = [l for l in self._NIGHT_FALLBACK_LINES if l not in used]
        if not pool:
            used.clear()
            pool = list(self._NIGHT_FALLBACK_LINES)
        line = _rr.choice(pool)
        used.add(line)
        return line

    def _set_night_count(self, sec):
        try:
            self.mafia_phase_lbl.config(
                text=f"🌙 밤 {self.core.day_no} · 마피아/의사 행동 대기 — {sec}초")
        except Exception:
            pass

    def _after_night(self, died, victim):
        if not self.mafia_active:
            return
        self._mafia_room_close()
        self._set_night_theme(False)
        self._sync_ai_alive()   # v1.11 — 밤사망 AI 즉시 발화 차단
        if died:
            role2 = self.core.reveal_role(victim)
            if self._mafia_is_host():
                self._mafia_broadcast("day", victim=victim, role=role2)
            self.add_mafia_system(f"🕯 밤 사망 — {victim}")
            self.add_mafia_host(
                f"아침이 밝았습니다… 유감스럽게도 '{victim}' 님의 자리가 비었습니다.")
            if role2:
                self.add_mafia_system(f"🎭 직업 공개 — {victim} ({_role_kr(role2)}였습니다)")
            self._mafia_show_splash(
                title=f"간밤의 비극 — '{victim}' 사망",
                subtitle=f"마피아의 잔혹한 습격으로 '{victim}' 님이 사망했습니다.\n🎭 정체: [{_role_kr(role2)}]",
                icon="🕯",
                color="#f87171",
                bg_color="#3b0d0d",
                border_color="#ef4444",
                duration_ms=2500,
                sound_type="trial"
            )
            if victim == getattr(self.engine, "name", None):
                self._open_ghost_chat()
        else:
            if self._mafia_is_host():
                self._mafia_broadcast("day", victim=None, role=None)
            self.add_mafia_host("아침이 밝았습니다. 오늘 밤은 희생자가 없었습니다. 의사 덕분일지도 모릅니다.")
            self._mafia_show_splash(
                title="새로운 아침이 밝았습니다",
                subtitle="의사의 신속한 치료로 오늘 밤은 아무도 희생되지 않았습니다!\n평화로운 아침 토론을 시작하세요.",
                icon="☀",
                color="#fde047",
                bg_color="#2b2308",
                border_color="#eab308",
                duration_ms=2000,
                sound_type="day"
            )
        # --- 보강: 경찰 조사 결과 아침 개인 통보(문서 — 본인에게만 비공개 DM) ---
        rep = getattr(self.core, "police_report", None)
        if rep:
            victim_t, res = rep
            verdict = "마피아입니다!" if res == "mafia" else "마피아가 아닙니다."
            me = getattr(self.engine, "name", None)
            pl_police = next((pl for pl in getattr(self, "ai", None) and self.ai.players or []
                              if pl.role == "police"), None)
            if self.core.players.get(me, {}).get("role") == "police":
                self._ghost_dm(f"🕵 [밤 조사 결과 — 나에게만] {victim_t}님은 {verdict}")
            elif pl_police:
                # AI 경찰 — 기억에 적립(발화 참조용, 노출 금지 지시 포함)
                pl_police.memory.append(
                    {"role": "user",
                     "content": f"[사회자 밤 비밀 통보 — 채팅 노출 금지] "
                                f"조사 결과: {victim_t} = {verdict}"})
            self.core.police_report = None
        winner = self.core.check_winner()
        if winner:
            self._on_game_end(winner)
            return
        self.refresh_mafia_phase_label()
        self.start_day_timer()
        self._trigger_ai_reactions(
            context="밤이 지나 아침이 다시 밝았습니다.",
            min_interval=0,
            prefix="[게임 상황] ")

    # ==================== 게임 종료 ====================
    def _on_game_end(self, winner):
        self._mafia_room_close()
        if self._mafia_is_host():
            self._mafia_broadcast("end", winner=winner, roles={
                n: p.get("role") for n, p in self.core.players.items()})
        self._cancel_mafia_timer()
        self.core.phase = Phase.END
        label = "시민" if winner == "citizen" else "마피아"
        emoji = "🎉" if winner == "citizen" else "🩸"
        self._play_mafia_sound("citizen_win" if winner == "citizen" else "mafia_win")
        self.add_mafia_system(f"⚖ 게임 종료 — {label} 팀 승리!")
        # 정체 공개
        role_names = ROLE_LABEL_KR
        reveals = ", ".join(
            f"{n}({role_names.get(p['role'], '?')})"
            for n, p in self.core.players.items())
        self.add_mafia_system(f"🎭 정체 공개 — {reveals}")
        self.add_mafia_host(
            f"{emoji} {label} 팀이 승리했습니다. 다들 수고하셨습니다. "
            "다시 시작하려면 [게임 시작]을 눌러 주세요.")
        self.ai.say_async(lambda pl: (
            "[게임 종료] 사회자가 승자를 발표했습니다. 당신 역할과 승패는 사회자가 별도 안내했습니다. "
            "진 심정이 담긴 마무리 한마디를 하세요 (역할명은 말해도 됨)."))
        self.mafia_active = False
        self._recruiting = False
        self._recruited_humans = []
        self._my_joined = False
        self._my_mafia_role = None
        self._unlock_defense_entry()
        self._set_night_theme(False)
        if hasattr(self, "mafia_role_btn"):
            self.mafia_role_btn.pack_forget()
        self.mafia_start_btn.configure(text="📢 참가자 모집", bg="#b91c1c", activebackground="#7f1d1d", state="normal")
        if hasattr(self, "mafia_cancel_recruit_btn"):
            self.mafia_cancel_recruit_btn.pack_forget()
        if hasattr(self, "mafia_join_btn"):
            self.mafia_join_btn.pack_forget()
        self.refresh_mafia_phase_label()

    def refresh_mafia_phase_label(self):
        if not getattr(self, "mafia_phase_lbl", None):
            return
        ph = self.core.phase
        if ph == Phase.LOBBY:
            txt = "로비 — [게임 시작]을 누르면 AI 참가자가 배정됩니다"
        elif ph == Phase.DAY:
            txt = f"☀ 낮 {self.core.day_no} · 토론 중 — 자유롭게 토론하세요"
        elif ph == Phase.NIGHT:
            txt = f"🌙 밤 {self.core.day_no} · 마피아/의사 행동 대기"
        elif ph == Phase.VOTE:
            txt = "🗳 개표 중…"
        else:
            txt = "⚖ 게임 종료 — 재시작 가능"

        # 내 직업 정보가 있으면 상태 라벨 끝에 붙여 상시 인지 가능하게 함
        my_role = getattr(self, "_my_mafia_role", None)
        if self.mafia_active and my_role:
            r_info = {"mafia": "🔪 마피아", "doctor": "💉 의사", "police": "🕵 경찰", "citizen": "🧑‍🌾 시민"}.get(my_role, my_role)
            txt += f" | 내 직업: {r_info}"

        try:
            self.mafia_phase_lbl.config(text=txt)
        except Exception:
            pass
        # 헤더 부제도 같이 갱신(상태 불일치 제거)
        try:
            self.ch_sub.config(text=txt, fg="#c4b5fd")
        except Exception:
            pass
        # 하단 상태 표시줄도 같이 갱신 — 방 진입 시 안내문이 토론/개표 등으로
        # 넘어간 뒤에도 그대로 남아있던 문제 수정
        if self.current == self.mafia_room_key():
            try:
                self.status.set(txt)
            except Exception:
                pass

    # ==================== 발풍선 기록 + 렌더 ====================
    def add_mafia_bubble(self, text, label, mine=False):
        rec = {"kind": "text", "mine": mine, "label": label, "ts": time.time(),
               "text": text, "is_system": False}
        self.mafia_history.append(rec)
        # v1.12 — 유저/AI 발화 시점 갱신 (침묵 감지가 이 기준으로 동작)
        try:
            self._last_any_talk_ts = time.time()
        except Exception:
            pass
        if self._is_mafia_room_active():
            self._hide_empty()
            self._mafia_append_live(rec)

    def add_mafia_system(self, text):
        # 랜톡 chat_renderer._draw_record와 호환: is_system=True면 시스템 구분선으로 그려짐
        rec = {"kind": "system", "is_system": True, "ts": time.time(), "text": text}
        self.mafia_history.append(rec)
        if self._is_mafia_room_active():
            self._hide_empty()
            self._mafia_append_live(rec)
        if getattr(self, "mafia_host_mode", False) and self.mafia_active:
            self._mafia_broadcast("sys", text=text)

    def add_mafia_host(self, text):
        self.add_mafia_bubble(text, "🖥 사회자")
        if getattr(self, "mafia_host_mode", False) and self.mafia_active:
            self._mafia_broadcast("hsay", text=text, host="🖥 사회자")
        if getattr(self, "ai", None) and self.mafia_active:
            self.ai.observe_all("사회자", text)

    def add_mafia_host_dm(self, text):
        """사회자 → 나 개인 쪽지(게임방 공개 아님). 자물쇠 표시로 그린다."""
        rec = {"kind": "host_dm", "mine": False, "label": "🖥 사회자", "ts": time.time(),
               "text": text, "is_system": False, "private": True}
        self.mafia_history.append(rec)
        if self._is_mafia_room_active():
            self._hide_empty()
            self._mafia_append_live(rec)

    def add_mafia_ai(self, name, text):
        self.add_mafia_bubble(text, name)
        if getattr(self, "mafia_host_mode", False) and self.mafia_active:
            self._mafia_broadcast("asay", name=name, text=text)

    def _is_mafia_room_active(self):
        return (self.current and self.current[0] == "mgame" and self.mafia_active)

    def _on_ai_utt(self, name, color, text):
        self.root.after(0, lambda: self.add_mafia_ai(name, text))
        # 다른 AI들도 이 발언을 기억(대화 맥락 유지)
        if getattr(self, "ai", None):
            self.ai.observe_all(name, text)
        # 티키타카: AI가 말하면 자연스러운 간격(2.5~4.5초)으로 다른 AI 1명만 최대 2회 연쇄
        if self.mafia_active and self.core.phase == Phase.DAY:
            chain_cnt = getattr(self, "_ai_chain_count", 0)
            if chain_cnt < 2 and random_mod.random() < 0.45:
                self._ai_chain_count = chain_cnt + 1
                delay = random_mod.randint(2500, 4500)
                self.root.after(delay, lambda: self._trigger_ai_chain_replier(
                    exclude=name, context=f'AI "{name}"의 발언: "{(text or "")[:120]}"'))

    # ==================== 종료 정리 ====================
    def mafia_shutdown(self):
        try:
            self.ai.stop_all()
        except Exception:
            pass
        self._cancel_mafia_timer()