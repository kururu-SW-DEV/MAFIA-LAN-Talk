# -*- coding: utf-8 -*-
"""mafia_ui_view.py — 마피아 게임방 UI 믹스인 (게임방 화면·연출: 아바타 색, 오버레이/팝업, 시네마틱, 말풍선 기록·렌더).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaViewMixin:
    """게임방 화면·연출: 아바타 색, 오버레이/팝업, 시네마틱, 말풍선 기록·렌더"""

    def _avacolor(self, name):
        """랜톡 _avacolor 위임 — 사회자는 고정 보라, AI 페르소나는 전용 색."""
        label = str(name or "")
        base = None
        try:
            base = self._super_avacolor(name)
        except Exception:
            base = None
        if label.startswith("🖥") or "사회자" in label:
            return M_HOST
        distinct = self._ai_distinct_color(label)
        if distinct:
            return distinct
        if label in M_AI_COLORS:
            return M_AI_COLORS[label]
        return base or "#94a3b8"

    # 색상환에서 멀리 떨어진 10색(사회자 보라와 겹치는 보라는 맨 끝) — AI끼리 프로필 색이 겹치거나 비슷해 헷갈리지 않게 한 판 안에서 하나씩 배정
    AI_DISTINCT_COLORS = ("#ef4444", "#f97316", "#eab308", "#84cc16", "#14b8a6",
                          "#3b82f6", "#ec4899", "#a16207", "#94a3b8", "#8b5cf6")

    def _ai_distinct_color(self, label):
        """게임 중 AI 참가자에게 서로 다른 색을 준다. 이름 정렬 순서로 배정하므로 모든 참가자 화면에서
        같은 색이 되고(명단은 전원이 같다), 게임 밖이거나 AI가 아니면 None."""
        core = getattr(self, "core", None)
        if not core or not getattr(self, "mafia_active", False):
            return None
        try:
            with core.lock:
                if not (core.players.get(label) or {}).get("is_ai"):
                    return None
                ais = sorted(n for n, p in core.players.items() if p.get("is_ai"))
            return self.AI_DISTINCT_COLORS[ais.index(label) % len(self.AI_DISTINCT_COLORS)]
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
            return None

    def _super_avacolor(self, name):
        # 앱의 원래 _avacolor와 동일한 CRC 로직(C_AVA 팔레트)을 따라간다
        import zlib
        from constants import C_AVA
        s = (name or "?").encode("utf-8", "replace")
        return C_AVA[zlib.crc32(s) % len(C_AVA)]

    def mafia_room_key(self):
        return ("mgame",)

    # ── 임베디드 팝업(Toplevel 대신 메인창 내부 오버레이) ──
    def _mafia_overlay_open(self, title_text, w=380, h=None):
        """팝업 — 앱 표준 임베드 다이얼로그(dialogs.py _make_embed_dialog) 재사용.
        별도 OS 창을 만들지 않고 본창 중앙 오버레이 위에 패널로 뜬다."""
        prev = getattr(self, "_mafia_overlay_close", None)
        if callable(prev):
            try: prev()
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        panel, body, close = self._make_embed_dialog(title_text, w, h)
        def _on_close():
            self._mafia_overlay = None
            self._mafia_overlay_cleanup()
            close()
        self._mafia_overlay = panel
        self._mafia_overlay_close = _on_close
        return body

    def _mafia_overlay_cleanup(self):
        """오버레이가 닫힐 때 유령방 UI 참조를 끊는다(닫힌 위젯을 계속 만지면 TclError, 300ms 폴러도 안 멈춤)."""
        self._ghost_ui_open = False
        self._ghost_list = None
        self._ghost_roster = None
        self._ghost_ent = None

    def _mafia_overlay_close(self, *a):
        f = getattr(self, "_mafia_overlay", None)
        if f is not None and f.winfo_exists():
            f.destroy()
        self._mafia_overlay = None
        # v1.17 — 유령방 UI 큐 폴러 정지(유령방 닫힘)
        self._mafia_overlay_cleanup()

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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        try:
            w, h = 1400, 800
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)

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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)

    def _set_night_theme(self, is_night):
        """밤/낮에 따라 대화 캔버스 및 상단 바 배경 분위기 전환."""
        bg_chat = "#0c101a" if is_night else C_MAIN
        bg_bar = "#111827" if is_night else C_CARD
        if hasattr(self, "chat"):
            try: self.chat.configure(bg=bg_chat)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        if hasattr(self, "chat_wrap"):
            try: self.chat_wrap.configure(bg=bg_chat)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        if hasattr(self, "mafia_bar") and not getattr(self, "_defense_in_progress", False):
            try: self.mafia_bar.configure(bg=bg_bar)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            # mafia_phase_lbl은 mafia_bar의 자식이지만 자기 bg를 따로 갖고 있어서
            # (실측 지적: 밤이 되면 게임바는 어두워지는데 이 라벨만 예전 밝은
            # 카드색 네모가 그대로 남아 있었음) 부모와 같이 맞춰줘야 한다.
            if hasattr(self, "mafia_phase_lbl"):
                try: self.mafia_phase_lbl.configure(bg=bg_bar)
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._defense_ticker = None
        if hasattr(self, "entry"):
            try: self.entry.configure(state="normal")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        if hasattr(self, "send_btn"):
            try: self.send_btn.configure(state="normal")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        if hasattr(self, "mafia_bar"):
            try: self.mafia_bar.configure(bg=C_CARD)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._refresh_mafia_bar_pill_bg()
        if hasattr(self, "mafia_phase_lbl"):
            try: self.mafia_phase_lbl.configure(bg=C_CARD, fg="#c4b5fd")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
            self._mafia_pack_lobby_buttons()      # 로비: [참가 신청]을 [참가자 모집] 왼쪽에 항상 표시
        self.mafia_bar.pack(fill="x", before=self.chat_wrap)
        self._refresh_mafia_roster()
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

    def _mafia_pump_list_inject(self, items):
        """_refresh_list의 items에 (mgame,)을 맨 위로 삽입(App._refresh_list에서 호출)."""
        items.insert(0, {"key": self.mafia_room_key(),
                          "kind": "mgame", "name": GAME_ROOM_NAME,
                          "online": True, "game_room": True})

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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            return prev_close(*a)
        self._mafia_overlay_close = _wrapped

    def _set_night_count(self, sec):
        try:
            self.mafia_phase_lbl.config(
                text=f"🌙 밤 {self.core.day_no} · 마피아/의사 행동 대기 — {sec}초")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _mafia_roster_text(self):
        """생존/사망 현황 문구(직업은 밝히지 않는다). 게임 중이 아니면 빈 문자열."""
        core = getattr(self, "core", None)
        if not core or not getattr(self, "mafia_active", False) or not core.players:
            return ""
        me = getattr(self.engine, "name", None)
        alive, dead = [], []
        with core.lock:
            for n, p in core.players.items():
                tag = n + (" (나)" if n == me else "") + (" 🤖" if p.get("is_ai") else "")
                (alive if p.get("alive") else dead).append(tag)
        txt = f"🟢 생존 {len(alive)}명: " + " · ".join(alive)
        if dead:
            txt += f"     💀 사망 {len(dead)}명: " + " · ".join(dead)
        return txt

    def _refresh_mafia_roster(self):
        """게임바 아래 생존/사망 현황 줄을 갱신한다(마피아방을 보고 있을 때만 표시)."""
        try:
            lbl = getattr(self, "mafia_roster_lbl", None)
            if lbl is None:
                lbl = self.mafia_roster_lbl = tk.Label(
                    self.mafia_bar.master, text="", anchor="w", justify="left",
                    fg="#e5e7eb", bg="#111827", font=FONT_SM, padx=14, pady=4)
                lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(200, e.width - 28)))
                emoji_render.apply(lbl, FONT_SM)
            self._refresh_ghost_button()
            txt = self._mafia_roster_text()
            if txt and getattr(self, "mafia_bar_is_game", False) and self.current == self.mafia_room_key():
                lbl.configure(text=txt)
                if not lbl.winfo_ismapped():
                    lbl.pack(fill="x", before=self.chat_wrap)
            else:
                lbl.pack_forget()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _refresh_ghost_button(self):
        """내가 사망했을 때 채팅 화면 우하단(맨 아래로 버튼 바로 위)에 [👻 유령방] 버튼을 띄운다. 닫힌 사이에 새 말이 오면 ●를 붙인다."""
        try:
            anchor = getattr(self, "scroll_btn", None)
            if anchor is None:
                return
            core = getattr(self, "core", None)
            me = getattr(self.engine, "name", None)
            dead = bool(core and me and getattr(self, "mafia_active", False)
                        and me in core.players and not core.players[me].get("alive", True))
            show = dead and getattr(self, "mafia_bar_is_game", False) and self.current == self.mafia_room_key()
            btn = getattr(self, "_ghost_btn", None)
            if not show:
                if btn is not None:
                    btn.place_forget()
                return
            label = "👻 유령방" + (" ●" if getattr(self, "_ghost_unread", False) else "")
            if btn is None:
                btn = self._ghost_btn = emoji_render.make_pill_button(
                    anchor.master, label, self._open_ghost_chat, bg="#6d28d9", fg="white",
                    hover_bg="#7c3aed", font_path=emoji_render.FONT_PATH_REGULAR,
                    font_size=POPUP_BTN_PX, radius=14, pad_x=14, pad_y=6)
                self._ghost_btn_label = label
            elif getattr(self, "_ghost_btn_label", None) != label:
                btn.config(text=label)
                self._ghost_btn_label = label
            btn.place(in_=self.chat, relx=1.0, rely=1.0, x=-16, y=-64, anchor="se")
            btn.lift()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _mafia_roster_tick(self):
        """2초마다 현황을 갱신(사망·접속 끊김 등 어떤 경로로 바뀌어도 반영). 게임 중에만 돈다."""
        self._roster_tick_id = None
        if not getattr(self, "mafia_active", False):
            self._refresh_mafia_roster()
            return
        self._refresh_mafia_roster()
        self._roster_tick_id = self.root.after(2000, self._mafia_roster_tick)

    def refresh_mafia_phase_label(self):
        if getattr(self, "mafia_active", False) and getattr(self, "_roster_tick_id", None) is None:
            self._roster_tick_id = self.root.after(0, self._mafia_roster_tick)
        else:
            self._refresh_mafia_roster()
        if not getattr(self, "mafia_phase_lbl", None):
            return
        ph = self.core.phase
        if ph == Phase.LOBBY:
            txt = "로비 — [게임 시작]을 누르면 AI 참가자가 배정됩니다"
        elif ph == Phase.DAY and getattr(self, "_vote_window", False):
            txt = "🗳 개표 중 — 투표를 진행하세요"      # 투표 창이 열린 동안(core는 표를 받기 위해 DAY를 유지한다)
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        # 헤더 부제도 같이 갱신(상태 불일치 제거)
        try:
            self.ch_sub.config(text=txt, fg="#c4b5fd")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        # 하단 상태 표시줄도 같이 갱신 — 방 진입 시 안내문이 토론/개표 등으로
        # 넘어간 뒤에도 그대로 남아있던 문제 수정
        if self.current == self.mafia_room_key():
            try:
                self.status.set(txt)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)

    def add_mafia_bubble(self, text, label, mine=False):
        rec = {"kind": "text", "mine": mine, "label": label, "ts": time.time(),
               "text": text, "is_system": False}
        self.mafia_history.append(rec)
        # v1.12 — 유저/AI 발화 시점 갱신 (침묵 감지가 이 기준으로 동작)
        try:
            self._last_any_talk_ts = time.time()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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
        self._ai_observe_system(text)

    # AI가 알아야 하는 게임 결과만 골라 기억에 넣는다(진행률·접속·안내·오류 같은 잡음은 제외 — AI는 최근 12개
    # 발언만 보므로 잡음이 섞이면 정작 중요한 결과가 밀려난다). 비공개 정보(경찰 결과 등)는 시스템 줄이 아니라 개인
    # 쪽지(host_dm)로만 가므로 여기에 들어오지 않는다.
    _AI_VISIBLE_SYSTEM_PREFIXES = (
        "🕯 밤 사망", "🎭 직업 공개", "🎭 정체 공개", "⚖ 찬성", "⚖ 최후 변론", "⚖ 변론 종료", "⚖ 게임 종료",
        "🗳 유효표 없음", "🗳 최다 득표 동률", "🗳 재투표도 동률", "🗳 동률 후보", "⏰ 낮 시간 종료",
        "⏰ 찬반 투표 시간 초과", "💀")

    def _ai_observe_system(self, text):
        """호스트에서만: 게임 결과 시스템 안내(사망·처형·직업 공개·개표 결과)를 모든 AI의 기억에 넣는다.
        예전에는 사회자 발언과 채팅만 기억해서, 처형 결과(찬반 수·부결)나 공개된 직업을 AI가 몰랐다."""
        try:
            if not (getattr(self, "mafia_host_mode", False) and getattr(self, "ai", None)):
                return
            if isinstance(text, str) and text.startswith(self._AI_VISIBLE_SYSTEM_PREFIXES):
                self.ai.observe_all("시스템", text)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
