# -*- coding: utf-8 -*-
"""GUI 조립 스모크 (v3 텔레그램풍) — 창 숨김 모드, 조건 폴링"""
import argparse
import importlib.util
import socket
import sys
import time
import os

BASE = os.path.dirname(os.path.abspath(__file__))      # tests/ 자기 폴더 (임시 테스트 데이터용)
APP_ROOT = os.path.dirname(BASE)                        # 프로젝트 루트 (lan_messenger.py 등 실제 코드)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)                        # lan_messenger.py가 import하는 app/engine 등을 찾게 함
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP_ROOT, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)

_real_bind = socket.socket.bind


def _spy(self, addr):
    if isinstance(addr, tuple) and len(addr) == 2 and addr[0] in ("", "0.0.0.0"):
        _real_bind(self, ("127.0.0.1", addr[1]))
    else:
        _real_bind(self, addr)


socket.socket.bind = _spy
spec.loader.exec_module(lm)

datadir = os.path.join(BASE, "smoke")
os.makedirs(datadir, exist_ok=True)
open(os.path.join(datadir, "firewall_notice_done"), "w", encoding="utf-8").close()

args = argparse.Namespace(name="스모크", port=50707, datadir=datadir, peers="")

import tkinter as tk

root = tk.Tk()
root.withdraw()
app = lm.App(root, args, ["127.0.0.1:50708"])

def wait_gui(cond, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


wait_gui(lambda: len(app.pinner.winfo_children()) >= 1)
res = dict(
    me=app.me_lbl.cget("text"),
    title=root.title(),
    cnt=app.cnt_lbl.cget("text"),
    chat_state=str(app.entry.cget("state")),
    attach_state=str(app.attach_btn.cget("state")),
    chat_head=app.ch_title.cget("text"),
    rows=len(app.pinner.winfo_children()),
    tab=app._tab,
    friend_tab_exists=hasattr(app, "tab_friend_btn"),
)
app._set_tab("friend")
res["friend_rows_after_switch"] = len(app.pinner.winfo_children())
app._set_tab("chat")
try:
    if getattr(app, "_notifier", None) is not None:
        app._notifier.close()      # 트레이 아이콘 제거 — 안 하면 종료 후에도 죽은 아이콘이 남는다
except Exception:
    pass
root.destroy()

print("engine =", app.engine is not None, "| port =", app.engine.port if app.engine else None)
print("me =", res.get("me"), "| title =", res.get("title"))
print("cnt_lbl =", res.get("cnt"), "| rows =", res.get("rows"))
print("chat_head =", res.get("chat_head"), "| entry_state =", res.get("chat_state"),
      "| attach_state =", res.get("attach_state"))
print("tab =", res.get("tab"), "| friend_tab_exists =", res.get("friend_tab_exists"),
      "| friend_rows_after_switch =", res.get("friend_rows_after_switch"))
ok = (app.engine is not None and res.get("rows", 0) >= 1 and res.get("chat_state") == "disabled"
     and res.get("attach_state") == "disabled" and res.get("friend_tab_exists")
     and res.get("tab") == "chat")
print("SMOKE", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
