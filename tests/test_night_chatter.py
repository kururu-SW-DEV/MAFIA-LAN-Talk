# -*- coding: utf-8 -*-
"""test_night_chatter.py — 밤 AI 잡담이 고정 문구가 아니라 LLM 발언/예비 문구 풀에서 나오는지."""
import os
import sys
import time

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
    def __init__(self, name, reply):
        self.name, self.alive, self.booted, self.reply, self.prompts = name, True, True, reply, []

    def say(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class Stub(MafiaUIMixin):
    def __init__(self, root, players):
        self.root = root
        self.core = GameCore("t")
        self.core.phase = Phase.NIGHT
        self.mafia_active = True
        self.ai = mafia_ai.AIDirector()
        self.ai.players = players
        self.posted = []
        self.observed = []
        self.ai.observe_all = lambda n, t: self.observed.append((n, t))

    def add_mafia_ai(self, name, text):
        self.posted.append((name, text))


def pump(cond, timeout=5.0):
    """mainloop()+quit() 폴링 — 워커 스레드가 root.after를 부르려면 Tk가 mainloop 안에 있어야 한다
    (앱 본체와 같은 조건)."""
    st = {"t0": time.time(), "done": False}

    def _poll():
        if cond():
            st["done"] = True
            root.quit()
        elif time.time() - st["t0"] > timeout:
            root.quit()
        else:
            root.after(15, _poll)
    root.after(0, _poll)
    root.mainloop()
    return st["done"]


root = tk.Tk(); root.withdraw()

# 1) LLM 발언이 그대로 올라오고, 프롬프트에 역할 노출 금지 규칙이 들어간다
a, b = FakePl("루카", "오늘따라 조용해서 괜히 오싹하네요."), FakePl("미나", "다들 자고 있는 건 아니죠?")
s = Stub(root, [a, b])
s._ai_night_chatter(2)
check("1) LLM이 만든 발언 2개가 게시됨", pump(lambda: len(s.posted) == 2))
check("1) 고정 문구가 아니라 LLM 발언이 사용됨", {t for _, t in s.posted} == {a.reply, b.reply})
check("1) 다른 AI들이 기억하도록 observe_all 호출됨", len(s.observed) == 2)
check("1) 프롬프트가 역할 노출 금지를 명시함", all("절대 금지" in p for pl in (a, b) for p in pl.prompts))

# 2) LLM 실패(None/예외) 시 예비 문구 풀에서 뽑고, 영어 고정문구가 아님
c, d = FakePl("제이", None), FakePl("레오", RuntimeError("boom"))
s2 = Stub(root, [c, d])
s2._ai_night_chatter(2)
check("2) LLM 실패 시에도 잡담 2개가 게시됨", pump(lambda: len(s2.posted) == 2))
check("2) 예비 문구 풀에서 나옴", all(t in Stub._NIGHT_FALLBACK_LINES for _, t in s2.posted))
check("2) 두 AI가 같은 문구를 쓰지 않음", s2.posted[0][1] != s2.posted[1][1])
check("2) 영어 고정 문구(Nobody talks at night)는 없어짐",
      not any("Nobody" in l for l in Stub._NIGHT_FALLBACK_LINES))

# 3) 예비 문구는 풀을 다 쓰기 전엔 반복되지 않는다
s3 = Stub(root, [])
n = len(Stub._NIGHT_FALLBACK_LINES)
picked = [s3._pick_night_fallback() for _ in range(n)]
check("3) 예비 문구가 한 바퀴 돌 때까지 겹치지 않음", len(set(picked)) == n)
check("3) 풀 소진 뒤에도 계속 뽑힘", s3._pick_night_fallback() in Stub._NIGHT_FALLBACK_LINES)

# 4) 응답이 늦어 이미 아침이 되었으면 게시하지 않는다
e = FakePl("소피", "밤이 길다…")
s4 = Stub(root, [e])
s4._ai_night_chatter(1)
s4.core.phase = Phase.DAY
pump(lambda: False, timeout=1.5)
check("4) 아침이 된 뒤에는 밤 잡담이 게시되지 않음", s4.posted == [])

# 5) 죽은 AI/미부팅 AI는 말하지 않는다
f = FakePl("그림자", "…")
f.alive = False
s5 = Stub(root, [f])
s5._ai_night_chatter(2)
pump(lambda: False, timeout=1.0)
check("5) 사망한 AI는 잡담하지 않음", s5.posted == [] and f.prompts == [])

root.destroy()
print("NIGHT CHATTER", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
