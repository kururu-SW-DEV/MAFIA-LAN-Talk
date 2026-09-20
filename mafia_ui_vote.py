# -*- coding: utf-8 -*-
"""mafia_ui_vote.py — 마피아 게임방 UI 믹스인 (낮 흐름: 낮 타이머, 투표·재투표, 최후 변론·찬반, 처형).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaVoteMixin:
    """낮 흐름: 낮 타이머, 투표·재투표, 최후 변론·찬반, 처형"""

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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        # v1.12 — 유저/AI 침묵 25초 경과 시 자율 토론 킥스타트(게임 정지 방지)
        if self._mafia_is_host() and remain > 3 and (time.time() - getattr(self, "_last_any_talk_ts", time.time())) >= 25:
            self._last_any_talk_ts = time.time()
            self._kick_silent_room()
        # v1.61 — 사람이 말 걸 때만 AI가 반응하면 사람에게만 몰리는 느낌이 난다.
        # 사람 발언과 무관하게 AI끼리 서로 의심·반박하는 대화를 주기적으로 시작한다.
        if self._mafia_is_host() and remain > 6:
            now_t = time.time()
            if now_t - getattr(self, "_last_ai_banter", 0) >= 11:
                self._last_ai_banter = now_t
                if random_mod.random() < 0.7:
                    self._ai_vs_ai_banter()
        self._day_tick = self.root.after(1000, self._day_tick_loop)

    # v1.40 — 토론 중 '투표 X' 조기 자동투표/파싱 로직 제거.
    # 실제 투표는 개표 팝업(_show_vote_popup)에서만 집계되며, 유저가
    # 먼저 투표하거나 유예시간이 지난 뒤에야 AI 표가 반영된다.

    def _cancel_tally_safety_timers(self):
        """투표 집계 관련 안전망 지연 타이머 전원 취소."""
        ft = getattr(self, "_force_tally_timer", None)
        if ft:
            try:
                self.root.after_cancel(ft)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._force_tally_timer = None
        pvc = getattr(self, "_pending_vote_close", None)
        if pvc:
            try:
                self.root.after_cancel(pvc)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._pending_vote_close = None

    def _schedule_tally(self, delay_ms=300):
        """v1.34: 개표 중복 호출 및 사회자 최후변론 멘트 이중 출력 방지 단일 스케줄러."""
        if not self._mafia_is_host():
            # v1.61 — 개표는 호스트 전용 권한. 원격 참가자는 시간이 다 되면 자기
            # 투표 팝업만 정리한다(불완전한 로컬 core로 임의 개표 방지).
            try:
                self._cancel_vote_popup()
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            return
        if getattr(self, "_tally_scheduled", False) or getattr(self, "_tally_in_progress", False):
            return
        self._tally_scheduled = True
        self._cancel_mafia_timer()
        self._cancel_tally_safety_timers()
        try:
            self._cancel_vote_popup()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_BTN_PX,
                radius=8, pad_x=10, pad_y=6, min_w=96
            )
        self._vote_btns = self._grid_candidates_centered(frame, alive, _mk_vote_btn)
        # v1.09 — 기권 버튼(문서 '기권 허용' 표준)
        ab = emoji_render.make_pill_button(
            body, "기권", lambda: self._popup_vote(None),
            bg="#111827", fg="#9ca3af", hover_bg="#374151",
            font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_SMALL_BTN_PX,
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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

    def _pile_on_redirect(self, voter, target):
        """v1.61 — 몰표 방지: 이미 AI 표가 한 명에게 절반 이상(최소 2표) 쌓였는데 뒤의 AI가 또 그 사람을
        찍으려 하면, 일정 확률로 표를 가장 덜 받은 다른 후보에게 돌린다. 사람이든 AI든 누구에게나
        똑같이 적용된다(사람만 봐주는 게 아님)."""
        import random as _rr
        try:
            ai_names = {pl.name for pl in self.ai.players}
            n_ai = max(1, sum(1 for pl in self.ai.players if pl.alive))
            limit = max(1, int(n_ai * AI_PILE_ON_LIMIT_RATIO))
            ai_votes = [t for v, t in self.core.votes.items() if v in ai_names and t]
            if ai_votes.count(target) < limit or _rr.random() >= AI_PILE_ON_REDIRECT_PROB:
                return target
            alts = [n for n in self.core.alive_players() if n not in (voter, target)]
            if not alts:
                return target
            fewest = min(ai_votes.count(n) for n in alts)
            return _rr.choice([n for n in alts if ai_votes.count(n) == fewest])
        except Exception:
            return target

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
                    f"말이 많거나 적다는 이유만으로 정하지 말고, 사람마다 수상한 근거를 따로 따져 "
                    f"표가 한 명에게만 쏠리지 않게 판단하세요. 사람 참가자와 AI 참가자를 똑같이 "
                    f"의심 대상으로 보고, AI끼리의 논쟁(서로 의심·반박한 내용)도 근거로 삼으세요. 답은 오직 '투표 이름' 한 줄.")
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
                # 사람 참가자 전원에게 똑같이 적용한다(호스트든 원격이든).
                alive = self.core.alive_players() if getattr(self, "core", None) else []
                humans = [n for n in alive
                          if not (self.core.players.get(n) or {}).get("is_ai") and n != ag.name]
                _r.shuffle(humans)
                for h in humans:
                    tone, _tone_txt = self._barometer_last_user_tone(h)
                    if tone == "threat":
                        # 협박 → AI가 그 사람을 지목할 확률 +25%p (반감/공포 반영)
                        if _r.random() < 0.25:
                            target = h
                            break
                    elif tone == "persuade" and target == h and _r.random() < 0.10:
                        # 설득 → 그 사람을 지목할 확률 소폭 감소(-10%p)
                        others = [n for n in alive if n not in (h, ag.name)]
                        if others:
                            target = random_mod.choice(others)

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
                    # (바깥 함수의 target을 여기서 재대입하면 파이썬이 지역변수로 취급해
                    #  UnboundLocalError가 난다 — 새 이름으로 받는다)
                    final_target = self._pile_on_redirect(ag.name, target)
                    self.core.cast_vote(ag.name, final_target)
                    _pd, _pt = self._vote_progress_counts()
                    self.add_mafia_system(f"🗳 {ag.name}(AI)님 투표 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                    self._refresh_vote_progress_label()
                    try:
                        self._update_vote_btn_state()
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
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
                                except Exception as _swallow_e:
                                    applog.swallowed(_swallow_e)
                            self._force_tally_timer = self.root.after(8_000, self._silent_tally_if_pending)

            try:
                self.root.after(0, _apply)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)

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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._pending_vote_close = None
        self._vote_lbl = None
        self._vote_btns = {}
        self._vote_abstain_btn = None
        self._vote_progress_lbl = None
        try:
            self._mafia_overlay_close()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
                if getattr(self, "_vote_abstain_btn", None):
                    try: self._vote_abstain_btn.config(bg=M_HOST, fg="white", text="✓ 기권", state="disabled")
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
                self.add_mafia_bubble("투표 기권", "나", mine=True)
                _pd, _pt = self._vote_progress_counts()
                self.add_mafia_system(f"🗳 {me} 기권 접수 · 진행률 {_pd}/{_pt}")
                self._refresh_vote_progress_label()
                # v1.61 — 복수 인간 플레이: 내가 호스트면 다른 참가자들에게
                # 즉시 재동기화, 클라이언트면 호스트에게 실제 반영을 요청.
                if self._mafia_is_host():
                    self._broadcast_vote_done(me, None)
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
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
                if getattr(self, "_vote_abstain_btn", None):
                    try: self._vote_abstain_btn.config(state="disabled")
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
                self.add_mafia_bubble("투표 완료 (익명 개표)", "나", mine=True)   # v1.47 — 대상 비노출
                _pd, _pt = self._vote_progress_counts()
                self.add_mafia_system(f"🗳 {me}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                self._refresh_vote_progress_label()
                # v1.61 — 복수 인간 플레이 동기화(위 기권 분기와 동일한 이유)
                if self._mafia_is_host():
                    self._broadcast_vote_done(me, name)
                else:
                    self._mafia_send_to_host("vote_cast", voter=me, target=name)
                # v1.18 — 클릭 즉시 시각 피드백(눌린 버튼 보라색 + '✓ 접수' 라벨),
                # 0.25초 뒤 닫기 — '반응 없이 꺼진다' 체감 해소
                try:
                    b = (getattr(self, "_vote_btns", {}) or {}).get(name)
                    if b is not None:
                        b.config(bg=M_HOST, fg="white", text=f"✓ {name}", state="disabled")
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        # v1.24 — 유저 표 접수 직후 미투 AI 표 즉시 가속 접수(150ms 간격)
        # 집계 지연 최소화('프리징' 체감 원인 제거)
        try:
            if not self.core.all_voted():
                pend = [p for p in getattr(self, "ai", None) and self.ai.players or []
                        if getattr(p, "alive", False) and p.name not in self.core.votes
                        and self.core.players.get(p.name, {}).get("alive")]
                for k, p2 in enumerate(pend):
                    self.root.after(150 + k * 200, lambda pp=p2: self._ai_vote_in_popup(pp))
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)  # 이미 닫힌 위젯 — 조용히 통과

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
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_BTN_PX,
                radius=8, pad_x=10, pad_y=6, min_w=96
            )
        self._revote_btns = self._grid_candidates_centered(row, cand_names, _mk_revote_btn)
        skip = emoji_render.make_pill_button(
            body, "기권", lambda: self._cast_revote(None),
            bg="#1f2937", fg="#9ca3af", hover_bg="#374151",
            font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_SMALL_BTN_PX,
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
                self._broadcast_vote_done(me, name)
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
                    self.add_mafia_ai(name, t)   # 로컬 표시 + 원격 참가자에게 asay 방송
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        if getattr(self, "_defense_ui_q", None) is not None and getattr(self.core, "defendant", None):
            self.root.after(300, self._poll_defense_ui_queue)

    def _barometer_last_user_tone(self, speaker=None):
        """v1.26 — 사람 참가자의 최근 발화 톤 측정: 협박/설득/중립. 변론 토대 반영.
        speaker가 없으면 이 PC의 사용자, 있으면 그 사람(원격 참가자 포함)의 발화를 본다 —
        예전에는 호스트 사용자만 봐서 협박 불이익이 원격 사람에게는 적용되지 않았다."""
        me = getattr(self.engine, "name", None)
        who = speaker or me
        vals = []
        for rec in getattr(self, "mafia_history", [])[-30:]:   # 최근 발화들 중 그 사람의 최근 5개만
            if rec.get("kind") != "text":
                continue
            # v1.26 — 내 발화는 mine=True('나')로 저장 — 라벨 조건 동시 인정
            own = (who == me) and (rec.get("mine") or rec.get("label") == "나")
            if own or rec.get("label") == who:
                vals.append(str(rec.get("text", "")))
        vals = vals[-5:]
        joined = " ".join(vals)
        threat_kw = mafia_config.THREAT_KEYWORDS
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
                if defendant and not (c.players.get(defendant) or {}).get("is_ai"):
                    tone, _t = self._barometer_last_user_tone(defendant)
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
            b_close = emoji_render.make_pill_button(
                body, "확인 (닫기)", _close_early,
                bg="#374151", fg="white", hover_bg="#4b5563",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_SMALL_BTN_PX,
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
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
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
            font_path=emoji_render.FONT_PATH_BOLD, font_size=POPUP_BTN_PX,
            radius=8, pad_x=_defense_pad_x, pad_y=6, min_w=_defense_btn_w
        )
        _btn_yes.pack(side="left", padx=6)
        _btn_no = emoji_render.make_pill_button(
            row, "🕊 만류", lambda: self._cast_defense(name, False),
            bg="#15803d", fg="white", hover_bg="#16a34a",
            font_path=emoji_render.FONT_PATH_BOLD, font_size=POPUP_BTN_PX,
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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._defense_popup10 = None

    def _cancel_defense_vote_tick(self):
        t = getattr(self, "_defense_vote_tick", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._defense_vote_tick = None

    def _cast_defense(self, name, yes):
        me = getattr(self.engine, "name", None)
        for _b in getattr(self, "_defense_btns", []):
            try: _b.config(state="disabled")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._defense_popup10 = None
        result, yes, no = self.core.execute_defense(name)
        self._sync_ai_alive()   # v1.11 — 최후변론 처형 시 AI 발화 차단
        role2 = self.core.reveal_role(name)
        if result == "executed":
            self.add_mafia_system(f"⚖ 찬성 {yes} : 반대 {no} — '{name}' 님 처형 확정!")
            if role2:
                self.add_mafia_system(f"🎭 직업 공개 — {name} ({_role_was(role2)})")
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
                txt = "이건 억울한 처형이에요. 저는 마피아가 아닙니다. 다시 한번 생각해 주세요."

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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
