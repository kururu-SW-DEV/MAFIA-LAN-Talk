# -*- coding: utf-8 -*-
"""test_remote_human_ai.py — 원격 사람 참가자의 발언에 AI가 반응하고, 먼저 말도 걸며, 투표를 기다려 주는지."""
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
from mafia_net import encode
from mafia_core import Phase

tmp = os.path.join(BASE, "tmp_remoteai"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60091, datadir=tmp), [])
app._select(("mgame",)); root.update()
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


class FakePl:
    def __init__(self, name):
        self.name, self.alive, self.booted, self.busy, self.freq = name, True, True, False, 50


app.core.players.clear()
app.core.join("나", is_ai=False); app.core.join("친구", is_ai=False)
app.core.join("레오", is_ai=True); app.core.join("미나", is_ai=True)
app.core.phase = Phase.DAY
app.mafia_active = True; app.mafia_host_mode = True
app.ai.players = [FakePl("레오"), FakePl("미나")]
app.ai.observe_all = lambda who, txt: None
one, many, prompts = [], [], []
app.ai.say_one_async = lambda pl, f: (one.append(pl.name), prompts.append(f(pl)))
app.ai.say_async = lambda f, honor_freq=True: (many.append(1), prompts.append(f(app.ai.players[0])))

# 1) 원격 참가자 발언 → AI 반응
import random
random.seed(1)
app._last_ai_trigger = 0
n = 0
for _ in range(30):                        # 무반응 확률(15%)이 있으므로 여러 번 시도
    app._last_ai_trigger = 0
    app._on_mafia_proto_msg(encode("user_say", name="친구", text="다들 오늘 좀 조용하네요"), "친구", None)
    pump(1.0)
    if one:
        break
check("1) 원격 참가자의 발언에 AI가 반응함", bool(one))
check("1) 반응 프롬프트가 원격 참가자('친구')를 가리킴", any("친구" in p for p in prompts))

# 2) 원격 참가자가 AI 이름을 부르면 그 AI가 답함
prompts.clear(); many.clear()
app._on_mafia_proto_msg(encode("user_say", name="친구", text="레오 너 수상해"), "친구", None)
pump(2.0)
check("2) AI 이름을 부르면 지목 답변이 호출됨", bool(many))
check("2) 지목 프롬프트에 원격 화자 이름이 들어감", any("'친구'님이 당신('레오')" in p for p in prompts))

# 3) 협박 발언 반응에 화자 이름
lines = []
app.add_mafia_bubble = lambda t, name=None, **kw: lines.append((t, name))
for _ in range(40):
    app._ai_threat_reaction(app.ai.players[1], "친구")
check("3) 협박 반응이 (호스트 '나'가 아니라) 원격 화자 이름을 부름",
      any("친구님" in t for t, _ in lines) and not any("나님" in t for t, _ in lines))

# 4) AI가 조용한 사람에게 먼저 말을 건다
one.clear(); prompts.clear()
app._human_last_talk = {"나": time.time(), "친구": time.time() - 120}
asked = app._ai_ask_human()
check("4) 가장 오래 조용한 사람에게 AI가 먼저 말을 검", asked and len(one) == 1 and "'친구'" in prompts[0])
one.clear()
app._human_last_talk = {"나": time.time(), "친구": time.time()}
check("4) 방금 말한 사람에게는 걸지 않음", app._ai_ask_human() is False and not one)

# 5) 원격 참가자 투표를 AI가 기다려 줌
app._vote_popup_open_ts = time.time()
app.core.votes.clear(); app.core.abstains.clear() if hasattr(app.core, "abstains") else None
app.core.votes["나"] = "레오"
check("5) 호스트가 투표했어도 원격 참가자가 미투표면 AI는 대기", app._ai_vote_user_should_wait() is True)
app.core.votes["친구"] = "미나"
check("5) 사람이 모두 투표하면 대기하지 않음", app._ai_vote_user_should_wait() is False)
app.core.votes.pop("친구")
app._vote_popup_open_ts = time.time() - 6
check("5) 유예시간(5초)이 지나면 대기하지 않음", app._ai_vote_user_should_wait() is False)
app.core.players["친구"]["alive"] = False
app._vote_popup_open_ts = time.time()
check("5) 미투표 사람이 사망이면 기다리지 않음", app._ai_vote_user_should_wait() is False)

root.destroy()
print("REMOTE HUMAN AI PASSED" if ALL else "REMOTE HUMAN AI FAILED")
sys.exit(0 if ALL else 1)
