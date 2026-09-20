# -*- coding: utf-8 -*-
"""test_roster_bar.py — 마피아방 생존/사망 현황 줄 테스트(실제 App + Tk)."""
import argparse
import importlib.util
import os
import shutil
import socket
import sys

if __name__ != "__main__":
    sys.exit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(BASE)
sys.path.insert(0, APP_ROOT)

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

import tkinter as tk
from mafia_core import Phase

tmp = os.path.join(BASE, "tmp_roster")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
open(os.path.join(tmp, "firewall_notice_done"), "w", encoding="utf-8").close()

root = tk.Tk()
root.withdraw()
app = lm.App(root, argparse.Namespace(name="테스터", port=60013, datadir=tmp), [])
ok_all = True


def check(label, cond):
    global ok_all
    print(("OK  " if cond else "FAIL"), label)
    if not cond:
        ok_all = False


try:
    app._select(("mgame",))
    root.update()
    lbl = getattr(app, "mafia_roster_lbl", None)
    check("게임 전(로비)에는 현황 줄이 보이지 않음", lbl is None or not lbl.winfo_ismapped())

    core = app.core
    for n, ai in (("테스터", False), ("철수", True), ("영희", True), ("민수", True)):
        core.join(n, ai)
    core.phase = Phase.DAY
    core.day_no = 1
    app.mafia_active = True
    app.refresh_mafia_phase_label()
    root.update()
    lbl = app.mafia_roster_lbl
    check("게임 중에는 현황 줄이 표시됨", lbl.winfo_ismapped())
    t = lbl.cget("text")
    check("생존 4명과 이름이 모두 표시됨", "생존 4명" in t and all(n in t for n in ("철수", "영희", "민수")))
    check("내 이름에 (나), AI에 🤖 표시", "테스터 (나)" in t and "🤖" in t)
    check("사망자가 없으면 사망 항목이 없음", "사망" not in t)

    core.players["영희"]["alive"] = False
    for _ in range(100):           # 2초 주기 갱신을 기다린다
        root.update()
        root.after(30)
        if "사망 1명" in lbl.cget("text"):
            break
        import time
        time.sleep(0.03)
    t = lbl.cget("text")
    check("사망 처리하면 자동으로 사망 명단에 반영됨", "생존 3명" in t and "사망 1명: 영희" in t)
    check("직업(역할)은 표시하지 않음", not any(w in t for w in ("마피아 ", "의사", "경찰", "시민")))

    # 마피아 밀담 다시 열기 버튼은 알약 버튼(이미지로 그린 둥근 버튼)이어야 한다
    core.players["테스터"]["role"] = "mafia"
    app._mafia_room_open()
    root.update()
    app._mafia_room_close(keep_reopen=True)
    root.update()
    mini = app._mafia_room_mini
    check("밀담 다시 열기 버튼이 화면에 표시됨", mini.winfo_ismapped())
    check("밀담 버튼이 알약(이미지) 버튼임", bool(mini.cget("image")))
    app._mafia_room_close()

    app.mafia_active = False
    app._refresh_mafia_roster()
    root.update()
    check("게임이 끝나면 현황 줄이 사라짐", not lbl.winfo_ismapped())
finally:
    try:
        app._notifier.close()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print("ROSTER BAR PASSED" if ok_all else "ROSTER BAR FAILED")
sys.exit(0 if ok_all else 1)
