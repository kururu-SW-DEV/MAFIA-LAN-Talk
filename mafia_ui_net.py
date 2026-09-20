# -*- coding: utf-8 -*-
"""mafia_ui_net.py — 마피아 게임방 UI 믹스인 (네트워크: [MAFIA1] 송수신·송신자 검증, 호스트↔클라이언트 동기화, 접속 끊김 감시).

mafia_ui.MafiaUIMixin이 다른 믹스인과 합쳐 쓴다. 모듈 전역 이름(import·상수·헬퍼)은
mafia_ui_common에서 가져온다.
"""
from mafia_ui_common import *  # noqa: F401,F403


class MafiaNetMixin:
    """네트워크: [MAFIA1] 송수신·송신자 검증, 호스트↔클라이언트 동기화, 접속 끊김 감시"""

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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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

    def _client_start_day_countdown(self):
        """v1.61 — 원격 참가자도 낮 남은 시간을 볼 수 있게 표시 전용 카운트다운을
        로컬에서 돌린다(개표/개행 판정은 하지 않음 — 그건 호스트 몫)."""
        t = getattr(self, "_day_tick", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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

    # 방장(호스트)만 보낼 수 있는 이벤트 — 다른 사람이 보내면 폐기한다.
    _HOST_ONLY_EVENTS = frozenset({
        "hsay", "asay", "sys", "hdm", "start", "night", "day", "death", "end", "tally",
        "vote", "vote_open", "revote_open", "defense_vote_open", "defense_start", "verdict",
        "recruit_start", "recruit_update", "recruit_cancel", "ghost_say"})

    # 참가자가 자기 이름으로 보내는 이벤트 → 본문에 적힌 '누구'가 실제 송신자와 같아야 한다.
    _PEER_CLAIM_FIELD = {
        "user_say": "name", "lobby_chat": "sender", "mafia_say": "name",
        "vote_cast": "voter", "defense_vote_cast": "voter", "night_action": "actor",
        "mafia_to_ai": "name", "ghost_to_ai": "name",
        "recruit_join": "name", "recruit_leave": "name"}

    def _sender_is(self, claimed, sender_name, peer=None):
        """본문이 주장하는 이름(claimed)이 실제 송신자인지. 표시 이름이 같거나, 그 이름의
        접속 상대(peer)가 이 패킷을 보낸 상대와 같으면(별칭을 붙여 이름이 달라진 경우) 인정."""
        if not claimed:
            return False
        if claimed == sender_name:
            return True
        if peer is not None:
            try:
                key = self._mafia_peer_of(claimed)
                return key is not None and tuple(key) == tuple(peer)
            except Exception:
                return False
        return False

    def _proto_authorized(self, t, ev, sender_name, peer=None):
        """[MAFIA1] 이벤트의 송신자 검증. 같은 LAN의 누구든 가짜 '게임 종료/역할 통보' 같은
        방장 전용 패킷이나 남의 이름으로 된 투표·밤 행동을 보낼 수 있던 문제를 막는다."""
        me = getattr(self.engine, "name", None)
        host = getattr(self, "_recruiter_host", None)
        am_host = bool(self._mafia_is_host() or (host and host == me))
        if t in self._HOST_ONLY_EVENTS:
            if am_host:
                return False            # 방장에게 방장 전용 이벤트가 올 이유가 없다
            if t == "recruit_start":
                # 모집을 여는 사람은 본인을 방장으로 알려야 하고, 진행 중인 판의 방장은 바꿀 수 없다
                if not self._sender_is(ev.get("host"), sender_name, peer):
                    return False
                if getattr(self, "mafia_active", False) and host                         and not self._sender_is(host, sender_name, peer):
                    return False
                return True
            return bool(host) and self._sender_is(host, sender_name, peer)
        field = self._PEER_CLAIM_FIELD.get(t)
        if field:
            if t == "mafia_say" and host and self._sender_is(host, sender_name, peer):
                return True             # 방장이 중계하는 AI 마피아 발언·목표 안내
            return self._sender_is(ev.get(field), sender_name, peer)
        return True

    def _broadcast_vote_done(self, voter, target):
        """투표가 '접수됐다'만 알린다(대상은 싣지 않는다) — 화면뿐 아니라 패킷·로그로도 익명."""
        self._mafia_broadcast("vote", voter=voter, abstain=(target is None))

    def _on_mafia_proto_msg(self, text, sender_name, peer=None):
        """[MAFIA1] 메시지 수신시. 송신자 검증을 통과한 것만 처리한다."""
        try:
            from mafia_net import decode
            ev = decode(text)
            if not ev:
                return False
        except Exception:
            return False
        t = ev.get("t")
        if not self._proto_authorized(t, ev, sender_name, peer):
            try:
                import applog
                applog.log("mafia_proto_rejected", detail=f"t={t} from={sender_name}")
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            return False
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
        elif t == "ghost_to_ai":
            # 원격 사망자가 유령방에 쓴 말을 호스트의 사망 AI에게 전달
            spk = ev.get("name")
            info = self.core.players.get(spk) or {}
            if (self._mafia_is_host() and spk and ev.get("text") and self.mafia_active
                    and not info.get("is_ai") and not info.get("alive", True)):
                self._ghost_ai_reply(ev.get("text", ""), speaker=spk)
        elif t == "ghost_say":
            # 호스트의 사망 AI가 보낸 유령방 답장(개인 전송) — 내 유령방이 열려 있을 때만 표시
            if ev.get("text") and getattr(self, "_ghost_list", None):
                self._append_ghost(f"👻 {ev.get('name') or '?'}: {ev.get('text')}", ai=True)
        elif t == "mafia_to_ai":
            # 원격 사람 마피아가 비밀방에 쓴 말을 호스트의 AI 마피아에게 전달
            spk = ev.get("name")
            if (self._mafia_is_host() and spk and ev.get("text")
                    and (self.core.players.get(spk) or {}).get("role") == "mafia"
                    and (self.core.players.get(spk) or {}).get("alive", True)):
                self._mafia_ai_respond(spk, ev.get("text", ""))
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        elif t == "day":
            self._unlock_defense_entry()
            self._set_night_theme(False)
            # v1.61 — 밤 결과(사망자/역할 공개) 동기화 + 남아있는 내 밤 행동
            # 패널 정리(호스트가 이미 밤을 끝냈는데 원격 화면엔 패널이 계속
            #떠 있던 문제 방지).
            try:
                self._mafia_overlay_close()
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
                # 새 호스트는 대상을 싣지 않는다(익명). 옛 호스트가 보낸 target은 무시한다.
                # 예전 호스트의 기권은 target=None으로 온다 — 그것만 기권으로 본다.
                if ev.get("abstain") or ("target" in ev and not ev.get("target")):
                    self.core.cast_abstain(v)
                else:
                    # 누가 '냈는지'만 표시(진행률용). 이미 내 표가 기록돼 있으면 덮어쓰지 않는다.
                    self.core.votes.setdefault(v, "")
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
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
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
                        "게임이 멈추지 않도록 그대로 진행합니다.")
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
                    if p.get("alive", True):
                        self.add_mafia_system(f"✅ {name}님이 다시 연결되었습니다.")
                    else:
                        self.add_mafia_system(
                            f"✅ {name}님이 다시 연결되었습니다 — 접속이 끊긴 사이 사망 처리되어 "
                            "이번 판은 관전만 할 수 있습니다.")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
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
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
                setattr(self, attr, None)

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
        self._broadcast_vote_done(voter, target)
        if revote:
            self._check_revote_done()
        elif self.core.all_voted():
            self._schedule_tally(300)

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

    def _sync_ai_alive(self):
        """v1.11 — core의 alive 정보를 PlayerAgent.alive에 동기화.
        (저번 턴에 죽은 AI가 다음 턴에 말하는 것 방지.)"""
        try:
            core_alive = set(self.core.alive_players())
            for pl in getattr(self, "ai", None) and self.ai.players or []:
                if pl.name not in core_alive:
                    pl.alive = False
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

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
