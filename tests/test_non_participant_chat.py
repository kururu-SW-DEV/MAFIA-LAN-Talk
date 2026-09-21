# -*- coding: utf-8 -*-
"""게임에 참가하지 않은 사람에게 다른 사람들의 게임 중 채팅이 보이지 않는지(참가자에게는 보이는지)."""
import argparse, importlib.util, os, shutil, socket, sys
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
from mafia_net import encode

tmp = os.path.join(BASE, "tmp_nonpart"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.withdraw()
app = lm.App(root, argparse.Namespace(name="구경꾼", port=60041, datadir=tmp), [])
app._select(("mgame",)); root.update()
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


def send(t, sender="방장", **kw):
    n = len(app.mafia_history)
    app._on_mafia_proto_msg(encode(t, **kw), sender, None); root.update()
    return len(app.mafia_history) - n


CHATS = (("hsay", "방장", dict(text="사회자 멘트", host="🖥 사회자")),
         ("asay", "방장", dict(text="AI 발언", name="레오")),
         ("sys", "방장", dict(text="시스템 안내")),
         ("user_say", "친구", dict(name="친구", text="친구 채팅")),
         ("ghost_say", "방장", dict(name="친구", text="유령 채팅")))

# 방장이 모집 → 나는 신청하지 않음 → 게임 시작(내 이름 없는 명단)
send("recruit_start", host="방장", players=["방장"])
send("start", players=["방장", "친구", "레오"], roles={})
check("참가하지 않았다고 인식(_in_game=False)", app._in_game is False)
for t, snd, kw in CHATS:
    check(f"비참가자에게 {t} 채팅이 표시되지 않음", send(t, snd, **kw) == 0)

# 대조군: 참가자면 보여야 한다
app._in_game = True
for t, snd, kw in CHATS[:2]:
    check(f"[대조] 참가자에게 {t}는 표시됨", send(t, snd, **kw) > 0)
root.destroy()
print("NON-PARTICIPANT CHAT PASSED" if ALL else "NON-PARTICIPANT CHAT FAILED")
sys.exit(0 if ALL else 1)
