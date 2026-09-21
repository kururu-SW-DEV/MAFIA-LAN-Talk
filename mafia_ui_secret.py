# -*- coding: utf-8 -*-
"""mafia_ui_secret.py — 마피아 게임방 UI 믹스인 (마피아 비밀방·유령방: AI와의 대화, 대화로 정하는 살해 목표).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaSecretMixin:
    """마피아 비밀방·유령방: AI와의 대화, 대화로 정하는 살해 목표"""

    def _mafia_room_wanted(self):
        """지금 밤이고 내가 살아있는 마피아이며 사람 동료가 있으면 비밀방이 필요하다."""
        me = getattr(self.engine, "name", None)
        info = self.core.players.get(me) or {}
        role = info.get("role") or getattr(self, "_my_mafia_role", None)
        return (self.mafia_active and self.core.phase == Phase.NIGHT and role == "mafia"
                and info.get("alive", True)
                and bool(self._mafia_team_names(me) or self._mafia_ai_mates(me)))

    def _maybe_open_mafia_room(self):
        if self._mafia_room_wanted():
            self._mafia_room_history = []
            self._mafia_room_open()

    def _mafia_room_place(self, w, **size):
        """비밀방/밀담 버튼 배치 — 메인 채팅창의 전송 버튼 바로 위(우측 정렬)에 붙여서
        기존 입력창·전송 버튼을 가리지 않게 한다. 전송 버튼이 아직 화면에 없으면
        예전처럼 창 우하단 기준으로 둔다."""
        anchor_w = getattr(self, "send_btn", None)
        try:
            if anchor_w is not None and anchor_w.winfo_ismapped():
                w.place(in_=anchor_w, relx=1.0, rely=0.0, x=0, y=-12, anchor="se", **size)
                return
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        w.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se", **size)

    def _mafia_room_open(self):
        f = getattr(self, "_mafia_room", None)
        try:
            if f is not None and f.winfo_exists():
                f.lift()
                return
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        mini = getattr(self, "_mafia_room_mini", None)
        self._mafia_room_mini = None
        try:
            if mini is not None and mini.winfo_exists():
                mini.destroy()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        f = tk.Frame(self.root, bg=C_CARD, highlightthickness=1, highlightbackground="#b91c1c")
        self._mafia_room_place(f, width=310, height=270)
        head = tk.Frame(f, bg=C_CARD)
        head.pack(fill="x", padx=10, pady=(8, 4))
        lbl = tk.Label(head, text="🔪 마피아 비밀방 — 마피아끼리만", bg=C_CARD, fg="#fca5a5",
                       font=(FONT_FAM, 9, "bold"), anchor="w")
        lbl.pack(side="left")
        emoji_render.apply(lbl, (FONT_FAM, 9, "bold"))
        emoji_render.make_pill_button(
            head, "✕", lambda: self._mafia_room_close(keep_reopen=True), bg="#374151", fg="white",
            hover_bg="#4b5563", font_path=emoji_render.FONT_PATH_REGULAR,
            font_size=POPUP_SMALL_BTN_PX, radius=6, pad_x=8, pad_y=2).pack(side="right")
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
                                      font_size=POPUP_SMALL_BTN_PX, radius=6, pad_x=10, pad_y=3).pack(side="right")
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _mafia_room_close(self, keep_reopen=False):
        f = getattr(self, "_mafia_room", None)
        self._mafia_room = None
        try:
            if f is not None and f.winfo_exists():
                f.destroy()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        mini = getattr(self, "_mafia_room_mini", None)
        self._mafia_room_mini = None
        try:
            if mini is not None and mini.winfo_exists():
                mini.destroy()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        if keep_reopen:
            # ✕로 직접 닫은 경우: 밤이 끝날 때까지 다시 열 수 있는 작은 버튼 유지
            b = emoji_render.make_pill_button(
                self.root, "🔪 마피아 밀담", self._mafia_room_open, bg="#b91c1c", fg="white",
                hover_bg="#ef4444", font_path=emoji_render.FONT_PATH_REGULAR,
                font_size=POPUP_BTN_PX, radius=8, pad_x=12, pad_y=4)
            self._mafia_room_place(b)
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
        # AI 마피아 동료도 이 말을 듣고 답한다(AI는 호스트에서만 돌아간다)
        if self._mafia_ai_mates(me):
            if self._mafia_is_host():
                self._mafia_ai_respond(me, text)
            else:
                self._mafia_send_to_host("mafia_to_ai", name=me, text=text)

    def _mafia_ai_mates(self, me):
        """내가 마피아일 때 살아있는 AI 마피아 동료(나 제외)."""
        names = [n for n in self.core.mafias() if n != me]
        for n in getattr(self, "_my_mafia_mates", None) or []:
            if n not in names and n != me:
                names.append(n)
        return [n for n in names
                if (self.core.players.get(n) or {}).get("is_ai", False)
                and (self.core.players.get(n) or {}).get("alive", True)]

    def _mafia_secret_recipients(self):
        """AI 마피아의 비밀 발언을 받을 사람 마피아(생존) 이름들 — 호스트 본인 포함."""
        return [n for n in self.core.mafias()
                if not (self.core.players.get(n) or {}).get("is_ai", False)]

    def _mafia_ai_respond(self, speaker, text):
        """사람 마피아(speaker)의 비밀방 발언에 AI 마피아 1~2명이 답한다.
        speaker/text가 None이면 AI가 먼저 말을 꺼낸다(밤 시작 직후)."""
        import random as _rr
        if (not self._mafia_is_host() or not getattr(self, "ai", None)
                or not self.mafia_active or self.core.phase != Phase.NIGHT):
            return
        ais = [pl for pl in self.ai.players
               if pl.alive and pl.role == "mafia" and getattr(pl, "booted", False)]
        if not ais or not self._mafia_secret_recipients():
            return
        log = getattr(self, "_mafia_secret_log", None)
        if log is None:
            log = self._mafia_secret_log = []
        if speaker and text:
            log.append(f"{speaker}: {text}")
            del log[:-10]
            for pl in ais:                      # 마피아 AI끼리만 기억(공개 채팅에는 안 새게)
                pl.observe(speaker, f"(마피아 비밀 대화) {text}")
            self._mafia_plan_from_human(text)   # 대화에서 정한 오늘 밤 목표를 기록
        _rr.shuffle(ais)
        picks = ais[:2] if (speaker and len(ais) > 1 and _rr.random() < 0.4) else ais[:1]
        self._ensure_mafia_ai_poll()
        for order, pl in enumerate(picks):
            threading.Thread(target=self._mafia_ai_worker,
                             args=(pl, speaker, text, order), daemon=True).start()

    _AGREE_RE = re.compile(r"(좋아|좋다|좋지|그래|그러자|ㅇㅋ|오케|okay|ok|동의|ㄱㄱ|가자|콜|찬성|그걸로|그렇게)", re.I)

    # 이름 뒤에 이런 표현이 붙으면 그 사람을 '노리자'가 아니라 '빼자/피하자'는 뜻으로 본다.
    _DISAGREE_RE = re.compile(r"(싫|아니|반대|말고|안 ?(?:돼|되)|별로|글쎄|말자|피하|제외|빼고|빼자|"
                              r"건드리지|건들지|살려|놔두|냅두|내버려|패스)")

    # 이름 '앞'에 올 때만 부정으로 보는 강한 표현('아니','말고'는 뒤 말에 따라 뜻이 갈려 제외)
    _PRE_NEG_RE = re.compile(r"(싫|반대|안 ?(?:돼|되)|별로|말자|피하|제외|패스)")

    # 이름 뒤에 이런 서술이 있으면 그 사람을 '목표로 삼자'는 뜻이라 앞쪽 부정어를 무시한다
    _AFFIRM_RE = re.compile(r"(노리|죽이|죽여|가자|하자|해보자|찍자|잡자|어때|로 ?정|콜)")

    def _mafia_kill_candidates(self):
        """오늘 밤 살해 후보 — 살아있는 마피아 아닌 참가자."""
        return [n for n in self.core.alive_players()
                if (self.core.players.get(n) or {}).get("role") != "mafia"]

    @staticmethod
    def _last_named(text, cands):
        """text에서 가장 나중에 언급된 후보 이름(없으면 None)."""
        best, pos = None, -1
        for n in cands:
            i = (text or "").rfind(n)
            if i > pos:
                best, pos = n, i
        return best

    @classmethod
    def _named_split(cls, text, cands):
        """text 속 후보 이름을 (긍정 대상, 부정된 이름 집합)으로 나눈다.
        각 이름 '바로 뒤부터 다음 이름 앞까지'에 부정 표현('말자','안 돼','피하자' 등)이 있으면
        부정된 이름 — 예) '김철수는 안 돼, 이영희로 하자' → (이영희, {김철수}).
        긍정 대상은 부정되지 않은 이름 중 가장 나중에 언급된 것(없으면 None)."""
        text = text or ""
        names = sorted({n for n in cands if n}, key=len, reverse=True)
        if not names or not text:
            return None, set()
        # 긴 이름부터 매칭해서 '정민우' 안의 '민우'처럼 다른 이름에 포함된 부분 문자열이 따로 잡히지 않게 한다
        hits = [(m.start(), m.end(), m.group(0))
                for m in re.finditer("|".join(re.escape(n) for n in names), text)]
        positive, negated = None, set()
        prev_end = 0
        for i, (start, end, name) in enumerate(hits):
            nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
            after = text[end:nxt]
            neg = bool(cls._DISAGREE_RE.search(after))
            if not neg:
                # 이름 앞에 부정어가 오는 말투('안 돼 김철수', '별로야 김철수는 진짜') — 뒤에
                # '노리자/하자' 같은 서술이 없으면 부정으로 본다('아니 김철수 노리자'는 긍정).
                before = text[max(prev_end, start - 10):start]
                neg = bool(cls._PRE_NEG_RE.search(before)) and not cls._AFFIRM_RE.search(after)
            prev_end = end
            if neg:
                negated.add(name)
            else:
                positive = name
        return positive, negated

    def _mafia_plan_from_human(self, text):
        """사람 마피아의 비밀방 발언에서 목표를 읽는다 — 이름을 말하면 사람이 정한 것(확정),
        이름 없이 동의하면 AI가 제안해 둔 목표를 확정한다. '○○는 안 돼/피하자'처럼 이름이
        부정과 함께 나오면 목표로 잡지 않고, 그 사람이 현재 목표였다면 취소한다."""
        name, negated = self._named_split(text, self._mafia_kill_candidates())
        plan = getattr(self, "_mafia_kill_plan", None)
        if name:
            self._mafia_set_plan(name, "human", True)
        elif negated:
            if plan and plan["target"] in negated:
                self._mafia_clear_plan()
        elif (plan and plan["by"] == "ai" and not plan["confirmed"]
              and self._AGREE_RE.search(text or "") and not self._DISAGREE_RE.search(text or "")):
            self._mafia_set_plan(plan["target"], "human", True)

    def _mafia_clear_plan(self):
        """현재 목표를 취소하고 사람 마피아 비밀방에 알린다."""
        plan = getattr(self, "_mafia_kill_plan", None)
        if not plan:
            return
        self._mafia_kill_plan = None
        self._mafia_secret_broadcast("📌 오늘 밤 목표", f"{plan['target']} 취소")

    def _mafia_set_plan(self, target, by, confirmed):
        """목표를 갱신하고, 바뀌었으면 사람 마피아 비밀방에 알린다."""
        plan = getattr(self, "_mafia_kill_plan", None)
        if plan and plan["target"] == target and plan["confirmed"] == confirmed:
            return
        self._mafia_kill_plan = {"target": target, "by": by, "confirmed": confirmed}
        self._mafia_secret_broadcast("📌 오늘 밤 목표", f"{target} ({'확정' if confirmed else '제안'})")

    def _mafia_secret_broadcast(self, who, text):
        """사람 마피아(호스트 본인 포함)의 비밀방에만 한 줄 전달."""
        me = getattr(self.engine, "name", None)
        for n in self._mafia_secret_recipients():
            if n == me:
                if self._mafia_room_wanted():
                    self._mafia_room_append(who, text)
            else:
                self._mafia_send_private(n, "mafia_say", name=who, text=text)

    _MAFIA_AI_FALLBACK = ("오늘 밤엔 누가 좋을까?", "난 조용한 사람이 신경 쓰이는데ㅋㅋ",
                          "우리 너무 티 내지 말자~", "일단 의견부터 들어보자!")

    def _mafia_ai_worker(self, pl, speaker, text, order):
        import random as _rr
        import time as _t
        try:
            if order:
                _t.sleep(1.5 + _rr.random())
            targets = [n for n in self.core.alive_players()
                       if (self.core.players.get(n) or {}).get("role") != "mafia"]
            history = "\n".join(list(getattr(self, "_mafia_secret_log", []))[-8:])
            base = (f"[마피아 비밀 채팅] 지금은 밤이고, 마피아 동료들끼리만 보는 비밀 대화방입니다. "
                    f"오늘 밤 살해 후보(시민 쪽 생존자): {', '.join(targets) or '없음'}. "
                    f"최근 대화:\n{history}\n") if history else (
                    f"[마피아 비밀 채팅] 지금은 밤이고, 마피아 동료들끼리만 보는 비밀 대화방입니다. "
                    f"오늘 밤 살해 후보(시민 쪽 생존자): {', '.join(targets) or '없음'}. ")
            plan = getattr(self, "_mafia_kill_plan", None)
            plan_t = plan["target"] if plan and plan["confirmed"] else None
            if speaker and text:
                if plan_t:
                    goal = (f"동료가 '{plan_t}'을(를) 오늘 밤 목표로 정했습니다. 그 결정에 동의하며 "
                            f"'{plan_t}' 이름을 넣어 짧게 확정 멘트를 하세요. 다른 사람을 제안하지 마세요.")
                else:
                    goal = ("누구를 노릴지 아직 안 정해졌으니, 후보 중 딱 한 명을 골라 이름을 넣어 "
                            "구체적으로 제안하거나 동료 의견에 맞춰 조율하세요.")
                prompt = (base + f"동료 '{speaker}'님이 방금 말했습니다: \"{text}\"\n"
                          "그 말에 직접 대답하세요. 친구랑 카톡하듯 편하게 1~2문장, 이름을 부르거나 "
                          "되물어서 대화가 이어지게. " + goal + " "
                          "후보 이름은 위 목록에 있는 사람만, 자기 자신이나 동료는 절대 대상으로 말하지 마세요.")
            else:
                prompt = (base + "동료에게 먼저 말을 걸어 오늘 밤 누구를 노릴지 후보 중 한 명의 이름을 "
                          "넣어 제안하고 의견을 물어보세요. 친구랑 카톡하듯 편하게 1문장. 후보 이름은 위 목록에서만.")
            reply = None
            for _ in range(3):
                reply = pl.say(prompt, secret=True)
                if reply:
                    break
                _t.sleep(1.5)
            reply = split_chat_tags(clean_llm_dialect((reply or "").strip()))
            if not reply or len(reply) > 160:
                if plan_t:
                    reply = f"좋아, {plan_t}로 가자!"
                elif targets:
                    reply = f"오늘은 {_rr.choice(targets)} 어때?"
                else:
                    reply = _rr.choice(self._MAFIA_AI_FALLBACK)
            self._mafia_ai_q.put((pl, reply))    # UI 반영은 메인스레드 폴러가 한다
        except Exception as e:
            import applog
            applog.log("mafia_ai_worker", exc=e)

    def _ensure_mafia_ai_poll(self):
        """비밀방 AI 답장 큐 폴러를 (중복 없이) 시작한다 — 밤이 끝나면 스스로 멈춘다.
        워커 스레드가 root.after를 직접 부르면 'main thread is not in main loop'로
        조용히 사라질 수 있어, 유령방·최후변론과 같은 큐 방식을 쓴다."""
        import queue as _q
        if getattr(self, "_mafia_ai_q", None) is None:
            self._mafia_ai_q = _q.Queue()
        if not getattr(self, "_mafia_ai_poll_on", False):
            self._mafia_ai_poll_on = True
            self._poll_mafia_ai_queue()

    def _poll_mafia_ai_queue(self):
        try:
            while True:
                pl, reply = self._mafia_ai_q.get_nowait()
                self._mafia_ai_post(pl, reply)
        except Exception as e:
            import queue as _q
            if not isinstance(e, _q.Empty):
                import applog
                applog.log("mafia_ai_poll", exc=e)
        if self.mafia_active and self.core.phase == Phase.NIGHT:
            self.root.after(250, self._poll_mafia_ai_queue)
        else:
            self._mafia_ai_poll_on = False

    def _mafia_ai_post(self, pl, reply):
        """AI 마피아의 비밀 발언을 사람 마피아들에게 전달(밤이 끝났으면 버린다)."""
        if (not self.mafia_active or self.core.phase != Phase.NIGHT or not pl.alive):
            return
        log = getattr(self, "_mafia_secret_log", None)
        if log is None:
            log = self._mafia_secret_log = []
        log.append(f"{pl.name}: {reply}")
        del log[:-10]
        for other in self.ai.players:           # 다른 AI 마피아도 이 말을 기억
            if other is not pl and other.alive and other.role == "mafia" and other.booted:
                other.observe(pl.name, f"(마피아 비밀 대화) {reply}")
        self._mafia_secret_broadcast(pl.name, reply)
        # AI가 제안한 이름은 '제안'으로 기록(사람이 이미 확정한 목표는 AI 말로 바뀌지 않는다)
        plan = getattr(self, "_mafia_kill_plan", None)
        if not (plan and plan["confirmed"]):
            named, _neg = self._named_split(reply, self._mafia_kill_candidates())
            if named:
                self._mafia_set_plan(named, "ai", False)

    def _mafia_ai_opener(self):
        """밤 시작 후 비밀방이 열릴 즈음 AI 마피아가 먼저 말을 건다(호스트 전용)."""
        if getattr(self, "_mafia_secret_log", None):
            return                                # 이미 대화가 오가는 중이면 굳이 또 꺼내지 않는다
        self._mafia_ai_respond(None, None)

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

    _GHOST_LOG_MAX = 300

    def _reset_ghost_state(self):
        """유령방 대화·상태를 비운다. 게임이 시작될 때와 끝날 때만 부른다 — 예전에는 유령방을 열 때마다 대화 기록을 비워서
        (낮·투표·밤마다 자동으로 다시 열림) 대화가 매 턴 초기화됐다."""
        self._ghost_log = []
        self._ghost_remote_log = {}
        self._ghost_alone_noted = False
        self._ghost_kick_sig = None
        self._ghost_unread = False
        self._ai_color_cache = None          # 게임이 시작·종료되면 명단이 바뀌므로 AI 색 캐시도 비운다

    def _ghost_roster_text(self):
        """유령방 상단 현황: 생존/사망 인원과 사망자(유령)의 직업."""
        core = self.core
        me = getattr(self.engine, "name", None)
        with core.lock:
            n_alive = sum(1 for p in core.players.values() if p.get("alive"))
            n_dead = sum(1 for p in core.players.values() if not p.get("alive"))
        lines = [f"🟢 생존 {n_alive}명 · 💀 사망 {n_dead}명 — 유령 현황"]
        for n in core.ghost_room_members():
            role = core.reveal_role(n)
            info = core.players.get(n) or {}
            tag = n + (" (나)" if n == me else "") + (" 🤖" if info.get("is_ai") else "")
            lines.append(f"👻 {tag} — {_role_kr(role) if role else '직업 확인 중'}")
        return lines

    def _open_ghost_chat(self):
        """사망자끼리만 진실(직업) 알고 수다 떠는 비공개 유령 채팅방.
        생존자에게는 전혀 노출되지 않는 별도 overlay. 대화는 게임이 끝날 때까지 누적되어, 닫았다가 다시 열어도 이어진다."""
        me = getattr(self.engine, "name", None)
        if not me or self.core.players.get(me, {}).get("alive", False):
            self.add_mafia_system("👻 유령 채팅방은 사망자 전용입니다 — 지금은 생존 중이라 들어올 수 없습니다")
            return
        if getattr(self, "_ghost_log", None) is None:
            self._reset_ghost_state()
        self._ghost_alone_noted = False
        # v1.17 — 유령방 UI 큐 준비(워커→메인스레드 안전 반영 경로)
        import queue as _q
        self._ghost_ui_q = getattr(self, "_ghost_ui_q", None) or _q.Queue()
        self._ghost_ui_open = True
        ghosts = self.core.ghost_room_members()
        if not ghosts:
            self.add_mafia_system("👻 사망자가 아직 없습니다.")
            return
        body = self._mafia_overlay_open("👻 유령 채팅방 — 사망자들만의 공간", w=460, h=None)
        # v1.17 — overlay_open 내부의 prev-close가 flag를 reset하므로 '뒤'에서 복원
        self._ghost_ui_open = True
        self._ghost_unread = False
        tk.Label(body, text="여기는 사망자만 보는 공간 — 모든 진실이 공개됩니다.",
                 fg="#a5b4fc", bg=C_CARD, font=M_FONT_HELP).pack(pady=(10, 4))
        # ── 상단: 유령 현황 + 직업 ──
        rostf = tk.Frame(body, bg="#111827"); rostf.pack(fill="x", padx=18, pady=(0, 6))
        self._ghost_roster = tk.Text(rostf, height=4, bd=0, bg="#111827", fg=M_TEXT_LIGHT, font=M_FONT_HELP,
                                     wrap="word", state="disabled", padx=8, pady=6)
        self._ghost_roster.pack(fill="x")
        self._render_ghost_list(body)
        # ── 대화 기록(누적) ──
        listf = tk.Frame(body, bg=C_CARD); listf.pack(fill="x", padx=18)
        self._ghost_list = tk.Text(listf, height=12, bd=0, bg=C_CARD, fg=M_TEXT_LIGHT,
                                    font=M_FONT_BODY, wrap="word", state="disabled")
        self._ghost_list.pack(fill="x")
        self._ghost_list.configure(state="normal")
        for ln in self._ghost_log[-self._GHOST_LOG_MAX:]:
            self._ghost_list.insert("end", ln + chr(10))
        self._ghost_list.configure(state="disabled")
        self._ghost_list.see("end")
        # 입력
        ent_f = tk.Frame(body, bg=C_CARD); ent_f.pack(fill="x", padx=18, pady=(6, 10))
        self._ghost_ent = tk.Entry(ent_f, bg="#111827", fg=M_TEXT_LIGHT, relief="flat",
                                   insertbackground=M_TEXT_LIGHT, font=M_FONT_BODY)
        self._ghost_ent.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._ghost_ent.bind("<Return>", self._ghost_send)
        emoji_render.make_pill_button(
            ent_f, "보내기", self._ghost_send, bg=M_HOST, fg="white",
            hover_bg="#9333ea", font_path=emoji_render.FONT_PATH_REGULAR,
            font_size=POPUP_BTN_PX, radius=6, pad_x=12, pad_y=4
        ).pack(side="right")
        self._ensure_ghost_poll()
        # v1.13 — 사망 AI가 유령방에서 수다를 떠는 자동 발화: 유령 구성이 바뀌었을 때만(새 사망자가 들어왔을 때) 한다.
        # 열 때마다 하면 같은 인사가 반복되고 대화가 어지러워진다.
        sig = tuple(sorted(ghosts))
        if sig != getattr(self, "_ghost_kick_sig", None):
            self._ghost_kick_sig = sig
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
                        f"사망자의 속닥임이나 부탁, 마지막 충고 2문장 이내로 해보세요.", secret=True)
                    txt2 = (txt or "").strip()
                    if txt2:
                        t2 = split_chat_tags(clean_llm_dialect(txt2))
                        self._ghost_ui_q.put(("ai", p_.name, t2))
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
            _th.Thread(target=worker, args=(pl, i), daemon=True).start()
        # UI 큐 폴러 — 메인스레드에서만 위젯 접근
        self._ensure_ghost_poll()
        # LLM 실패/침묵 시 기본 문구 — 3~5초 후
        self.root.after(3000, self._ghost_fallback_lines)

    def _ensure_ghost_poll(self):
        """유령방 UI 큐 폴러를 (중복 없이) 시작한다. 사망 AI가 없어 첫 수다가
        안 떴던 방에서도, 이후 유저 발언에 대한 AI 답장이 화면에 뜨려면 필요."""
        if not getattr(self, "_ghost_poll_on", False):
            self._ghost_poll_on = True
            self._poll_ghost_ui_queue()

    def _poll_ghost_ui_queue(self):
        """v1.17 — 유령방 UI 큐 드레인(메인스레드 전용). 0.3초 주기."""
        try:
            while True:
                kind, name, t = self._ghost_ui_q.get_nowait()
                self._append_ghost(f"👻 {name}: {t}", ai=True)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        if getattr(self, "_ghost_ui_open", False):
            _now = time.time()
            if _now - getattr(self, "_ghost_roster_ts", 0) > 2.0:      # 새 사망자·직업 공개를 현황에 반영
                self._ghost_roster_ts = _now
                self._render_ghost_list(None)
            self.root.after(300, self._poll_ghost_ui_queue)
        else:
            self._ghost_poll_on = False

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

    def _render_ghost_list(self, body=None):
        """유령방 상단 현황(생존·사망 인원, 유령과 직업)을 그린다."""
        box = getattr(self, "_ghost_roster", None)
        if not box:
            return
        try:
            if not box.winfo_exists():
                self._ghost_roster = None
                return
            lines = self._ghost_roster_text()
            box.configure(state="normal", height=max(2, min(7, len(lines))))
            box.delete("1.0", "end")
            box.insert("end", chr(10).join(lines))
            box.configure(state="disabled")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
            self._ghost_roster = None

    def _ghost_send(self, ev=None):
        ent = getattr(self, "_ghost_ent", None)
        if not ent:
            return
        txt = (ent.get() or "").strip()
        if not txt:
            return
        ent.delete(0, "end")
        self._append_ghost(f"{self.engine.name}: {txt}")
        # 호스트가 죽은 사람이면 원격의 다른 사망자에게도 전달한다(원격 사망자의 말은 호스트가 중계 — ghost_to_ai 처리부).
        self._ghost_relay_humans(self.engine.name, txt)
        # 내 말에 사망 AI가 대답해 티키타카가 이어지게 한다.
        self._ghost_ai_reply(txt)

    def _ghost_relay_humans(self, speaker, text):
        """호스트 전용: 사람 사망자가 유령방에 쓴 말을 다른 사람 사망자에게 전달한다(호스트 본인이 죽었으면 호스트 화면에도 표시).
        예전에는 사망 AI만 답하고 사람 사망자끼리는 서로의 말을 볼 수 없었다."""
        if not self._mafia_is_host():
            return
        me = getattr(self.engine, "name", None)
        for n, p in list(self.core.players.items()):
            if p.get("is_ai") or p.get("alive", True) or n == speaker:
                continue
            if n == me:
                self._append_ghost(f"👻 {speaker}: {text}")
            else:
                self._mafia_send_private(n, "ghost_say", name=speaker, text=text)

    _GHOST_FALLBACK_REPLY = (
        "ㅋㅋ 맞아 그러게", "오 그렇구나~", "나도 그렇게 생각해", "헐 진짜?",
        "아 그거 좀 억울했지ㅠㅠ", "ㅋㅋㅋ 인정", "그럼 산 사람들 누가 이길까?")

    _NO_GHOST_PARTNER = "(아직 이 방에 대화 상대가 없어요 — 다른 참가자가 죽으면 함께 수다 떨 수 있어요)"

    def _ghost_ai_reply(self, user_text, speaker=None):
        """유령방에서 유저가 말하면 사망 AI 1~2명이 그 말에 직접 대답한다.
        AI는 호스트에서만 돌아간다 — 원격 참가자는 호스트에 말을 중계하고(ghost_to_ai),
        호스트가 사망 AI의 답을 그 참가자에게만 개인 전송(ghost_say)한다.
        speaker가 있으면 호스트가 원격 참가자 대신 처리하는 경우다."""
        import random as _rr
        import threading as _th
        import time as _t
        own = getattr(self.engine, "name", "")
        if speaker is None and not self._mafia_is_host():
            self._mafia_send_to_host("ghost_to_ai", name=own, text=user_text)
            return
        remote = speaker is not None and speaker != own
        me = speaker if remote else own
        ais = [pl for pl in (getattr(self, "ai", None) and self.ai.players or [])
               if not pl.alive and getattr(pl, "booted", False)]
        if not ais:
            if remote:
                self._mafia_send_private(me, "ghost_say", name="안내", text=self._NO_GHOST_PARTNER)
            elif not getattr(self, "_ghost_alone_noted", False):
                self._ghost_alone_noted = True
                self._append_ghost(self._NO_GHOST_PARTNER)
            return
        if not remote:
            self._ghost_alone_noted = False
            self._ensure_ghost_poll()
        else:
            self._ensure_ghost_relay_poll()
        _rr.shuffle(ais)
        picks = ais[:2] if (len(ais) > 1 and _rr.random() < 0.5) else ais[:1]
        if remote:
            rlog = getattr(self, "_ghost_remote_log", None)
            if rlog is None:
                rlog = self._ghost_remote_log = {}
            lines = rlog.setdefault(me, [])
            lines.append(f"👻 {me}: {user_text}")
            del lines[:-30]
            recent = lines[-8:]
        else:
            recent = list(getattr(self, "_ghost_log", []))[-8:]
        history = "\n".join(recent)

        def worker(p_, order):
            try:
                if order:
                    _t.sleep(1.5 + _rr.random())       # 두 번째 AI는 조금 뒤에 끼어든다
                role_str = _role_kr(self.core.reveal_role(p_.name))
                prompt = (
                    f"[유령 채팅방 대화] 당신('{p_.name}', 직업 {role_str})은 사망해 유령방에서 "
                    f"수다 중입니다. 최근 대화:\n{history}\n"
                    f"방금 '{me}'님이 말했습니다: \"{user_text}\"\n"
                    f"이 말에 직접 대답하세요. 친구랑 카톡하듯 편하게, 1~2문장, 이름을 부르거나 "
                    f"되물어서 대화가 이어지게. 게임 결과를 가르치듯 설명하지 마세요.")
                reply = None
                for _ in range(3):                      # 다른 발화 중(busy)이면 잠깐 뒤 재시도
                    reply = p_.say(prompt, secret=True)
                    if reply:
                        break
                    _t.sleep(1.5)
                reply = split_chat_tags(clean_llm_dialect((reply or "").strip()))
                if not reply:
                    reply = _rr.choice(self._GHOST_FALLBACK_REPLY)
                if remote:
                    self._ghost_relay_q.put((me, p_.name, reply))
                else:
                    self._ghost_ui_q.put(("ai", p_.name, reply))
            except Exception:
                if remote:      # 대기 카운터가 남지 않도록 실패해도 반드시 하나는 돌려준다
                    self._ghost_relay_q.put((me, p_.name, _rr.choice(self._GHOST_FALLBACK_REPLY)))
        for i, pl in enumerate(picks):
            if remote:
                self._ghost_relay_pending += 1
            _th.Thread(target=worker, args=(pl, i), daemon=True).start()

    def _ensure_ghost_relay_poll(self):
        """원격 사망자에게 보낼 AI 답장 큐 폴러(메인스레드) — 대기 중인 답장이 없으면 멈춘다."""
        import queue as _q
        if getattr(self, "_ghost_relay_q", None) is None:
            self._ghost_relay_q = _q.Queue()
            self._ghost_relay_pending = 0
        if not getattr(self, "_ghost_relay_on", False):
            self._ghost_relay_on = True
            self.root.after(0, self._poll_ghost_relay)

    def _poll_ghost_relay(self):
        import queue as _q
        try:
            while True:
                who, ai_name, text = self._ghost_relay_q.get_nowait()
                self._ghost_relay_pending -= 1
                try:                       # 한 건이 실패해도 큐에 남은 다른 사망자의 답장은 계속 처리한다
                    lines = getattr(self, "_ghost_remote_log", {}).get(who)
                    if lines is not None:
                        lines.append(f"👻 {ai_name}: {text}")
                        del lines[:-30]
                    self._mafia_send_private(who, "ghost_say", name=ai_name, text=text)
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
        except _q.Empty:
            pass
        except Exception as e:
            applog.log("ghost_relay_poll", exc=e)      # (지역 import applog를 두면 위쪽의 applog 사용이 UnboundLocalError가 된다)
        if self._ghost_relay_pending > 0:
            self.root.after(300, self._poll_ghost_relay)
        else:
            self._ghost_relay_on = False

    def _append_ghost(self, text, ai=False):
        """유령방 대화 한 줄을 기록한다. 창이 닫혀 있어도 기록해 두었다가 다시 열 때 보여 준다(누적)."""
        log = getattr(self, "_ghost_log", None)
        if log is None:
            log = self._ghost_log = []
        log.append(text)
        del log[:-self._GHOST_LOG_MAX]
        box = getattr(self, "_ghost_list", None)
        if not box:
            self._ghost_unread = True                    # 닫혀 있는 동안 온 말 — 버튼에 새 글 표시
            return
        try:
            if not box.winfo_exists():
                self._ghost_list = None
                self._ghost_unread = True
                return
        except Exception:
            self._ghost_list = None
            self._ghost_unread = True
            return
        box.configure(state="normal")
        box.insert("end", text + chr(10))
        box.configure(state="disabled")
        box.see("end")

    def _ghost_dm(self, text):
        """나에게만 보이는 사회자 쪽지(기존 host_dm UI 재사용 — 없는 것이면 시스템 라인).
        경찰 조사 결과 같은 비밀 정보는 절대 전체 채팅에 노출 금지(문서 4-2.5).
        ※ 사실 구현: add_mafia_system은 채팅방 전원에게 보이므로, 폐쇄망 문서 기준
        '나에게만' 노출은 host_dm이 있어야 한다.mafia_ui에 host_dm이 존재하면 사용."""
        if getattr(self, "add_mafia_host_dm", None):
            self.add_mafia_host_dm(text)
        else:
            self.add_mafia_system(text)  # 폴백(비밀 DM 없는 환경)
