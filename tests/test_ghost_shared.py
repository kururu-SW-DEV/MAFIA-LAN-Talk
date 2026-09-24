# -*- coding: utf-8 -*-
"""test_ghost_shared.py — 유령방: AI의 답이 말한 사람뿐 아니라 방의 모든 사람 사망자에게 전달되는지."""
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


tmp = os.path.join(BASE, "tmp_ghost_shared"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60089, 60090

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



from types import SimpleNamespace
import time
sent = []
try:
    app._select_mafia_room(("mgame",)); root.update()
    app.core.lobby_reset(); app.core.players.clear()
    for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True)):
        app.core.join(n, is_ai=a)
    app.core.phase = Phase.NIGHT
    app.core.players["방장"]["alive"] = False       # 호스트도 사망
    app.core.players["이팀장B"]["alive"] = False    # 원격 참가자도 사망
    app.core.players["철수"]["alive"] = False       # 사망 AI(유령방 대화 상대)
    app.mafia_active = True; app.mafia_host_mode = True; app.engine.name = "방장"
    fake = SimpleNamespace(name="철수", alive=False, booted=True, say=lambda prompt, secret=False: "반가워요 유령님들")
    app.ai.players = [fake]
    app._mafia_send_private = lambda who, t, **kw: sent.append((who, t, kw))
    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            root.update(); time.sleep(0.02)
    # 1) 원격 사망자가 말함 → AI 답이 원격 본인 + 호스트(사망) 화면에
    app._ghost_ai_reply("안녕 AI", speaker="이팀장B"); pump(2.0)
    check("원격 사망자가 말하면 그 사람에게 AI 답이 감", any(w == "이팀장B" and t == "ghost_say" and "반가워요" in kw.get("text", "") for w, t, kw in sent))
    check("호스트(사망) 유령방에도 그 답이 보임", any("반가워요" in x for x in (app._ghost_log or [])))
    app._open_ghost_chat(); root.update()      # 호스트 유령방을 실제로 연다
    # 2) 호스트가 말함 → AI 답이 호스트 + 원격 사망자에게도
    sent.clear(); app._ghost_log = []
    app._ghost_ai_reply("나도 안녕"); pump(2.5)
    check("호스트가 말해도 AI 답이 원격 사망자에게 전달", any(w == "이팀장B" and t == "ghost_say" and "반가워요" in kw.get("text", "") for w, t, kw in sent))
    check("호스트 본인에게도 AI 답이 옴", any("반가워요" in x for x in (app._ghost_log or [])))
finally:
    try: app._cancel_mafia_timer()
    except Exception: pass
    try: root.destroy()
    except Exception: pass
print("GHOST SHARED", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
