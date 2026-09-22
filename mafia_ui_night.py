# -*- coding: utf-8 -*-
"""mafia_ui_night.py — 마피아 게임방 UI 믹스인 (밤 흐름: AI 밤 행동(LLM 판단), 밤 패널, 밤 결과, 밤 잡담).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaNightMixin:
    """밤 흐름: AI 밤 행동(LLM 판단), 밤 패널, 밤 결과, 밤 잡담"""

    # 밤 AI 행동 타이밍(밀리초)
    NIGHT_LLM_FALLBACK_MS = 11000     # 경찰·의사: 이 안에 LLM 답이 없으면 무작위로 대신한다

    NIGHT_MAFIA_LLM_MS = 10000        # 마피아: 비밀 대화가 오간 뒤 LLM이 살해 대상을 다시 판단한다

    def _trigger_night_actions(self):
        """밤 AI 행동을 시작한다.
        - 마피아: 즉시 무작위 기본 지목을 깔아 두고(LLM이 늦어도 밤이 진행되게), 비밀 대화가
          오간 뒤(NIGHT_MAFIA_LLM_MS) LLM이 후보 중 살해 대상을 다시 고른다.
        - 경찰·의사: 시작 즉시 LLM에게 묻고, NIGHT_LLM_FALLBACK_MS 안에 답이 없으면 무작위.
        예전엔 세 역할 모두 random.choice였다(추리 게임의 밤이 전부 난수)."""
        import random as _rr
        mafia_ais = [pl for pl in self.ai.players
                     if pl.alive and pl.role == "mafia" and pl.booted]
        alive = self.core.alive_players()
        results = {"multi": [], "multi_pairs": []}
        all_mafias = set(self.core.mafias())
        for pl in mafia_ais:
            # 인간 마피아까지 포함해 동료 살해 제외 + 자투 금지
            cand = [n for n in alive if n != pl.name and n not in all_mafias]
            if cand:
                # 경찰(90%)·의사(70%)를 자처한 사람이 있으면 마피아 AI는 대부분 그 사람을 노린다
                t = self._mafia_claim_target(cand) or _rr.choice(cand)
                results.setdefault("kill", t)
                results["multi"].append(t)
                results["multi_pairs"].append((pl.name, t))
        self.root.after(0, lambda: self._apply_night_actions(results))

        night_no = self.core.day_no
        self._ensure_night_ai_poll()
        for pl in self.ai.players:
            if pl.alive and pl.booted and pl.role == "police":
                self._night_ai_police(pl, night_no)
            elif pl.alive and pl.booted and pl.role == "doctor":
                self._night_ai_doctor(pl, night_no)
        if mafia_ais:
            self.root.after(self.NIGHT_MAFIA_LLM_MS, lambda: self._night_ai_mafia(night_no))

    def _night_ai_active(self, night_no):
        """이 밤이 아직 진행 중인가(늦게 도착한 LLM 답이 다음 낮/밤에 섞이지 않게)."""
        return bool(self.mafia_active and getattr(self, "core", None)
                    and self.core.phase == Phase.NIGHT and self.core.day_no == night_no)

    @classmethod
    def _night_ai_parse(cls, reply, cands):
        """LLM 답에서 후보 이름 하나를 뽑는다(없으면 None)."""
        reply = (reply or "").strip()
        if not reply or not cands:
            return None
        pos, _neg = cls._named_split(reply, cands)
        if pos:
            return pos
        hits = sorted((reply.find(n), n) for n in cands if n in reply)
        return hits[0][1] if hits else None

    def _ensure_night_ai_poll(self):
        """밤 AI 결과 큐 폴러(메인스레드) — 밤이 끝나면 스스로 멈춘다."""
        import queue as _q
        if getattr(self, "_night_ai_q", None) is None:
            self._night_ai_q = _q.Queue()
        if not getattr(self, "_night_ai_poll_on", False):
            self._night_ai_poll_on = True
            self._poll_night_ai_queue()

    def _poll_night_ai_queue(self):
        import queue as _q
        try:
            while True:
                fn = self._night_ai_q.get_nowait()
                try:
                    fn()
                except Exception as e:
                    import applog
                    applog.log("night_ai_apply", exc=e)
        except _q.Empty:
            pass
        if self.mafia_active and self.core.phase == Phase.NIGHT:
            self.root.after(250, self._poll_night_ai_queue)
        else:
            self._night_ai_poll_on = False

    def _night_ai_pick_async(self, pl, prompt, cands, cb):
        """워커 스레드에서 LLM에게 후보 중 하나를 고르게 하고, 결과(cb(pick), 실패 시 None)는
        큐를 거쳐 메인스레드에서 실행한다(워커에서 Tk를 직접 만지지 않는다)."""
        def worker():
            pick = None
            try:
                reply = None
                for _ in range(3):                   # 다른 발화 중(busy)이면 잠깐 뒤 재시도
                    reply = pl.say(prompt)
                    if reply:
                        break
                    time.sleep(1.2)
                pick = self._night_ai_parse(split_chat_tags(clean_llm_dialect(reply or "")), cands)
            except Exception as e:
                import applog
                applog.log("night_ai_llm", exc=e)
            self._night_ai_q.put(lambda: cb(pick))
        threading.Thread(target=worker, daemon=True).start()

    def _night_ai_decide(self, pl, night_no, prompt, cands, apply_fn, fallback_ms=None):
        """후보 중 하나를 LLM이 고르게 하고 apply_fn(target)을 '정확히 한 번' 실행한다.
        LLM 답이 없거나 후보에 없으면 무작위, fallback_ms가 지나도 답이 없으면 그때 무작위."""
        import random as _rr
        if not cands:
            return
        state = {"done": False}

        def finish(target):
            if state["done"] or not self._night_ai_active(night_no):
                return
            state["done"] = True
            apply_fn(target if target in cands else _rr.choice(cands))

        self._night_ai_pick_async(pl, prompt, cands, finish)
        if fallback_ms:
            self.root.after(fallback_ms, lambda: finish(None))

    def _night_ai_police(self, pl, night_no):
        core = self.core
        cands = [n for n in core.alive_players()
                 if n != pl.name and n not in core.police_invest]     # 이미 조사한 사람은 제외
        known = ", ".join(f"{t}={'마피아' if r == 'mafia' else '마피아 아님'}"
                          for t, r in core.police_invest.items()) or "없음"
        prompt = (f"[밤 행동 — 경찰] 오늘 밤 조사할 사람을 한 명 고르세요. 후보: {', '.join(cands)}. "
                  f"지금까지 조사 결과: {known}. 낮 토론에서 수상했던 사람이나 아직 정체를 모르는 "
                  f"사람을 근거로 판단하세요. 답은 오직 '선택 이름' 한 줄.")
        self._night_ai_decide(pl, night_no, prompt, cands,
                              lambda t: self._apply_night_actions({"invest": t}),
                              self.NIGHT_LLM_FALLBACK_MS)

    def _night_ai_doctor(self, pl, night_no):
        core = self.core
        alive = core.alive_players()
        last = getattr(core, "last_protect", None)
        cands = [n for n in alive if n != last] or alive       # 자기 자신 포함, 어젯밤 대상 제외
        claimed = [n for n in pl.public_police_claims() if n in cands] if hasattr(pl, "public_police_claims") else []
        hist = "".join(f" {n}일차 밤: {t}님 보호 → {o}." for n, t, o in getattr(pl, "doctor_log", []))
        prompt = (f"[밤 행동 — 의사] 오늘 밤 마피아에게 노려질 것 같은 사람을 한 명 보호하세요"
                  f"(자신도 가능). 후보: {', '.join(cands)}. 어젯밤 보호한 사람은 연속 보호할 수 "
                  f"없어 후보에서 뺐습니다. 마피아가 제거하고 싶어 할 만한 사람(추리를 잘하거나 "
                  f"의심을 많이 받는 사람)을 근거로 고르세요."
                  + (f" 경찰이라고 밝힌 사람: {', '.join(claimed)} — 마피아가 가장 먼저 노릴 사람입니다." if claimed else "")
                  + (f" 내 지난 밤 기록:{hist}" if hist else "")
                  + " 답은 오직 '선택 이름' 한 줄.")

        def _save(t):
            # 경찰을 자처한 사람이 살아 있으면 대부분 그 사람을 지킨다(마피아 AI가 그 사람을 노리므로)
            import random as _r4
            if claimed and _r4.random() < 0.75:
                t = _r4.choice(claimed)
            self._apply_night_actions({"save": t})

        self._night_ai_decide(pl, night_no, prompt, cands, _save, self.NIGHT_LLM_FALLBACK_MS)

    def _night_ai_mafia(self, night_no):
        """AI 마피아 팀의 살해 대상을 LLM이 다시 고른다. 사람이 이미 정했으면(대화 목표나
        밤 패널 선택) 그 결정이 우선이므로 건드리지 않는다."""
        if not self._night_ai_active(night_no):
            return
        core = self.core
        if getattr(self, "_mafia_kill_plan", None):
            return                                               # 비밀방에서 목표가 나옴
        if any(not (core.players.get(m) or {}).get("is_ai") and m in core.night_targets
               for m in core.mafias()):
            return                                               # 사람 마피아가 패널에서 고름
        ais = [pl for pl in self.ai.players
               if pl.alive and pl.role == "mafia" and pl.booted]
        cands = self._mafia_kill_candidates()
        if not ais or not cands:
            return
        talk = chr(10).join(list(getattr(self, "_mafia_secret_log", []) or [])[-8:]) or "없음"
        prompt = (f"[밤 행동 — 마피아] 오늘 밤 살해할 사람을 한 명 고르세요. 후보(시민 쪽 생존자): "
                  f"{', '.join(cands)}. 마피아 비밀 대화: {talk}. 낮 토론에서 마피아 쪽을 의심하거나 "
                  f"추리가 날카로웠던 사람, 경찰·의사로 짐작되는 사람을 우선 고려하세요. "
                  + (f"경찰이라고 밝힌 사람: {', '.join(n for n in self._live_police_claims() if n in cands)} — 우선 제거하세요. "
                     if any(n in cands for n in self._live_police_claims()) else "")
                  + f"답은 오직 '선택 이름' 한 줄.")

        def apply(target):
            forced = self._mafia_claim_target(cands)
            if forced:
                target = forced        # 경찰·의사를 자처한 사람은 LLM 판단보다 우선 제거 대상
            for pl in ais:
                if (core.players.get(pl.name) or {}).get("alive"):
                    core.mafia_night_vote(pl.name, target)       # 이후 합의 단계에서 AI 지목으로 쓰인다

        self._night_ai_decide(ais[0], night_no, prompt, cands, apply)

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
                    if hasattr(pl_police, "add_intel"):
                        pl_police.add_intel(inv, res)      # 영구 기록 — 이후 발언·투표에 활용
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
        self._mafia_secret_log = []                          # 이번 밤의 마피아 비밀 대화 기록
        self._mafia_kill_plan = None                         # 이번 밤 대화로 정한 살해 목표
        self.root.after(4500, self._mafia_ai_opener)         # 방이 열리면 AI 마피아가 먼저 말을 건다
        self._trigger_night_actions()     # AI 마피아/의사 밤 행동 백그라운드 접수
        # 밤 카운트다운은 thread가 아닌 after 루프로 — daemon 스레드와의 경합 제거
        self._night_resolve_bg()

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
                    font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_BTN_PX,
                    radius=8, pad_x=10, pad_y=4, min_w=90, state=state_val
                )
            self._grid_candidates_centered(row, cand_names, _mk_police_btn, padx=4, pady=4)
            if already:
                tk.Label(body, text=f"이미 조사: {', '.join(already) or '없음'}",
                         fg="#6b7280", bg="#1f2937", font=FONT_SM).pack(pady=(0, 8))
            self._make_night_warn(body)
            self._start_night_pick_countdown(body)
            return
        if role not in ("mafia", "doctor") or not info.get("alive", True):
            return
        act = "살해" if role == "mafia" else "구조"
        body = self._mafia_overlay_open(("🔪 마피아 — 누구를" if role == "mafia" else "💉 의사 — 누구를 ") + act + "?", w=350, h=None)
        row = tk.Frame(body, bg="#1f2937"); row.pack(fill="x", padx=18, pady=10)
        mafia_names = set(self.core.mafias()) if role == "mafia" else set()
        cands = [n for n in self.core.alive_players() if not (role == "mafia" and n in mafia_names)]
        last_healed = getattr(self.core, "last_protect", None) if role == "doctor" else None

        def _mk_night_btn(parent, n):
            lbl = f"{n} (나)" if n == me else n
            if last_healed and n == last_healed:
                # 어젯밤 치료한 사람은 연속으로 치료할 수 없다 — 누르기 전에 미리 표시(중복 선택 방지)
                return emoji_render.make_pill_button(
                    parent, lbl + " · 어제 치료", lambda: None, bg="#1f2937", fg="#6b7280", hover_bg=None,
                    font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_BTN_PX,
                    radius=8, pad_x=10, pad_y=4, min_w=90, state="disabled")
            return emoji_render.make_pill_button(
                parent, lbl, lambda nn=n, r=role: self._apply_night_pick(nn, r),
                bg="#374151", fg="white", hover_bg="#4b5563",
                font_path=emoji_render.FONT_PATH_REGULAR, font_size=POPUP_BTN_PX,
                radius=8, pad_x=10, pad_y=4, min_w=90
            )
        self._grid_candidates_centered(row, cands, _mk_night_btn, padx=4, pady=4)
        if last_healed and last_healed in cands:
            tk.Label(body, text=f"어젯밤 치료한 '{last_healed}'님은 연속으로 치료할 수 없습니다.", fg="#9ca3af",
                     bg="#1f2937", font=FONT_SM, wraplength=320).pack(pady=(0, 4))
        self._make_night_warn(body)
        self._start_night_pick_countdown(body)

    def _make_night_warn(self, body):
        """밤 행동 팝업 안의 경고 줄. 호스트가 선택을 거절하면(의사 중복 치료 등) 채팅이 아니라 이 팝업 안에 표시한다 —
        예전에는 경고가 채팅 쪽지로만 가서 팝업이 가리고 있는 채팅 화면 뒤에 숨어 아무것도 안 보였다."""
        self._night_warn_lbl = tk.Label(body, text="", fg="#fca5a5", bg="#1f2937", font=FONT_SM, wraplength=320,
                                        justify="center")
        self._night_warn_lbl.pack(pady=(0, 6))

    def _night_panel_warn(self, text):
        """열려 있는 밤 행동 팝업에 경고를 띄운다(없으면 아무 일도 하지 않는다)."""
        lbl = getattr(self, "_night_warn_lbl", None)
        if lbl is None or not isinstance(text, str):
            return
        try:
            if not lbl.winfo_exists():
                self._night_warn_lbl = None
                return
            import re as _re
            lbl.config(text=_re.sub(r"^⚠\s*\([^)]*\)\s*", "⚠ ", text))
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
            self._night_warn_lbl = None

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
        # v1.88 — 예전에는 '오버레이가 있는지'만 봤는데, _mafia_overlay_open이 이전 팝업의
        # close()를 부르면서 취소 예약도 같이 하지만(_wrap_overlay_close_with) 이미 큐에서
        # 빠져나와 실행 중이던 이 틱은 취소되지 않는다. 그 순간 다른 밤 행동 팝업이 이미
        # 대신 열려 있으면 '오버레이가 있다'는 것만으로 통과해, 방금 새로 연 팝업(이미
        # 다른 행동을 고르는 중일 수 있음)을 이 낡은 타이머가 "시간 초과"로 잘못 닫아버린다.
        # 이 패널 자신(body가 속한 오버레이)이 지금도 활성 오버레이일 때만 진행한다.
        my_panel = getattr(self, "_mafia_overlay", None)

        def _tick():
            self._night_pick_tick = None
            if getattr(self, "_mafia_overlay", None) is not my_panel:
                return
            state["n"] -= 1
            if state["n"] <= 0:
                self._ghost_dm("⏰ 시간 초과 — 이번 밤은 행동하지 않은 걸로 처리됩니다")
                try:
                    self._mafia_overlay_close()
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._night_pick_tick = None

    def _apply_night_pick(self, name, role):
        me = getattr(self.engine, "name", None)
        if self._mafia_is_host():
            def _notify(text):
                self._ghost_dm(text)
                if isinstance(text, str) and text.startswith("⚠"):
                    self._night_panel_warn(text)            # 팝업 안에도 경고를 띄운다
            ok = self._night_action_apply(me, role, name, _notify)
            if ok:
                self._mafia_overlay_close()
            # v1.29 — 실패(의사 연속보호 등)면 패널을 닫지 않고 유지: 유저가
            # 즉시 다른 대상을 다시 클릭할 수 있게.
        else:
            # v1.61 — 복수 인간 플레이: 클라이언트는 자기 로컬 core만 봐서는
            # 유효성(동료 마피아 여부 등, 남의 역할은 비밀이라 모름)을 정확히
            # 검증할 수 없다 — 호스트에게 보내고 결과는 개인 쪽지로 받는다.
            # 패널은 닫지 않는다 — 호스트가 거절(의사 연속 보호 등)하면 호스트 본인처럼
            # 바로 다른 대상을 다시 고를 수 있어야 하기 때문. 접수 결과 쪽지(hdm)가 오면
            # _close_night_panel_on_ack가 닫고, 안 오면 카운트다운이 닫는다.
            self._ghost_dm("⏳ 밤 행동을 호스트에게 전달했습니다 — 처리 결과는 곧 알려드립니다.")
            if role == "doctor":
                self._pending_heal = name          # 호스트가 접수(💉)해 주면 확정한다
            self._mafia_send_to_host("night_action", actor=me, role=role, target=name)

    def _close_night_panel_on_ack(self, text):
        """원격 참가자 — 호스트의 밤 행동 회신이 '접수'면 선택 패널을 닫는다(⚠ 거절이면 유지)."""
        try:
            if (text or "").startswith(("🔪", "💉", "🕵")) and getattr(self, "_mafia_overlay", None):
                self._mafia_overlay_close()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _night_action_apply(self, actor, role, target, notify):
        """호스트 전용 — 밤 행동(살해/조사/치료)을 실제 권위 core에 반영한다.
        notify(text)로 결과를 알린다(로컬 클릭이면 _ghost_dm, 원격 참가자면
        개인 쪽지 콜백). 반영 성공 여부를 반환.

        요청자(actor)가 주장하는 역할(role)이 권위 core의 실제 역할과 같고, 살아 있고, 지금이
        밤일 때만 받는다. 예전에는 role을 그대로 믿어서, 시민이 자기 이름으로 role="police"를
        보내면 조사 결과를 받고 role="doctor"면 의사의 보호 대상을 덮어쓸 수 있었다."""
        info = self.core.players.get(actor) or {}
        if (role not in ("mafia", "police", "doctor") or info.get("role") != role
                or not info.get("alive", False) or self.core.phase != Phase.NIGHT):
            try:
                applog.log("mafia_night_action_rejected", detail=f"actor={actor} claimed={role} "
                           f"real={info.get('role')} alive={info.get('alive')} phase={self.core.phase}")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            notify("⚠ 지금은 그 밤 행동을 할 수 없습니다")
            return False
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
        notify(f"⚠ ({actor} 의사) {target}님은 어젯밤 이미 치료한 사람입니다 — 중복 치료는 안 됩니다. 다른 대상을 선택하세요")
        return False

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
        규칙: ① 사람 마피아가 고른 게 있으면 그중 '가장 먼저 최종 선택을 끝낸' 사람의 대상이 팀 결정,
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
            # '먼저 고른' 순서는 night_targets에 처음 기록된 순서(=실제로 먼저 고른 순서)다.
            # 예전에는 마피아 명단(입장 순서)을 돌아서, 늘 먼저 입장한 사람의 선택이 이겼다.
            human_picks = [t for m, t in core.night_targets.items()
                           if m in humans and valid(t)]
            plan = getattr(self, "_mafia_kill_plan", None)
            plan_t = plan["target"] if plan else None
            if human_picks:
                team = human_picks[0]
                who = "사람 마피아의 먼저 고른 선택"
            elif plan_t and valid(plan_t):
                team = plan_t
                who = ("비밀방 대화로 정한 대상" if plan["confirmed"]
                       else "비밀방에서 AI 마피아가 제안한 대상")
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

    def _record_doctor_night(self, died, victim):
        """의사 AI에게 어젯밤 결과(내 보호 대상이 노려졌는지 등)를 기록한다 — 이후 발언·선택에 쓰인다."""
        try:
            core = self.core
            protected = getattr(core, "last_protect", None)
            if not protected:
                return
            if died:
                out = f"밤에 {victim}님이 사망했습니다" + ("(내가 지킨 사람입니다 — 보호가 실패했습니다)" if victim == protected else "")
            elif core.night_target and core.night_target == protected:
                out = "마피아가 바로 그 사람을 노렸지만 살려냈습니다(희생자 없음)"
            else:
                out = "희생자가 없었습니다"
            for pl in getattr(self, "ai", None) and self.ai.players or []:
                if pl.role == "doctor" and pl.alive and hasattr(pl, "add_doctor_result"):
                    pl.add_doctor_result(core.day_no - 1, protected, out)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _after_night(self, died, victim):
        if not self.mafia_active:
            return
        self._mafia_room_close()
        self._set_night_theme(False)
        self._sync_ai_alive()   # v1.11 — 밤사망 AI 즉시 발화 차단
        self._record_doctor_night(died, victim)
        if died:
            role2 = self.core.reveal_role(victim)
            if self._mafia_is_host():
                self._mafia_broadcast("day", victim=victim, role=role2)
            self.add_mafia_system(f"🕯 밤 사망 — {victim}")
            self.add_mafia_host(
                f"아침이 밝았습니다… 유감스럽게도 '{victim}' 님의 자리가 비었습니다.")
            if role2:
                self.add_mafia_system(f"🎭 직업 공개 — {victim} ({_role_was(role2)})")
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
            # v1.88 — 예전에는 "내가 호스트 경찰인가" → "AI 경찰이 있는가" 두 갈래뿐이었다.
            # 경찰이 원격 인간이면 둘 다 아니라서 이 아침 통보가 아무 데도 가지 않는 죽은
            # 분기였다(그 순간까지 실제로는 조사 클릭 시점의 hdm 쪽지로만 전달돼 왔다 —
            # 만약 앞으로 사람 경찰도 밤 자동 대체 경로를 타게 되면 여기서 놓치게 된다).
            # 지금 실제로 경찰 역할을 쥔 사람을 명단에서 직접 찾아 그 사람 기준으로 보낸다.
            police_name = next((n for n, p in self.core.players.items()
                                if p.get("role") == "police"), None)
            pl_police = next((pl for pl in getattr(self, "ai", None) and self.ai.players or []
                              if pl.role == "police"), None)
            if police_name == me:
                self._ghost_dm(f"🕵 [밤 조사 결과 — 나에게만] {victim_t}님은 {verdict}")
            elif pl_police and (self.core.players.get(police_name) or {}).get("is_ai"):
                # AI 경찰 — 기억에 적립(발화 참조용, 노출 금지 지시 포함)
                if hasattr(pl_police, "add_intel"):
                    pl_police.add_intel(victim_t, res)
                pl_police.memory.append(
                    {"role": "user",
                     "content": f"[사회자 밤 비밀 통보 — 절대 채팅에 노출 금지] "
                                f"조사 결과: {victim_t} = {verdict}"})
            elif police_name:
                # 원격 인간 경찰 — 조사 클릭 시점에 이미 hdm으로 받았겠지만(v1.85), 앞으로
                # 생길 수 있는 자동 대체 경로까지 대비해 항상 명시적으로 다시 보낸다
                # (중복 수신은 host-경찰과 마찬가지로 무해하다).
                self._mafia_send_private(police_name, "hdm", target=police_name,
                                         text=f"🕵 [밤 조사 결과 — 나에게만] {victim_t}님은 {verdict}")
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
