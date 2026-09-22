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
        """게임 이벤트를 접속 피어에게 DM 프로토콜로 전송(호스트 모드에서만).

        v1.88 — 게임이 시작된 뒤(mafia_active)에는 명단(core.players)에 있는 실제 참가자에게만
        보낸다. 예전에는 모집 신청 여부와 무관하게 LAN에서 발견된 모든 피어(eng.peers)로 보냈다
        — 그러면 직업 공개(🎭)를 포함한 매 시스템 줄·투표 완료 알림이 게임에 참가하지도 않은
        구경꾼의 클라이언트에도 도착했다(화면 표시만 _in_game으로 걸러졌을 뿐, 패킷 자체는 갔다).
        모집 중(recruit_*)에는 아직 명단이 없으므로 예전처럼 LAN 전체로 보낸다 — 그게 곧
        모집 광고 그 자체다."""
        try:
            from mafia_net import encode
            pkt = encode(ev_type, **kw)
            if not pkt:
                return
            eng = getattr(self, "engine", None)
            if eng is None:
                return
            core = getattr(self, "core", None)
            if self.mafia_active and core is not None:
                me = getattr(eng, "name", None)
                targets = set()
                for n, p in list(core.players.items()):
                    if p.get("is_ai") or n == me:
                        continue
                    ip_port = self._mafia_peer_of(n)
                    if ip_port:
                        targets.add(ip_port)
                peers = list(targets)
            else:
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

    def _mafia_peer_raw(self, name):
        """접속 상대 목록에서 표시 이름(별칭)이 같은 '첫' 상대의 (ip, port). 이름만 보는 조회다."""
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

    def _mafia_peer_of(self, name):
        """이름에 대응하는 (ip, port). 이미 접속 주소에 묶인 이름(_mafia_ident)이면 그 주소를 쓴다.

        예전에는 언제나 '표시 이름이 같은 첫 피어'에게 보내서, 표시 이름을 피해자와 같게 하고 먼저
        접속한 사람이 직업 통보·마피아 비밀 대화·경찰 조사 결과를 대신 받을 수 있었다(수신 검증만
        고치고 송신 경로는 그대로였던 구멍). 묶인 주소가 없을 때만 이름으로 찾는다."""
        bound = self._ident().get(name)
        if bound is not None:
            return bound
        return self._mafia_peer_raw(name)

    def _mafia_name_ambiguous(self, name):
        """지금 접속 중인 상대 중 같은 이름(별칭)을 쓰는 사람이 둘 이상인가. 그러면 누가 진짜인지
        알 수 없어 이름을 어느 한쪽 주소에 묶을 수 없다."""
        eng = getattr(self, "engine", None)
        if eng is None or not name:
            return False
        try:
            now = time.time()
            n = 0
            with eng.plock:
                for (ip, port), p in eng.peers.items():
                    if now - p.get("last", 0) >= PEER_TIMEOUT:
                        continue                       # 오래 소식 없는 옛 항목은 세지 않는다
                    if (eng.get_alias((ip, port)) or "") == name or p.get("name", "") == name:
                        n += 1
            return n > 1
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
            return False

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
        self._reset_ghost_state()
        self._play_mafia_sound("citizen_win" if winner == "citizen" else "mafia_win")
        self.add_mafia_system(f"⚖ 게임 종료 — {label} 팀 승리!")
        if roles and isinstance(roles, dict):
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
        self.refresh_mafia_phase_label()
        self._mafia_pack_lobby_buttons()

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
        self._vote_window = False
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
        tok = getattr(self, "_my_tok", None)
        if tok and ev_type in self._TOKEN_EVENTS:
            kw["tok"] = tok
        self._mafia_send_private(host, ev_type, **kw)
        if ev_type in ("vote_cast", "defense_vote_cast"):
            self._expect_host_ack(ev_type, kw)

    _ACK_WAIT_MS = 4000

    def _expect_host_ack(self, ev_type, kw):
        """내 표를 호스트가 실제로 접수했는지 확인한다. 예전에는 보내기만 하고 "투표 완료"를 띄워서, 호스트가
        표를 버려도(버전 불일치·인증 실패·유실) 나는 성공한 줄 알았고 호스트는 집계가 안 됐다.
        호스트가 접수하면 "✅ 방장이 내 표를 접수했습니다" 쪽지를 돌려준다. 4초 안에 없으면 한 번 다시 보내고,
        그래도 없으면 원인을 안내한다."""
        pend = self.__dict__.setdefault("_ack_pending", {})
        pend[ev_type] = (dict(kw), 0)
        try:
            self.root.after(self._ACK_WAIT_MS, lambda: self._check_host_ack(ev_type))
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _check_host_ack(self, ev_type):
        pend = getattr(self, "_ack_pending", {})
        item = pend.get(ev_type)
        if not item or not getattr(self, "mafia_active", False) or self._mafia_is_host():
            pend.pop(ev_type, None)
            return
        kw, tries = item
        host = getattr(self, "_recruiter_host", None)
        label = "찬반 표" if ev_type == "defense_vote_cast" else "투표"
        if tries >= 1 or not host:
            pend.pop(ev_type, None)
            if getattr(self, "core", None) and self.core.phase not in (Phase.DAY, Phase.VOTE):
                return                       # 이미 다음 단계로 넘어갔다 — 경고할 상황이 아니다
            self.add_mafia_system(
                f"⚠ 방장이 내 {label}를 접수했다는 확인이 없습니다. 방장과 같은 버전(v1.76 이상)인지, "
                "방장과 연결이 끊기지 않았는지 확인하세요. 방장 화면에 '접수'가 안 뜨면 집계되지 않은 것입니다.")
            return
        pend[ev_type] = (kw, tries + 1)
        try:
            self._mafia_send_private(host, ev_type, **kw)      # 한 번 더 보낸다(호스트는 같은 표를 덮어쓸 뿐)
            self.root.after(self._ACK_WAIT_MS, lambda: self._check_host_ack(ev_type))
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _clear_host_ack(self, text):
        pend = getattr(self, "_ack_pending", None)
        if not pend:
            return
        pend.pop("defense_vote_cast" if "찬반" in text else "vote_cast", None)

    # 토큰이 있어야 하는 참가자→호스트 이벤트(투표·찬반·밤 행동)
    _TOKEN_EVENTS = frozenset({"vote_cast", "defense_vote_cast", "night_action"})

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

    def _ident(self):
        """이름 → 접속 주소(ip, port) 묶음. 새 모집이 시작될 때마다 비운다."""
        m = getattr(self, "_mafia_ident", None)
        if m is None:
            m = self._mafia_ident = {}
        return m

    def _reset_ident(self):
        """새 모집 — 이전 판의 이름·접속 주소 묶음과 사칭 안내 기록을 함께 비운다."""
        self._mafia_ident = {}
        self._mafia_ident_noted = set()
        self._mafia_tokens = {}      # 호스트: 참가자 이름 → 비밀 토큰
        self._my_tok = None          # 참가자: 호스트가 개인 쪽지로 준 토큰

    _IDENT_NOTICE_MAX = 5      # 한 모집 동안 사용자에게 알리는 사칭 안내의 최대 횟수

    def _note_impersonation(self, claimed, key):
        """이미 다른 접속 주소에 묶인 이름으로 요청이 왔을 때(사칭 또는 주소 변경) 알린다.

        안내는 (1) 이름별로 한 번만, (2) 한 모집당 최대 _IDENT_NOTICE_MAX번, (3) 이 PC 화면에만
        표시한다. 예전에는 (이름, 주소·포트)마다 알렸는데 포트는 보내는 쪽이 정하는 값이라 포트만
        바꿔 보내면 매번 새 안내가 되고, 그 안내가 호스트에서 전원에게 브로드캐스트돼 도배할 수 있었다."""
        seen = getattr(self, "_mafia_ident_noted", None)
        if seen is None:
            seen = self._mafia_ident_noted = set()
        if claimed in seen or len(seen) >= self._IDENT_NOTICE_MAX:
            return
        seen.add(claimed)
        try:
            applog.log("mafia_ident_mismatch",
                       detail=f"name={claimed} from={key} bound={self._ident().get(claimed)}")
            self.add_mafia_host_dm(
                f"⚠ '{claimed}' 이름으로 온 요청이 처음 확인된 접속 주소와 달라 무시했습니다"
                f"({key[0]}). 다른 PC가 이름을 사칭했거나 그 사람의 접속 주소가 바뀐 경우입니다.")
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _claim_matches(self, claimed, sender_name, peer=None):
        """이름 주장이 이 패킷의 송신자와 맞는지만 본다(묶음을 읽지도 쓰지도 않는다)."""
        if not claimed:
            return False
        if peer is None or claimed == sender_name:
            return claimed == sender_name
        try:
            pk = self._mafia_peer_raw(claimed)
            return pk is not None and tuple(pk) == tuple(peer)
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)
            return False

    def _sender_is(self, claimed, sender_name, peer=None):
        """본문이 주장하는 이름(claimed)이 실제 송신자인지.

        패킷 본문의 이름은 보내는 사람이 마음대로 적을 수 있어(엔진도 그대로 읽는다) 그것만 믿으면
        이름만 바꿔 사칭할 수 있다. 그래서 이름을 '처음 확인된 접속 주소(ip, port)'에 묶고,
        한번 묶인 이름은 그 주소에서 온 것만 인정한다(표시 이름이 같아도 다른 주소면 거부).
        처음 보는 이름은 표시 이름이 같거나 그 이름의 접속 상대(peer)가 이 패킷의 송신자와
        같을 때(별칭으로 이름이 달라진 경우) 인정하고 그 주소에 묶는다."""
        if not claimed:
            return False
        if peer is None:
            return claimed == sender_name      # peer 정보가 없는 호출(단위 테스트 등)은 이름 비교만
        key = tuple(peer)
        ident = self._ident()
        bound = ident.get(claimed)
        if bound is not None:
            if bound == key:
                return True
            self._note_impersonation(claimed, key)
            return False
        ok = claimed == sender_name
        if not ok:
            try:
                pk = self._mafia_peer_of(claimed)
                ok = pk is not None and tuple(pk) == key
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        if ok:
            ident[claimed] = key               # 처음 확인된 접속 주소에 이름을 묶는다
        return ok

    _STALE_GAME_SECONDS = 180      # 방장에게서 이 시간 넘게 아무 패킷도 없고 접속 목록에도 없으면 판이 끝난 것으로 본다

    @staticmethod
    def _name_list(v):
        """패킷으로 온 명단을 문자열 이름 목록으로만 정규화(잘못된 값은 버린다)."""
        if not isinstance(v, (list, tuple)):
            return []
        return [n for n in v if isinstance(n, str) and n][:MAX_PLAYERS]

    def _arm_client_defense_guard(self):
        """원격 참가자 안전망 — 방장이 판결(verdict)을 못 보내고 사라져도 변론 중 발언 잠금이 영영 안 풀리는 일을 막는다."""
        self._cancel_client_defense_guard()
        self._defense_client_guard = self.root.after(
            (60 + DEFENSE_VOTE_WINDOW + 15) * 1000, self._client_defense_timeout)

    def _cancel_client_defense_guard(self):
        t = getattr(self, "_defense_client_guard", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        self._defense_client_guard = None

    def _clear_client_defense(self):
        self._cancel_client_defense_guard()
        self._defense_in_progress = False

    def _client_defense_timeout(self):
        self._defense_client_guard = None
        if self._mafia_is_host() or not getattr(self, "_defense_in_progress", False):
            return
        self._defense_in_progress = False
        self.core.defendant = None
        self._unlock_defense_entry()
        self.add_mafia_system("⚠ 방장의 판결 결과가 오지 않아 변론 화면을 정리했습니다.")

    def _clear_stale_host_state(self):
        """새 모집 알림을 받기 전에, 지난 판이 비정상적으로 끝나 남은 상태를 걷어낸다.
        남아 있으면 이 PC가 아직 방장이거나 진행 중인 판이 있다고 믿어 새 방장의 모집 알림을
        전부 버리고, 그러면 [참가 신청] 버튼이 뜨지 않는다.
          - 방장 신분인데 모집도 게임도 진행 중이 아님(게임 시작 실패·비정상 종료 등)
          - 클라이언트인데 진행 중이라 믿는 판의 방장이 오래 조용하고 접속 목록에도 없음(종료 알림을 놓침)"""
        try:
            recruiting = bool(getattr(self, "_recruiting", False))
            active = bool(getattr(self, "mafia_active", False))
            host = getattr(self, "_recruiter_host", None)
            me = getattr(self.engine, "name", None)
            if not recruiting and not active:
                self.mafia_host_mode = False
                if host == me:                 # 내가 방장이던 흔적만 지운다(남의 방장 정보는 그대로)
                    self._recruiter_host = None
                return
            last = getattr(self, "_mafia_last_host_ts", None)
            if (active and not self._mafia_is_host() and host and host != me and last
                    and time.time() - last > self._STALE_GAME_SECONDS
                    and self._mafia_peer_raw(host) is None):
                self.mafia_active = False
                self._recruiter_host = None
                self._my_mafia_role = None
                self._my_mafia_mates = None
                self.core.lobby_reset()
        except Exception as _swallow_e:
            applog.swallowed(_swallow_e)

    def _proto_authorized(self, t, ev, sender_name, peer=None):
        """[MAFIA1] 이벤트의 송신자 검증. 같은 LAN의 누구든 가짜 '게임 종료/역할 통보' 같은
        방장 전용 패킷이나 남의 이름으로 된 투표·밤 행동을 보낼 수 있던 문제를 막는다."""
        if t == "recruit_start":
            self._clear_stale_host_state()
        me = getattr(self.engine, "name", None)
        host = getattr(self, "_recruiter_host", None)
        am_host = bool(self._mafia_is_host() or (host and host == me))
        if t in self._HOST_ONLY_EVENTS:
            if am_host:
                return False            # 방장에게 방장 전용 이벤트가 올 이유가 없다
            if t == "recruit_start":
                # 모집을 여는 사람은 본인을 방장으로 알려야 하고, 진행 중인 판의 방장은 바꿀 수 없다
                claimed = ev.get("host")
                if not self._claim_matches(claimed, sender_name, peer):
                    return False                  # 본인을 방장으로 알리지 않은 패킷은 묶음도 건드리지 않는다
                active = getattr(self, "mafia_active", False)
                if not active:
                    self._reset_ident()           # 검증을 통과한 새 모집 — 이전 판의 묶음을 버린다
                if not self._sender_is(claimed, sender_name, peer):
                    return False
                if active and host and not self._sender_is(host, sender_name, peer):
                    return False
                return True
            return bool(host) and self._sender_is(host, sender_name, peer)
        field = self._PEER_CLAIM_FIELD.get(t)
        if field:
            if t == "mafia_say" and host and self._sender_is(host, sender_name, peer):
                return True             # 방장이 중계하는 AI 마피아 발언·목표 안내
            if t == "recruit_join" and self._ident().get(ev.get(field)) is None \
                    and self._mafia_name_ambiguous(ev.get(field)):
                # 같은 이름을 쓰는 상대가 둘 이상 접속 중이면 누가 진짜인지 알 수 없다 — 어느 한쪽에
                # 이름을 묶으면 사칭한 쪽이 직업 통보·비밀 대화를 대신 받게 되므로 참가를 받지 않는다.
                try:
                    applog.log("mafia_join_ambiguous", detail=f"name={ev.get(field)} from={sender_name}")
                    self.add_mafia_host_dm(
                        f"⚠ '{ev.get(field)}' 이름을 쓰는 접속자가 둘 이상이라 참가를 받지 않았습니다. "
                        "표시 이름을 서로 다르게 바꾼 뒤 다시 신청하세요.")
                except Exception as _swallow_e:
                    applog.swallowed(_swallow_e)
                return False
            if t in self._TOKEN_EVENTS and self._mafia_is_host():
                want = getattr(self, "_mafia_tokens", {}).get(ev.get(field))
                if want:
                    import hmac
                    got = ev.get("tok")
                    if not isinstance(got, str) or not hmac.compare_digest(got, want):
                        self._note_impersonation(ev.get(field), peer or ("?",))
                        return False
            return self._sender_is(ev.get(field), sender_name, peer)
        return True

    def _broadcast_vote_done(self, voter, target):
        """투표가 '접수됐다'만 알린다(대상은 싣지 않는다) — 화면뿐 아니라 패킷·로그로도 익명.
        진행 안내 문구("○○님 투표 접수 완료 · 진행률")는 호스트의 add_mafia_system이 이미 전원에게 sys로 방송하므로 여기서
        다시 보내지 않는다(v1.75가 같은 줄을 한 번 더 보내 원격 화면에 두 번 뜨던 것을 v1.82에서 제거)."""
        self._mafia_broadcast("vote", voter=voter, abstain=(target is None))

    def _broadcast_defense_progress(self, voter):
        """(호환용 빈 함수) 찬반 표 접수 안내도 add_mafia_system이 이미 방송한다."""
        return

    def _mafia_sys_except(self, text, exclude=None):
        """호스트 전용 — 시스템 안내(sys)를 모든 원격 참가자에게 보낸다. exclude(보통 방금 표를 낸 사람)는
        자기 화면에 이미 안내가 떠 있으므로 뺀다."""
        if not self._mafia_is_host():
            return
        from mafia_net import encode
        pkt = encode("sys", text=text)
        eng = getattr(self, "engine", None)
        if not pkt or eng is None:
            return
        skip = None
        if exclude:
            try:
                skip = self._mafia_peer_of(exclude)
            except Exception:
                skip = None
        with eng.plock:
            peers = list(eng.peers.keys())
        for ip_port in peers:
            if skip is not None and tuple(ip_port) == tuple(skip):
                continue
            try:
                eng.send_message(ip_port[0], ip_port[1], pkt)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)

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
        # v1.87 — 이 클라이언트 쪽 상태(표시 안내·변론 잠금 해제)를 지우는 것은 진짜 방장의
        # 이벤트로 검증된 뒤에만 한다. 예전에는 송신자 검증(_proto_authorized) 전에 지웠기
        # 때문에, LAN에 떠 있는 아무 기기(옛 버전 인스턴스 등)가 같은 t 값을 보내는 것만으로도
        # 실제 최후 변론 도중 발언 잠금이 풀리고 75초 안전 타이머가 취소될 수 있었다.
        if t in ("defense_start", "verdict", "night", "day", "end", "tally") and not self._mafia_is_host():
            self._vote_window = False
        if t in ("verdict", "night", "day", "end") and not self._mafia_is_host():
            self._clear_client_defense()
        if t != "recruit_start" and t in self._HOST_ONLY_EVENTS:
            self._mafia_last_host_ts = time.time()      # 방장이 살아 있다는 표시
        # 참가 신청하지 않은 사람에게는 게임 진행 화면(시작·밤 연출·투표 팝업)을 띄우지 않는다.
        if not self._mafia_is_host():
            if t == "start":
                me_n = getattr(self.engine, "name", None)
                names = [(e.get("name") if isinstance(e, dict) else e) for e in (ev.get("players") or [])]
                self._in_game = (not names) or (me_n in names)     # 구버전(명단 없음)은 참가로 간주
                if not self._in_game:
                    self._recruiting = False
                    for w in ("mafia_join_btn", "mafia_cancel_recruit_btn"):
                        if hasattr(self, w):
                            getattr(self, w).pack_forget()
                    return True
            elif t == "recruit_start":
                self._in_game = True
            elif (t in ("night", "day", "death", "tally", "vote", "vote_open", "revote_open",
                        "defense_vote_open", "defense_start", "verdict", "end",
                        # 사회자·AI·참가자의 게임 중 대화도 참가하지 않은 사람에게는 보이면 안 된다
                        "hsay", "asay", "sys", "user_say", "ghost_say", "mafia_say", "lobby_chat")
                  and not getattr(self, "_in_game", True)):
                return True
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
                self._ghost_relay_humans(spk, ev.get("text", ""))     # 다른 사람 사망자에게(호스트가 죽었으면 호스트 화면에도)
                self._ghost_ai_reply(ev.get("text", ""), speaker=spk)
        elif t == "ghost_say":
            # 호스트의 사망 AI가 보낸 유령방 답장(개인 전송) — 내 유령방이 열려 있을 때만 표시.
            # v1.88 검토 — mafia_say처럼 수신측에서도 '내가 지금 사망 상태인지' 확인하는 걸
            # 시도했으나, 클라이언트의 로컬 core.players[me]["alive"]는 호스트가 보낸 death
            # 브로드캐스트를 받아야 갱신되는 값이라 실제로는 정상적인 지연이 있다(방금 죽어서
            # 유령방으로 안내된 순간에도 아직 True로 남아 있을 수 있음) — 그 상태에서 정당한
            # ghost_say까지 걸러지는 게 더 나쁜 회귀라 되돌렸다(test_multiplayer_sync로 확인).
            # 대신 주소 오배달 자체는 송신측 _mafia_peer_of의 ident 우선 바인딩으로 막는다.
            if ev.get("text") and isinstance(ev.get("text"), str):
                self._append_ghost(f"👻 {ev.get('name') or '?'}: {ev.get('text')}", ai=True)   # 창이 닫혀 있어도 누적 기록
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
            if not isinstance(say_text, str):
                return True
            _info = (self.core.players.get(name) or {}) if getattr(self, "core", None) else {}
            if self.mafia_active and (not _info or _info.get("is_ai") or not _info.get("alive", True)):
                return True        # 참가자가 아니거나 이미 사망한 사람의 발언은 버린다(AI 기억 오염 방지)
            if say_text:
                self.add_mafia_bubble(say_text, name)
                if self._mafia_is_host() and getattr(self, "ai", None):
                    self.ai.observe_all(name, say_text)
                    self._ai_hear_human(name, say_text)   # 원격 참가자의 발언에도 AI가 반응한다
        elif t == "sys":
            _txt = ev.get("text", "")
            if isinstance(_txt, str) and _txt.startswith("✅ 방장이 내"):
                self._clear_host_ack(_txt)
            self.add_mafia_system(_txt)
        elif t == "hdm":
            # 개인 쪽지 — target==내 이름일 때만 표시
            me = getattr(self.engine, "name", None)
            if ev.get("target") == me:
                # v1.88 — text가 문자열이 아니면(다른 버전 호스트·손상된 패킷) 아래
                # "f\"'{r_kr}'\" in msg_txt"에서 TypeError가 나서, 이 사람만 역할 통보를
                # 통째로 못 받고 그 판 내내 직업 없는 시민처럼 게임하게 된다.
                msg_txt = ev.get("text")
                msg_txt = msg_txt if isinstance(msg_txt, str) else ""
                self.add_mafia_host_dm(msg_txt)
                if ev.get("tok") and not self._mafia_is_host():
                    self._my_tok = ev.get("tok")
                if not self._mafia_is_host():
                    self._close_night_panel_on_ack(msg_txt)
                    if isinstance(msg_txt, str) and msg_txt.startswith("⚠"):
                        self._night_panel_warn(msg_txt)      # 원격 참가자도 거절 사유를 팝업 안에서 본다
                    elif isinstance(msg_txt, str) and msg_txt.startswith("💉") and getattr(self, "_pending_heal", None):
                        self._confirmed_heal = self._pending_heal      # 호스트가 치료를 접수했다 — 밤이 끝나면 last_protect가 된다
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
            self._reset_ghost_state()
            self._pending_heal = None
            self._confirmed_heal = None
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
                # v1.88 — recruit_start/recruit_update는 v1.74에서 _name_list로 이미 방어했는데
                # 이 "start" 이벤트는 빠져 있었다. players가 리스트가 아니면(손상된 패킷·다른
                # 버전 호스트) 아래 "for entry in roster"가 TypeError를 내서 self._in_game이
                # 대입되기도 전에 멈추고, 이 사람은 게임 화면 자체를 못 본다.
                roster = ev.get("players")
                roster = roster if isinstance(roster, (list, tuple)) else []
                with self.core.lock:
                    if self.core.phase == Phase.LOBBY:
                        for entry in roster:
                            if isinstance(entry, dict):
                                nm, is_ai = entry.get("name"), bool(entry.get("is_ai"))
                            else:
                                nm, is_ai = entry, False   # 구버전 호환(문자열 명단)
                            if isinstance(nm, str) and nm and nm not in self.core.players:
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
            # 원격 의사: 호스트만 갖던 "어젯밤 치료 대상"을 내 화면에도 기록해 다음 밤 팝업에서 미리 비활성화한다
            self.core.last_protect = getattr(self, "_confirmed_heal", None)
            self._confirmed_heal = None
            self._pending_heal = None
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
            self._defense_in_progress = True
            self._start_defense_visuals(ev.get("name", "피고인"))
            self._arm_client_defense_guard()
        elif t == "verdict":
            # v1.61 — 처형 확정이면 원격 core에서도 사망 처리 + 본인이면 유령방
            vname, vrole = ev.get("name"), ev.get("role")
            # v1.88 — vname이 문자열이 아니면(손상된 패킷·다른 버전) "in self.core.players"가
            # 딕셔너리 키 비교라 해시 불가능한 값(list/dict 등)에서 TypeError를 낸다.
            if ev.get("result") == "executed" and isinstance(vname, str) and vname in self.core.players:
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
            host = ev.get("host")
            host = host if isinstance(host, str) and host else "방장"
            self._recruiting = True
            self._recruiter_host = host
            self._recruited_humans = self._name_list(ev.get("players"))
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
                if pname and pname not in self._recruited_humans \
                        and len(self._recruited_humans) >= MAX_PLAYERS - 1:
                    # 사람은 최대 MAX_PLAYERS-1명(AI 최소 1명 자리). 예전엔 상한이 없어 10명이 모인 뒤에야
                    # 시작이 거절됐고, 그때 모집 상태가 이미 망가져 전원이 다시 신청해야 했다.
                    self.add_mafia_host_dm(
                        f"⚠ '{pname}' 님의 참가 신청을 받지 못했습니다 — 사람은 최대 {MAX_PLAYERS - 1}명까지입니다.")
                    self._mafia_send_private(pname, "hdm", target=pname,
                                             text=f"⚠ 모집 인원이 가득 찼습니다(사람 최대 {MAX_PLAYERS - 1}명).")
                    self._mafia_broadcast("recruit_update", host=me, players=self._recruited_humans)  # 신청자 화면을 되돌린다
                elif pname and pname not in self._recruited_humans:
                    self._recruited_humans.append(pname)
                    self.add_mafia_system(f"🙋 '{pname}' 님이 참가 신청했습니다! (현재 {len(self._recruited_humans)}명)")
                    self.mafia_start_btn.config(text=f"🎮 게임 시작 (인간 {len(self._recruited_humans)}명)")
                    self._mafia_broadcast("recruit_update", host=me, players=self._recruited_humans)
                if pname and pname in self._recruited_humans:
                    # 모집 알림(recruit_start)을 놓친 신청자도 방장·명단·버튼 상태를 알 수 있게 개인 쪽지로 다시 보낸다
                    self._mafia_send_private(pname, "recruit_start", host=me, players=self._recruited_humans)
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
            self._recruited_humans = self._name_list(ev.get("players"))
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
            if hasattr(self, "mafia_cancel_recruit_btn"):
                self.mafia_cancel_recruit_btn.pack_forget()
            if hasattr(self, "mafia_start_btn"):
                self.mafia_start_btn.pack(side="right", padx=(10, 6), pady=8)
                self.mafia_start_btn.config(text="📢 참가자 모집", bg="#b91c1c", activebackground="#7f1d1d", state="normal")
            self._mafia_pack_lobby_buttons()
            self.mafia_phase_lbl.config(text="")
            self.add_mafia_system("📢 방장이 참가자 모집을 취소했습니다.")
        elif t == "lobby_chat":
            sender = ev.get("sender", "알 수 없음")
            msg_text = ev.get("text", "")
            me = getattr(self.engine, "name", None)
            if getattr(self, "mafia_active", False):
                return True        # 게임 중 로비 채팅은 참가하지 않은 사람의 발언 — 게임방에 넣지 않는다
            if sender != me and msg_text:
                self.add_mafia_bubble(msg_text, sender)
        return True

    def _mafia_stop_disconnect_watch(self):
        """판이 끝나면 접속 감시 예약을 취소한다(단계마다 부르는 _cancel_mafia_timer에는 넣지 않는다 —
        넣으면 낮/밤이 바뀔 때마다 감시가 멈춘다)."""
        t = getattr(self, "_disconnect_watch_timer", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
        self._disconnect_watch_timer = None

    def _mafia_start_disconnect_watch(self):
        """게임 시작 시 1회 호출 — 이후 mafia_active인 동안 스스로 재예약되며 계속 돈다.
        이전 판의 예약이 남아 있으면 먼저 취소해 감시 루프가 둘이 되지 않게 한다."""
        t = getattr(self, "_disconnect_watch_timer", None)
        if t:
            try:
                self.root.after_cancel(t)
            except Exception as _swallow_e:
                applog.swallowed(_swallow_e)
            self._disconnect_watch_timer = None
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
            strikes = getattr(self, "_mafia_disconnect_strikes", None)
            if strikes is None:
                strikes = self._mafia_disconnect_strikes = {}
            # v1.88 — 예전에는 사람 참가자마다 _mafia_peer_of(이름 안 묶였으면 eng.plock을
            # 잡고 피어 전체를 훑음) → get_peer(다시 eng.plock)를 따로 불러, 참가자 N명이면
            # 4초마다 최대 2N번 락을 잡고 피어 목록을 매번 훑었다(호스트 메인 스레드가
            # _send_reliable 워커·프레즌스 루프와 경합). eng.peers를 이 폴링 한 번에 딱
            # 한 번만 복사해 두고, 그 사본으로 모든 참가자의 이름을 맞춰본다.
            eng = self.engine
            with eng.plock:
                peer_snapshot = [((ip, port), dict(p)) for (ip, port), p in eng.peers.items()]
            ident = self._ident()
            now = time.time()
            for name, p in list(self.core.players.items()):
                if p.get("is_ai") or name == me:
                    continue
                bound = ident.get(name)
                online = False
                if bound is not None:
                    for (ip, port), info in peer_snapshot:
                        if (ip, port) == bound:
                            if now - info.get("last", 0) < PEER_TIMEOUT:
                                online = True
                            break
                else:
                    for (ip, port), info in peer_snapshot:
                        alias = eng.get_alias((ip, port)) or ""
                        if (alias == name or info.get("name", "") == name) and now - info.get("last", 0) < PEER_TIMEOUT:
                            online = True
                            break
                was_disconnected = name in known
                if not online and not was_disconnected:
                    # v1.87 — 와이파이에서는 프레즌스(UDP 브로드캐스트)가 몇 번 연속으로
                    # 누락되는 일이 흔하다. 첫 번째 누락에 바로 사망 처리하면 순간적인
                    # 전파 문제로 살아 있는 사람을 게임에서 제외하게 되므로, 연속으로
                    # 몇 번(약 12초) 더 확인한 뒤에만 접속 끊김으로 확정한다.
                    n = strikes.get(name, 0) + 1
                    strikes[name] = n
                    if n < 3:
                        continue
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
                    strikes.pop(name, None)
                elif online:
                    strikes.pop(name, None)
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
                     "_tick_vote", "_defense_vote_tick", "_night_pick_tick",
                     "_defense_client_guard", "_defense_ticker"):
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
            # 개표가 이미 시작됐거나 대상이 사망한 경우 등 — 조용히 버리면 투표자는 "확인이 없다"는 엉뚱한 경고(버전 불일치)를 본다
            if voter != getattr(self.engine, "name", None):
                self._mafia_send_private(voter, "sys",
                                         text="✅ 방장이 내 투표를 받았지만 반영하지 못했습니다 (이미 개표가 시작되었거나 대상이 사망)")
            return
        _pd, _pt = self._vote_progress_counts()
        if target:
            self.add_mafia_system(f"🗳 {voter}님 투표 접수 완료 (익명 개표) · 진행률 {_pd}/{_pt}")
        else:
            self.add_mafia_system(f"🗳 {voter} 기권 접수 · 진행률 {_pd}/{_pt}")
        self._refresh_vote_progress_label()
        if voter != getattr(self.engine, "name", None):
            self._mafia_send_private(voter, "sys", text="✅ 방장이 내 " + ("투표를 접수했습니다 (익명)" if target else "기권을 접수했습니다"))
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
        _late = "✅ 방장이 내 찬반 표를 받았지만 반영하지 못했습니다 (이미 끝난 재판이거나 투표 자격 없음)"
        if getattr(self.core, "defendant", None) != name:
            if voter != getattr(self.engine, "name", None):
                self._mafia_send_private(voter, "sys", text=_late)
            return   # 이미 끝난 재판에 늦게 도착한 표
        if not (self.core.players.get(voter) or {}).get("alive", True):
            if voter != getattr(self.engine, "name", None):
                self._mafia_send_private(voter, "sys", text=_late)
            return
        self.core.cast_defense_vote(voter, bool(yes))
        self.add_mafia_system(f"⚖ {voter}님 찬반 표 접수 (익명) · {self._defense_progress_text()}")
        self._broadcast_defense_progress(voter)
        if voter != getattr(self.engine, "name", None):
            self._mafia_send_private(voter, "sys", text="✅ 방장이 내 찬반 표를 접수했습니다 (익명)")
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
