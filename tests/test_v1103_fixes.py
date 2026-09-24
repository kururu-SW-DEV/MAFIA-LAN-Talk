# -*- coding: utf-8 -*-
"""test_v1103_fixes.py — Opus 7차 리뷰: mafia_say 사칭 우회, 이름 선점, 밤 도중 개표, 구경꾼 오분류 등."""
import argparse, importlib.util, os, queue, shutil, socket, sys, time
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
import mafia_ai
from mafia_core import GameCore, Phase

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_v1103"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60087, 60088

root = tk.Tk(); root.geometry("1000x700"); root.withdraw()

app = lm.App(root, argparse.Namespace(name="방장", port=A_PORT, datadir=os.path.join(tmp, "A")),
             [f"127.0.0.1:{B_PORT}"])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
    core.join(n, is_ai=a)
core.phase = Phase.DAY; core.day_no = 1
app.mafia_active = True
app.mafia_host_mode = True
app._recruiting = False



from mafia_net import encode
B=("127.0.0.1", B_PORT); X=("127.0.0.1", 61111); Y=("127.0.0.1", 61112)
try:
    app._select_mafia_room(("mgame",)); root.update()
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("이팀장B","mafia"),("철수","mafia"),("영희","citizen"),("미나","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.phase = Phase.DAY; app.core.day_no = 1
    app.mafia_active = True; app.mafia_host_mode = True; app._recruiter_host = "방장"
    app.engine.name = "방장"
    app._reset_ident(); app._ident()["이팀장B"] = B
    # 3) 방장 쪽 mafia_say 사칭 우회 차단
    check("제3자가 방장 이름으로 보낸 mafia_say(본문은 마피아 이름) 거부",
          app._proto_authorized("mafia_say", {"t":"mafia_say","name":"이팀장B","text":"x"}, "방장", X) is False)
    # 4) 로비 채팅으로 이름을 먼저 묶어도 진짜 참가 신청이 살아 있다
    app.mafia_active = False; app._recruiting = True; app._reset_ident()
    app._proto_authorized("lobby_chat", {"t":"lobby_chat","sender":"영수","text":"hi"}, "영수", X)
    check("로비 채팅은 이름을 묶지 않음", app._ident().get("영수") is None)
    check("진짜 참가 신청은 통과", app._proto_authorized("recruit_join", {"t":"recruit_join","name":"영수"}, "영수", Y) is True)
    check("공격자의 이후 요청은 거부", app._proto_authorized("recruit_leave", {"t":"recruit_leave","name":"영수"}, "영수", X) is False)
    # 1) 밤으로 들어가면 투표 창 표시가 꺼진다
    app.mafia_active = True; app._recruiting = False
    app._vote_window = True
    try: app._enter_night_sequence()
    except Exception as e: print("night seq exc", repr(e))
    check("밤 진입 시 _vote_window 해제", app._vote_window is False)
    # 9) 이미 끝난 재판에는 찬반 접수 안내를 방송하지 않음
    app.core.defendant = None
    n = len(app.mafia_history)
    app._cast_defense("영희", True); root.update()
    check("끝난 재판에 찬반 접수 안내 없음", not any("찬반 표 접수" in r.get("text","") for r in app.mafia_history[n:]))
    # 7, 8) recruit_cancel(started)
    app.mafia_host_mode = False; app._recruiter_host = "다른방장"; app.mafia_active = True; app._reset_ident()
    app._ident()["다른방장"] = B
    app._on_mafia_proto_msg(encode("recruit_cancel", host="다른방장", started=True, players=["방장", "x"]), "다른방장", B)
    check("명단에 내가 있으면 구경꾼 처리를 무시(게임 유지)", app.mafia_active is True and app._recruiter_host == "다른방장")
    app._on_mafia_proto_msg(encode("recruit_cancel", host="다른방장", started=True, players=["x"]), "다른방장", B)
    check("명단에 없고 옛 mafia_active가 남았으면 로비로 복귀", app.mafia_active is False)
    # v1.105) 게임 종료 시 유령 채팅방 정리 + 종료 뒤 늦은 유령방 호출은 조용히 무시
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("이팀장B","citizen"),("철수","mafia"),("영희","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.players["방장"]["alive"] = False
    app.core.phase = Phase.NIGHT; app.mafia_active = True; app.mafia_host_mode = True; app.engine.name = "방장"
    app._open_ghost_chat(); root.update()
    check("사전 조건: 유령 채팅방 오버레이가 열림", getattr(app, "_mafia_overlay", None) is not None)
    app._on_game_end("mafia"); root.update()
    check("게임 종료 시 유령 채팅방 오버레이가 닫힘", getattr(app, "_mafia_overlay", None) is None)
    n = len(app.mafia_history)
    app._open_ghost_chat(); root.update()
    check("종료 뒤 늦은 유령방 호출은 '사망자가 아직 없습니다'를 띄우지 않음",
          not any("사망자가 아직 없습니다" in r.get("text", "") for r in app.mafia_history[n:]))
    # v1.111) 피고인 안내 팝업은 개표가 끝나면 닫히고, 문구에 깨지던 '닫' 글자가 없다
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("이팀장B","mafia"),("철수","citizen"),("영희","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.phase = Phase.VOTE; app.mafia_active = True; app.mafia_host_mode = True; app.engine.name = "방장"
    app.core.set_defendant("방장")
    app._enter_night_sequence = lambda *a, **k: None
    app._show_defense_vote_popup("방장"); root.update()
    check("사전 조건: 피고인 안내 팝업이 뜸", getattr(app, "_mafia_overlay", None) is not None)
    def _txts(w, out=None):
        out = [] if out is None else out
        try:
            t = w.cget("text")
            if t: out.append(str(t))
        except Exception: pass
        for c in w.winfo_children(): _txts(c, out)
        return out
    check("팝업 문구에 깨져 보이던 '닫'이 없음", not any("닫" in t for t in _txts(app._mafia_overlay)))
    app._resolve_defense("방장"); root.update()
    check("개표가 끝나면 피고인 안내 팝업이 닫힘", getattr(app, "_mafia_overlay", None) is None)
    # v1.112) 재투표에도 화면에 보이는 카운트다운이 있고, 시간이 다 되면 기권 처리 후 팝업이 닫힘
    import mafia_ui_vote as _mv
    _mv.VOTE_WINDOW = 3
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("철수", True), ("영희", True), ("미나", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("철수","mafia"),("영희","citizen"),("미나","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.phase = Phase.VOTE; app.mafia_active = True; app.mafia_host_mode = True; app.engine.name = "방장"
    app.ai.players = []
    app._tally_full = lambda *a, **k: None
    app._open_revote_popup(["철수", "영희"]); root.update()
    _lbls = _txts(app._mafia_overlay) if getattr(app, "_mafia_overlay", None) is not None else []
    check("재투표 팝업에 남은 시간 표시가 있음", any("남은 시간" in t and "기권" in t for t in _lbls))
    _t0 = time.time()
    while time.time() - _t0 < 6 and getattr(app, "_mafia_overlay", None) is not None:
        root.update(); time.sleep(0.05)
    check("시간이 다 되면 팝업이 닫힘", getattr(app, "_mafia_overlay", None) is None)
    check("투표하지 않은 내가 기권 처리됨", "방장" in app.core.abstains)
    _mv.VOTE_WINDOW = 15
finally:
    try: app._cancel_mafia_timer()
    except Exception: pass
    try: root.destroy()
    except Exception: pass

print("V1103 FIXES", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
