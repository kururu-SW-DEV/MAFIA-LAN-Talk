# -*- coding: utf-8 -*-
"""mafia_ui_ai.py — 마피아 게임방 UI 믹스인 (AI 대화: 사용자 발언 처리, 멘션 답변, AI 반응·잡담).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaAIChatMixin:
    """AI 대화: 사용자 발언 처리, 멘션 답변, AI 반응·잡담"""

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
            if my_role == "mafia" and me not in self.core.night_targets and not self.core.night_target:
                self.add_mafia_system("(안내) 마피아는 '살해 이름'으로 밤 행동을 알려야 합니다.")
            elif my_role == "doctor" and not self.core.night_saved:
                self.add_mafia_system("(안내) 의사는 '구조 이름'으로 밤 행동을 알려야 합니다.")
            elif my_role == "police" and not self.core.police_report:
                self.add_mafia_system("(안내) 경찰은 '조사 이름'으로 밤 행동을 알려야 합니다.")
            return
        if self.core.phase == Phase.DAY:
            # '@이름'만 친 것은 멘션(AI 호출)이지 투표가 아니다 — 투표는 '투표 이름'으로만
            m = re.match(r"^(?:투표|지목|vote)\s+([^\s]+)$", text.strip())
            if m:
                target = m.group(1).strip()
                me = self.engine.name
                ok = self.core.cast_vote(me, target)
                if ok:
                    # v1.47 — 대상 이름을 내 말풍선에도 남기지 않는다(익명 개표와 완전히 일치시켜
                    # '정말 비공개 맞나' 하는 의심을 원천 차단). 자기 자신은 로컬에서만 보이는
                    # 화면이라 실제로 새어나간 적은 없지만, 눈에 보이는 문구부터 통일한다.
                    _pd, _pt = self._vote_progress_counts()
                    self.add_mafia_system(f"🗳 {me}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
                    self._refresh_vote_progress_label()
                    if self._mafia_is_host():
                        self._broadcast_vote_done(me, target)
                    else:
                        self._mafia_send_to_host("vote_cast", voter=me, target=target)
                    # v1.18 — 투표 팝업이 열려 있으면 즉시 닫기 + 잔여 버튼 잠금
                    try:
                        if getattr(self, "_vote_lbl", None):
                            self._cancel_vote_popup()
                        if getattr(self, "_mafia_overlay", None) is not None:
                            self._mafia_overlay_close()
                        self._update_vote_btn_state()
                    except Exception as _swallow_e:
                        applog.swallowed(_swallow_e)
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
        self._ai_hear_human(self.engine.name, text)

    # "나 경찰이야" 같은 커밍아웃 — 부인("경찰 아니야")은 제외하고 문장 끝맺음까지 요구해 오탐을 줄인다.
    _POLICE_CLAIM_RE = re.compile(
        r"(?:나|저|내가|제가|난|전)(?:는|은|가)?\s*(?:진짜\s*|바로\s*|사실\s*)?경찰"
        r"(?:이야|이에요|이예요|입니다|임|이거든|이다|이라고|인데|이니까|이지|이라니까|이란|이라)")
    _POLICE_DENY_RE = re.compile(r"경찰(?:이|은)?\s*(?:아니|아냐|아님|아닌|아닙)")

    def _note_police_claim(self, speaker, text):
        """누군가 스스로 경찰이라고 밝히면 기록한다(마피아 AI가 그 사람을 표적으로 삼는다). 호스트 전용."""
        if not (getattr(self, "mafia_host_mode", False) and isinstance(text, str) and speaker):
            return
        if self._POLICE_DENY_RE.search(text) or not self._POLICE_CLAIM_RE.search(text):
            return
        info = (getattr(self, "core", None) and self.core.players.get(speaker)) or None
        if info and info.get("alive", True):
            self.__dict__.setdefault("_police_claims", {})[speaker] = getattr(self.core, "day_no", 1)

    def _live_police_claims(self):
        """경찰을 자처했고 아직 살아 있으며 마피아 팀이 아닌 사람(마피아 AI의 표적 후보)."""
        core = getattr(self, "core", None)
        if core is None:
            return []
        mafias = set(core.mafias())
        return [n for n in list(getattr(self, "_police_claims", {}))
                if (core.players.get(n) or {}).get("alive", True) and n not in mafias]

    def _ai_hear_human(self, speaker, text):
        """사람 참가자의 발언에 AI가 반응하게 한다. 이 PC의 사용자든 원격 참가자든 똑같이 처리한다.
        예전에는 호스트 사용자의 발언에만 반응하고 원격 참가자의 발언(user_say)은 화면에 표시·기억만
        해서, AI들이 원격 참가자에게 관심도 없고 대화도 걸지 않았다."""
        if not getattr(self, "ai", None) or not getattr(self, "core", None):
            return
        self._note_police_claim(speaker, text)
        try:
            self._human_last_talk = getattr(self, "_human_last_talk", None) or {}
            self._human_last_talk[speaker] = time.time()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
        _threat_kw = mafia_config.THREAT_KEYWORDS
        if self.core.phase in (Phase.DAY, Phase.VOTE) and any(k in text for k in _threat_kw):
            live = [pl for pl in self.ai.players if pl.alive and getattr(pl, "booted", False)]
            if live:
                pick = random_mod.choice(live)
                self.root.after(600, lambda p=pick, who=speaker: self._ai_threat_reaction(p, who))
        if self.core.phase == Phase.DAY:
            self._ai_chain_count = 0
            # 이름 직접 호출 → 불린 AI만 대답, 아니면 일반 상황 반응 1회
            mentioned = self._detect_mention(text)
            if mentioned:
                self._trigger_mentions_reply(mentioned, text, speaker)
            else:
                self._trigger_ai_reactions(
                    context=f"플레이어 '{speaker}'의 발언: \"{text}\"",
                    min_interval=5, max_replies=AI_REACT_MAX_REPLIES)

    def _ai_ask_human(self):
        """AI 한 명이 가장 오래 말이 없던 사람 참가자에게 먼저 말을 건다(사람 발언을 기다리지 않는다).
        조용한 원격 참가자가 AI 대화에서 소외되지 않게 한다. 말을 걸었으면 True."""
        if not (self.mafia_active and self.core.phase == Phase.DAY and getattr(self, "ai", None)):
            return False
        humans = [n for n, p in self.core.players.items()
                  if not p.get("is_ai") and p.get("alive", True)]
        if not humans:
            return False
        talk = getattr(self, "_human_last_talk", None) or {}
        now = time.time()
        target = min(humans, key=lambda n: talk.get(n, 0))
        if now - talk.get(target, 0) < 20:
            return False                      # 방금 말한 사람에게는 다시 걸 필요 없다
        cands = [pl for pl in self.ai.players
                 if pl.alive and getattr(pl, "booted", False) and not getattr(pl, "busy", False)]
        if not cands:
            return False
        pl = random_mod.choice(cands)
        alive = ", ".join(self.core.alive_players())
        def factory(p):
            return (f"[사람에게 말 걸기] 현재 낮 {self.core.day_no}, 생존: {alive}\n"
                    f"'{p.name}'로서 사람 참가자 '{target}'님에게 직접 말을 거세요: 이름을 부르며 "
                    f"지금까지의 대화·행동에 대한 질문을 하나 던지거나 의견을 물으세요. "
                    f"2문장 이내, 혼잣말/규칙 설명 금지.\n" + self._NO_PILE_ON)
        self._human_last_talk = talk
        talk[target] = now - 10               # 연속으로 같은 사람만 조르지 않도록 살짝 갱신
        self.ai.say_one_async(pl, factory)
        return True

    def _detect_mention(self, text):
        """유저 메시지에 AI 이름이 직접 포함되면 해당 AI 목록. @이름/'OOO아' 포함."""
        if not getattr(self, "ai", None):
            return None
        hits = []
        for pl in self.ai.players:
            if not pl.alive or not pl.booted:
                continue
            nm = pl.name
            # 이름 전체가 들어 있을 때만 지목으로 본다('미나야'·'@미나'·'미나가' 모두 포함).
            # 예전엔 이름의 첫 글자만('반대'→반장, '미안'→미나) 있어도 지목으로 봐서, 지목된 AI가
            # 다구리 상한을 우회해 반드시 대답했다.
            if nm and nm in text:
                hits.append(pl)
        return hits or None

    def _trigger_mentions_reply(self, mentioned, user_text, speaker=None):
        """이름 불린 AI는 반드시 그 사람에게 대답(순차 지연 틈 후 비동기)."""
        alive = ", ".join(self.core.alive_players())
        speaker = speaker or self.engine.name
        for idx, pl in enumerate(mentioned):
            factory = (lambda pl=pl: (
                f"[직접 지목] '{speaker}'님이 당신('{pl.name}')의 이름을 불렀습니다.\n"
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
                f"[게임 상황] '{speaker}' 님이 '{mentioned[0].name}'님에게 "
                f"말했습니다: \"{user_text}\"\n"
                f"당신('{pl.name}')은 그 대화에 곁에서 한마디만 보태세요. 2문장 이내."))
            self.root.after(1900, lambda f=factory2: self.ai.say_async(f))

    def _ai_threat_reaction(self, pl, speaker=None):
        """v1.30 — 유저 협박 발언에 대한 AI 불쾌 반응 발화(1명, 1회).
        LLM 없이 인격 톤 문구 즉결 + AI 기억에 '협박' 사실 적립(투표/찬반 참조)."""
        import random as _rr2
        try:
            me_u = speaker or getattr(self.engine, "name", "")
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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _show_ai_typing_hint(self):
        """AI 발화 대기 표시 — 애니메이션 '입력 중' 말풍선 (직접 API라 4~6초 내 응답)."""
        who = random_mod.choice([pl.name for pl in self.ai.players
                                 if pl.alive and pl.booted] or ["AI"])
        self.add_mafia_system(f"💭 {who}님이 생각 중… (약 4~6초)")

    # 한 사람에게 몰아붙이는 분위기를 막는 공통 지시문(반응/연쇄/투표 프롬프트에 덧붙인다)
    _NO_PILE_ON = ("[중요] 한 사람에게만 집중해서 공격·의심하지 마세요. 반박만 하지 말고 동의·질문·농담·"
                   "다른 참가자 언급도 섞고, 이미 다른 사람이 그 사람을 몰아가고 있으면 같이 몰지 말고 "
                   "다른 시선이나 변호를 보태세요.")

    def _trigger_ai_reactions(self, context, min_interval=0, prefix="", max_replies=None):
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
                    f"  2) 동의/반박/질문/농담 중 자연스러운 태도를 고르세요(무조건 의심·반박하지 말 것).\n"
                    f"혼잣말/게임 규칙 설명은 금지. 2문장 이내.\n" + self._NO_PILE_ON)
        if max_replies is None:
            self.ai.say_async(factory)          # 게임 상황 전환 등: 예전처럼 여러 명이 반응
        else:
            # v1.61 — 사람 발언 하나에 AI가 우르르 대답해 다구리처럼 느껴지던 것을,
            # 대답하는 AI를 상한(기본 1명, 가끔 아예 무반응)으로 줄인다. 이어지는 대화는
            # 기존 연쇄 반응(_on_ai_utt → chain replier)이 AI끼리 이어 간다.
            import random as _rr
            cands = [pl for pl in self.ai.players
                     if pl.alive and getattr(pl, "booted", False) and not getattr(pl, "busy", False)]
            if not cands or _rr.random() < AI_REACT_SKIP_PROB:
                return
            picks = []
            pool = list(cands)
            while pool and len(picks) < max(1, int(max_replies)):
                w = [max(1, getattr(p, "freq", 50)) for p in pool]
                p = _rr.choices(pool, weights=w, k=1)[0]
                picks.append(p); pool.remove(p)
            for i, pl in enumerate(picks):
                self.root.after(int(i * 900), lambda p=pl: self.ai.say_one_async(p, factory))
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
            f"혼잣말 금지, 2문장 이내.\n" + self._NO_PILE_ON))
        self.ai.say_one_async(pick, factory)

    def _ai_vs_ai_banter(self):
        """AI 한 명이 '다른 AI'를 지목해 캐묻고, 지목당한 AI가 받아치게 한다(사람 제외)."""
        if not (self.mafia_active and self.core.phase == Phase.DAY and getattr(self, "ai", None)):
            return
        if random_mod.random() < 0.4 and self._ai_ask_human():
            return
        cands = [pl for pl in self.ai.players
                 if pl.alive and getattr(pl, "booted", False) and not getattr(pl, "busy", False)]
        if len(cands) < 2:
            return
        a, b = random_mod.sample(cands, 2)
        alive = ", ".join(self.core.alive_players())
        def fa(pl):
            return (f"[AI끼리 토론] 현재 낮 {self.core.day_no}, 생존: {alive}\n"
                    f"'{pl.name}'로서 참가자 '{b.name}'에게 직접 말을 거세요: 그 사람의 앞선 "
                    f"발언/행동에서 수상하거나 앞뒤가 안 맞는 점 하나를 짚어 캐묻거나 의심하세요. "
                    f"'{b.name}'의 이름을 부를 것. 2문장 이내, 혼잣말/규칙 설명 금지.\n" + self._NO_PILE_ON)
        def fb(pl):
            return (f"[AI끼리 토론] '{a.name}'가 당신에게 의심을 던졌습니다. 최근 대화를 떠올려 "
                    f"'{pl.name}'로서 변명하거나 맞받아치며 '{a.name}'의 허점을 되짚으세요. "
                    f"2문장 이내.\n" + self._NO_PILE_ON)
        self.ai.say_one_async(a, fa)
        self.root.after(random_mod.randint(4500, 7000),
                        lambda: self.ai.say_one_async(b, fb)
                        if (self.mafia_active and self.core.phase == Phase.DAY and b.alive) else None)

    def _on_ai_utt(self, name, color, text):
        # LLM 응답은 수~수십 초 뒤에 오므로 그 사이 죽었거나 다음 판으로 넘어간 AI의 발언은 버린다.
        core = getattr(self, "core", None)
        if self.mafia_active and core is not None:
            info = core.players.get(name)
            if not info or not info.get("is_ai") or not info.get("alive", True):
                return
        self.root.after(0, lambda: self.add_mafia_ai(name, text))
        self._note_police_claim(name, text)      # AI 경찰의 커밍아웃도 마피아 AI가 듣는다
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
