# -*- coding: utf-8 -*-
"""v1.116 — 게임 종료 때 참가자(클라이언트) 화면에도 사회자의 승리 멘트가 뜨고, 방장은 그 멘트를 방송하지 않는다."""
import argparse, importlib.util, os, shutil, socket, sys
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
if __name__ != "__main__":
    sys.exit(0)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
from mafia_net import encode
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_winann"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="클라", port=60161, datadir=tmp), [])
app.mafia_active = True; app._recruiter_host = "방장"; app._in_game = True
app._client_game_end("mafia", {"방장": "mafia", "클라": "citizen"})
texts = [(r.get("label"), r.get("text", "")) for r in app.mafia_history]
check("참가자 화면에 사회자의 '마피아 팀이 승리' 멘트가 뜸", any(l == "🖥 사회자" and "마피아 팀이 승리했습니다" in t for l, t in texts))
n = sum(1 for l, t in texts if l == "🖥 사회자" and "승리했습니다" in t)
check("한 번만 뜸", n == 1)
# 방장 쪽: 승리 멘트를 hsay로 방송하지 않는다
app2_sent = []
app.mafia_active = True; app.mafia_host_mode = True
app._mafia_broadcast = lambda t, **kw: app2_sent.append(t)
app.add_mafia_host("🩸 테스트", local=True)
check("local=True 사회자 멘트는 방송하지 않음", "hsay" not in app2_sent)
app.add_mafia_host("일반 사회자 멘트")
check("일반 사회자 멘트는 그대로 방송함", "hsay" in app2_sent)
root.destroy()
print("WIN ANNOUNCE PASSED" if ALL else "WIN ANNOUNCE FAILED"); sys.exit(0 if ALL else 1)
