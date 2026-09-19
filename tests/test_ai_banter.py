# -*- coding: utf-8 -*-
"""test_ai_banter.py — 낮 토론에서 AI가 사람 발언 없이도 다른 AI를 지목해 서로 캐묻는지."""
import os
import sys

if __name__ != "__main__":
    sys.exit(0)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
import mafia_ai
from mafia_core import GameCore, Phase
from mafia_ui import MafiaUIMixin

ok_all = True


def check(label, cond):
    global ok_all
    print(("OK  " if cond else "FAIL"), label)
    ok_all = ok_all and bool(cond)


class FakePl:
    def __init__(self, name):
        self.name, self.alive, self.booted, self.busy, self.prompts = name, True, True, False, []


class Stub(MafiaUIMixin):
    _NO_PILE_ON = ""

    def __init__(self, root):
        self.root = root
        self.core = GameCore("t")
        self.core.phase = Phase.DAY
        self.mafia_active = True
        self.ai = mafia_ai.AIDirector()
        self.ai.players = [FakePl("가"), FakePl("나"), FakePl("다")]
        self.calls = []
        self.ai.say_one_async = lambda pl, f: self.calls.append((pl.name, f(pl)))


root = tk.Tk()
root.withdraw()
s = Stub(root)
s.core.players = {n: {"is_ai": True, "alive": True, "role": "citizen"} for n in "가나다"}
s.core.alive_players = lambda: ["가", "나", "다", "나사람"]
s._ai_vs_ai_banter()
root.update()
check("첫 AI가 즉시 발언 시작", len(s.calls) == 1)
a_name, a_prompt = s.calls[0]
others = [n for n in "가나다" if n != a_name]
check("프롬프트가 다른 AI 이름을 지목", any(f"'{o}'" in a_prompt for o in others))
check("사람 이름을 지목하지 않음", "나사람" not in a_prompt.split("생존:")[1].split("\n")[1])
root.after(8000, root.quit)
root.mainloop()
check("지목된 AI가 받아침", len(s.calls) == 2 and s.calls[1][0] != a_name)
s.core.phase = Phase.NIGHT
n = len(s.calls)
s._ai_vs_ai_banter()
check("낮이 아니면 시작하지 않음", len(s.calls) == n)
print("AI BANTER PASSED" if ok_all else "AI BANTER FAILED")
sys.exit(0 if ok_all else 1)
