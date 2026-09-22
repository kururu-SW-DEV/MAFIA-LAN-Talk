# -*- coding: utf-8 -*-
"""mafia_ui.py — MAFIA의 게임방 UI 믹스인.

실제 구현은 기능별 믹스인 모듈로 나뉘어 있고, 여기서 하나의 MafiaUIMixin으로 합친다.
  · mafia_ui_common : import·상수·헬퍼(모듈 전역)
  · mafia_ui_view   : 화면·연출·말풍선
  · mafia_ui_net    : [MAFIA1] 송수신·송신자 검증·동기화·끊김 감시
  · mafia_ui_secret : 마피아 비밀방·유령방·대화로 정하는 살해 목표
  · mafia_ui_night  : 밤 흐름·밤 AI(LLM)
  · mafia_ui_vote   : 낮 타이머·투표·변론·처형
  · mafia_ui_ai     : 사용자 발언 처리·AI 반응
  · 이 파일         : 로비·모집·게임 시작/종료 등 남은 수명주기
"""
from mafia_ui_common import *  # noqa: F401,F403  (외부가 mafia_ui.<이름>으로 쓰던 것과 호환)
from mafia_ui_view import MafiaViewMixin
from mafia_ui_net import MafiaNetMixin
from mafia_ui_secret import MafiaSecretMixin
from mafia_ui_night import MafiaNightMixin
from mafia_ui_vote import MafiaVoteMixin
from mafia_ui_ai import MafiaAIChatMixin


