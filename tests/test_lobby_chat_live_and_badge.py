# -*- coding: utf-8 -*-
"""test_lobby_chat_live_and_badge.py — 게임 시작 전(로비) 채팅이 탭을 보고 있는 동안
실시간으로 그려지는지, 다른 탭을 보는 동안에는 사이드바에 안 읽음 배지가 뜨는지."""
import argparse, importlib.util, os, shutil, socket, sys, time
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

ALL = True
def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)

tmp = os.path.join(BASE, "tmp_lobby_live"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.deiconify(); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60092, datadir=tmp), [])

def pump(sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)

# ---- 1) 게임 시작 전, 마피아 탭을 보고 있는 동안 로비 채팅이 실시간으로 그려짐 ----
app._select(("mgame",)); root.update()
check("게임 전이라 mafia_active는 아직 False", app.mafia_active is False)
before = len(app.chat.find_all())
app._on_mafia_proto_msg('[MAFIA1]{"t": "lobby_chat", "sender": "친구", "text": "안녕 로비"}', "친구", None)
root.update(); pump(0.2)
check("로비 채팅이 mafia_history에 쌓임",
      any("안녕 로비" in r.get("text", "") for r in app.mafia_history))
check("탭을 보고 있는 동안 캔버스에 즉시 그려짐(전체 다시 그리기 없이)",
      len(app.chat.find_all()) > before)

# ---- 2) 다른 탭을 보는 동안 로비 채팅이 오면 사이드바에 안 읽음 배지가 뜸 ----
app._select_mafia_room  # (no-op just to keep reference)
app.current = None      # 마피아 탭이 아닌 상태를 흉내
app.unread[("mgame",)] = 0
app._on_mafia_proto_msg('[MAFIA1]{"t": "lobby_chat", "sender": "친구2", "text": "다른 탭 볼 때 온 메시지"}', "친구2", None)
root.update(); pump(0.2)
check("다른 탭을 보는 동안 온 로비 채팅은 안 읽음 카운트가 올라감",
      app.unread.get(("mgame",), 0) >= 1)

try:
    app._cancel_mafia_timer()
except Exception:
    pass
root.destroy()
print("LOBBY CHAT LIVE+BADGE", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
