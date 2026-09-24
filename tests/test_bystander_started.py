# -*- coding: utf-8 -*-
"""test_bystander_started.py — 참가 신청을 취소했거나 안 한 클라이언트가 게임 시작 때 모집 상태에서 풀리는지."""
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
from mafia_ui import MafiaUIMixin
from dialogs import DialogsMixin
from app import App as RealApp

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_bystander_started"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60085, 60086

root = tk.Tk(); root.geometry("1000x700"); root.withdraw()
rootB = tk.Toplevel(root); rootB.withdraw()

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


class ClientStub(DialogsMixin, MafiaUIMixin):
    def _on_msg(self, ev):
        return RealApp._on_msg(self, ev)


b_q = queue.SimpleQueue()
B_engine = lm.Engine("이팀장B", port=B_PORT, datadir=os.path.join(tmp, "B"), on_event=b_q.put, instance_id="B" * 10)
B_engine.set_static_targets({("127.0.0.1", A_PORT)})
stubB = ClientStub()
stubB.root = rootB; stubB.engine = B_engine; stubB.core = GameCore("mafia-room")
stubB.ai = mafia_ai.AIDirector(); stubB.mafia_active = False; stubB.mafia_host_mode = False
stubB.mafia_history = []; stubB.current = None; stubB._my_mafia_role = None
stubB._recruiting = True; stubB._recruited_humans = []; stubB._my_joined = False
stubB._mafia_overlay = None; stubB._in_game = True; stubB._recruiter_host = "방장"
stubB.mafia_phase_lbl = tk.Label(rootB)
for n, a in (("방장", False), ("이팀장B", False), ("철수", True)):
    stubB.core.join(n, is_ai=a)
stubB.core.phase = Phase.DAY


def _drain_b():
    while True:
        try:
            ev = b_q.get_nowait()
        except queue.Empty:
            break
        if ev.get("ev") == "msg":
            try:
                stubB._on_msg(ev)
            except Exception as e:
                print("stubB._on_msg 예외:", repr(e))


def pump(cond, timeout=10.0):
    state = {"start": time.time(), "done": False}
    def _poll():
        try: rootB.update()
        except tk.TclError: pass
        _drain_b()
        try:
            if cond():
                state["done"] = True; root.quit(); return
        except tk.TclError:
            root.quit(); return
        if time.time() - state["start"] > timeout:
            root.quit(); return
        root.after(15, _poll)
    root.after(0, _poll)
    root.mainloop()
    return state["done"]


try:
    check("A가 B를 발견", pump(lambda: len(app.engine.peers) >= 1, timeout=10))
    check("A가 B의 실제 이름을 앎", pump(lambda: app._mafia_peer_of("이팀장B") is not None, timeout=10))
    core.lobby_reset(); core.players.clear()
    for n, a in (("방장", False), ("철수", True), ("영희", True), ("미나", True)):
        core.join(n, is_ai=a)          # B는 참가 신청을 취소해서 명단에 없다
    check("사전 조건: B는 모집 상태(클라이언트 모드)", stubB._recruiting is True and stubB._recruiter_host == "방장")
    app._mafia_notify_bystanders_started()
    check("B의 모집 상태가 풀림", pump(lambda: stubB._recruiting is False and stubB._recruiter_host is None, timeout=10))
    check("B는 구경 상태(_in_game False)", stubB._in_game is False)
    n = len(stubB.mafia_history)
    app.mafia_active = True; app.mafia_host_mode = True
    app._mafia_notify_bystanders_end("force")
    check("강제 종료 안내가 구경 상태의 B에게도 도착",
          pump(lambda: any("강제로 종료" in r.get("text", "") for r in stubB.mafia_history[n:]), timeout=10))
    n = len(stubB.mafia_history)
    app._mafia_notify_bystanders_end("force")
    pump(lambda: False, timeout=1.0)
    check("한 번만 보냄(중복 없음)", len(stubB.mafia_history) == n)
finally:
    try: app._cancel_mafia_timer()
    except Exception: pass
    try: root.destroy()
    except Exception: pass
print("BYSTANDER STARTED", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