class MafiaUIMixin(MafiaViewMixin, MafiaNetMixin, MafiaSecretMixin, MafiaNightMixin, MafiaVoteMixin, MafiaAIChatMixin):
    """마피아 게임방 UI 믹스인 — 위 믹스인들을 합치고 로비·게임 시작/종료를 담당한다."""

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
            mafia_config.set_overrides_file(_datadir)   # None이면 exe 옆 data 폴더
            mafia_config.load_overrides()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
            self._mafia_overlay_cleanup()
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
                self.add_mafia_system("⚙ 게임 설정 파일 저장 완료 — " + str(mafia_config._OVERRIDES_FILE))
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
        self._reset_ident()         # 새 모집 — 이전 판의 이름·접속 주소 묶음과 사칭 안내 기록을 버린다
        self._recruiting = True
        me = getattr(self.engine, "name", None) or "방장"
        self._recruiter_host = me
        self._recruited_humans = [me]
        self._my_joined = True

        self.mafia_start_btn.config(
            text="🎮 게임 시작 (인간 1명)",
            bg="#16a34a", activebackground="#15803d"
        )
        if hasattr(self, "mafia_join_btn"):
            self.mafia_join_btn.pack_forget()
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
        self._mafia_pack_lobby_buttons()

    def _mafia_pack_lobby_buttons(self):
        """로비(모집 전) 상태의 게임바 버튼 배치: [참가 신청][참가자 모집]. 참가 신청 버튼은 방장의
        모집 알림을 못 받았어도 누를 수 있게 로비에서 항상 보인다(진행 중인 판·모집 중인 방장에게는 숨김)."""
        try:
            start = getattr(self, "mafia_start_btn", None)
            join = getattr(self, "mafia_join_btn", None)
            if start is None or join is None:
                return
            join.pack_forget()
            if getattr(self, "mafia_active", False) or getattr(self, "_recruiting", False):
                return
            start.pack_forget()
            start.pack(side="right", padx=(10, 6), pady=8)
            join.config(text="🙋 참가 신청", bg="#059669", activebackground="#047857")
            join.pack(side="right", padx=(6, 0), pady=8)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _request_join_without_notice(self, me):
        """방장의 모집 알림(recruit_start)을 받지 못한 채 [참가 신청]을 눌렀을 때 — 접속 중인 모두에게
        신청을 보낸다. 모집 중인 방장이 있으면 명단에 넣고 개인 쪽지로 모집 상태를 돌려준다."""
        eng = getattr(self, "engine", None)
        try:
            with eng.plock:
                n_peers = len(eng.peers)
        except Exception:
            n_peers = 0
        if n_peers == 0:
            self.add_mafia_system("⚠ 접속 중인 상대가 없습니다 — 방장과 같은 네트워크에 있는지, 좌측 목록에 방장이 보이는지 확인하세요.")
            return
        self._mafia_broadcast("recruit_join", name=me)
        self.add_mafia_system("🙋 참가 신청을 보냈습니다. 방장이 모집 중이면 곧 명단에 추가되고 안내가 옵니다. "
                              "응답이 없으면 방장이 아직 [참가자 모집]을 누르지 않은 것입니다.")

    def mafia_toggle_join_clicked(self):
        me = getattr(self.engine, "name", None)
        if not me:
            return
        if not getattr(self, "_recruiting", False):
            self._request_join_without_notice(me)
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

    @staticmethod
    def _pick_ai_personas(need):
        """AI 참가자 인격을 인격 풀(ALL_PERSONAS)에서 무작위로 need명 뽑는다(중복 없음).
        예전에는 항상 앞에서부터 순서대로 써서 매판 똑같은 캐스팅(루카·미나·제이…)이었다."""
        need = max(0, min(int(need), len(ALL_PERSONAS)))
        return random_mod.sample(list(ALL_PERSONAS), need)

    def _launch_game_with_recruits(self):
        me = getattr(self.engine, "name", None) or "나"
        humans = list(getattr(self, "_recruited_humans", []))
        if me not in humans:
            humans.insert(0, me)

        if len(humans) >= MAX_PLAYERS:
            # 사람이 최대 인원(10명)을 채우면 AI 자리(최소 1명)가 없다 — 예전엔 검사가 없어 11명 이상이어도
            # 그냥 시작됐다. 이 검사는 반드시 '상태를 바꾸기 전에' 해야 한다: 예전 수정은 모집 종료·버튼
            # 숨김을 먼저 해 놓고 거절해서, 거절된 뒤 모집이 이미 끝나 있었다. 지금은 모집을 그대로 둔다.
            self.add_mafia_system(
                f"⚠ 게임 시작 실패 — 참가자가 너무 많습니다(사람 {len(humans)}명). "
                f"사람은 최대 {MAX_PLAYERS - 1}명까지이고 AI가 최소 1명 함께합니다.")
            return

        self._recruiting = False
        if hasattr(self, "mafia_cancel_recruit_btn"):
            self.mafia_cancel_recruit_btn.pack_forget()
        if hasattr(self, "mafia_join_btn"):
            self.mafia_join_btn.pack_forget()

        with self.core.lock:
            self.core.lobby_reset()
            # 오직 참가 신청한 실제 인간들만 join! (강제 납치 제거)
            for nm in humans:
                self.core.join(nm, is_ai=False)

            ai_count = max(1, getattr(self, "mafia_ai_count", 4))
            need = max(ai_count, MIN_PLAYERS - len(self.core.players))
            need = min(need, len(ALL_PERSONAS), MAX_PLAYERS - len(humans))   # 총원 최대 10명
            chosen_personas = self._pick_ai_personas(need)
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
        self._mafia_disconnect_strikes = {}  # v1.87 — 연속 누락 횟수도 새 판마다 초기화
        self._mafia_start_disconnect_watch()

        self._police_claims = {}
        self._reset_ghost_state()
        self._doctor_claims = {}
        self._bluff_count = 0
        self.ai.assign_roles(assigned, self.core, self._claims_for)
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
                # 참가자별 비밀 토큰 — 이후 투표·밤 행동에 실어 보내야 호스트가 받는다(같은 PC·같은
                # 공유기 뒤에서 이름·포트만 흉내 낸 위조 패킷을 막는다). 개인 전송으로만 전달된다.
                import secrets as _secrets
                tok = _secrets.token_hex(8)
                self.__dict__.setdefault("_mafia_tokens", {})[pname] = tok
                if mates:
                    self._mafia_send_private(pname, "hdm", target=pname, text=msg, role=prole, mates=mates, tok=tok)
                else:
                    self._mafia_send_private(pname, "hdm", target=pname, text=msg, role=prole, tok=tok)
        self.add_mafia_system("(AI 참가자가 순차 입장합니다…)")
        threading.Thread(target=self._host_then_bootstrap_bg, daemon=True).start()

    def _host_then_bootstrap_bg(self):
        """(사회자 즉시 개회) → AI 부트(느림, 백그라운드) → 완료 알림."""
        self._host_opening_now()
        players_desc = ", ".join(self.core.players.keys())
        need = max(1, getattr(self, "mafia_ai_count", 4))
        ai_names = [n for n, p in self.core.players.items() if p.get("is_ai")]
        personas_to_spawn = getattr(self, "_session_ai_personas", None) or ALL_PERSONAS[:len(ai_names)]
        oks = self.ai.spawn_all(personas_to_spawn, self.engine.name,
                                players_desc, names=ai_names)
        # AI 스폰 완료 후 core의 역할 정보를 AI 객체들에게 배정!
        self.ai.assign_roles({n: p["role"] for n, p in self.core.players.items()}, self.core, self._claims_for)
        fail = [n for n, ok in oks if not ok]
        if fail:
            self.root.after(0, lambda: self.add_mafia_system(
                f"⚠ AI 부트 실패: {', '.join(fail)} — 해당 AI는 관전만 합니다."))

    def _host_opening_now(self):
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

    def _on_game_end(self, winner):
        self._mafia_room_close()
        self._reset_ghost_state()
        if self._mafia_is_host():
            self._mafia_broadcast("end", winner=winner, roles={
                n: p.get("role") for n, p in self.core.players.items()})
        self._cancel_mafia_timer()
        self._mafia_stop_disconnect_watch()
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
        # 판이 끝나면 방장 신분도 내려놓는다. 그대로 두면 이 PC가 다음 판에서 클라이언트가 됐을 때
        # 방장 전용 이벤트(recruit_start 등)를 '방장에게 온 것'으로 보고 전부 버려 참가 신청 버튼이 안 뜬다.
        self.mafia_host_mode = False
        self._recruiter_host = None
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
        self.refresh_mafia_phase_label()
        self._mafia_pack_lobby_buttons()

    def mafia_shutdown(self):
        try:
            self.ai.stop_all()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        self._cancel_mafia_timer()
