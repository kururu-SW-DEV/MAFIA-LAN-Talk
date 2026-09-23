# -*- coding: utf-8 -*-
"""test_leave_button.py — 클라이언트의 [🚪 방 나가기] 버튼과 방장 쪽 leave_game 처리."""
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


tmp = os.path.join(BASE, "tmp_leave"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60081, 60082

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
try:
    app._select_mafia_room(("mgame",)); root.update()
    # ---- 클라이언트로서 ----
    app.mafia_host_mode = False; app._recruiter_host = "다른방장"; app._in_game = True; app.mafia_active = True
    app._select_mafia_room(("mgame",)); root.update()
    check("참가 중인 클라이언트에게 [방 나가기]가 보임", app.mafia_leave_btn.winfo_ismapped())
    check("강제 종료 버튼은 안 보임", not app.mafia_force_quit_btn.winfo_ismapped())
    app._embed_confirm = lambda *a, **k: False
    app.mafia_leave_clicked()
    check("취소하면 그대로", app.mafia_active is True)
    app._embed_confirm = lambda *a, **k: True
    app.mafia_leave_clicked(); root.update()
    check("나가면 로비 상태", app.mafia_active is False and app._in_game is False)
    check("버튼이 숨음", not app.mafia_leave_btn.winfo_ismapped())
    # ---- 방장으로서 leave_game 수신 ----
    app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
        app.core.join(n, is_ai=a)
    for _n, _r in (("방장","citizen"),("이팀장B","citizen"),("철수","mafia"),("영희","citizen"),("미나","citizen")):
        app.core.players[_n]["role"] = _r
    app.core.phase = Phase.DAY; app.core.day_no = 1
    app.mafia_active = True; app.mafia_host_mode = True; app._recruiter_host = "방장"
    app._reset_ident(); app._ident()["이팀장B"] = ("127.0.0.1", B_PORT)
    app._mafia_tokens = {"이팀장B": "TOK"}
    ok = app._on_mafia_proto_msg(encode("leave_game", name="이팀장B", tok="BAD"), "이팀장B", ("127.0.0.1", B_PORT))
    check("토큰이 틀리면 무시", app.core.players["이팀장B"]["alive"] is True)
    app._on_mafia_proto_msg(encode("leave_game", name="이팀장B", tok="TOK"), "이팀장B", ("127.0.0.1", B_PORT))
    check("방장이 사망 처리", app.core.players["이팀장B"]["alive"] is False)
    check("끊김 목록에 등록", "이팀장B" in app._mafia_disconnected)
    check("안내 메시지", any("나갔습니다" in r.get("text", "") for r in app.mafia_history))
finally:
    try: app._cancel_mafia_timer()
    except Exception: pass
    try: root.destroy()
    except Exception: pass

print("LEAVE BUTTON", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
