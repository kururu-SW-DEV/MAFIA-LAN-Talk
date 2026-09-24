# -*- coding: utf-8 -*-
"""test_ai_pacing.py — AI 채팅: 한 줄 강제, 동시 발언 인원 제한, 발언 간격 조절."""
import os, sys, time
from types import SimpleNamespace
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import tkinter as tk
import mafia_ai
from mafia_ai import to_one_line, AIDirector
ok_all = True
def check(l, c):
    global ok_all; print("OK  " if c else "FAIL", l); ok_all = ok_all and bool(c)

check("여러 줄이면 첫 줄만", to_one_line("안녕하세요.\n두번째 줄이에요.") == "안녕하세요.")
long = "음 저는 민수님이 좀 수상해 보이는데요 왜냐하면 아까부터 계속 말이 없었고 그러다가 갑자기 영희님을 지목했거든요 ㅋㅋ 그래서 의심돼요"
r = to_one_line(long)
check("긴 문장은 40자 이내로 자름", 0 < len(r) <= 40)
check("짧은 문장은 그대로", to_one_line("ㅋㅋ 그러게요~ 너무 몰아가는 거 아냐?") == "ㅋㅋ 그러게요~ 너무 몰아가는 거 아냐?")
check("문장 끝에서 자름", to_one_line("첫 문장이에요. " + "가" * 80) == "첫 문장이에요.")

d = AIDirector()
d.players = [SimpleNamespace(name=f"AI{i}", alive=True, freq=95, booted=True, busy=False) for i in range(6)]
called = []
d._say_worker = lambda pl, f: called.append(pl.name)
for _ in range(10):
    called.clear(); d.say_async(lambda pl: "x"); time.sleep(0.15)
    if len(called) > 2:
        break
check("한 번에 최대 2명만 발언", len(called) <= 2)

from mafia_ui import MafiaUIMixin
root = tk.Tk(); root.withdraw()
shown = []
class Stub(MafiaUIMixin):
    def __init__(self):
        self.root = root; self.mafia_active = False; self.ai = None
    def add_mafia_ai(self, n, t): shown.append((time.time(), n, t))
    def _note_police_claim(self, *a): pass
    def _maybe_police_defend(self, *a): pass
s = Stub()
t0 = time.time()
for i in range(3):
    s._on_ai_utt(f"AI{i}", "#fff", f"안녕 {i}")
while time.time() - t0 < 24 and len(shown) < 3:
    root.update(); time.sleep(0.02)
check("3개 발언이 모두 표시됨", len(shown) == 3)
gaps = [shown[i + 1][0] - shown[i][0] for i in range(len(shown) - 1)]
check("발언 사이에 5초 이상 간격", all(g >= 4.9 for g in gaps))
check("순서 유지", [x[1] for x in shown] == ["AI0", "AI1", "AI2"])

# v1.109) 최후 변론 중에는 피고인 외의 AI 발언을 내보내지 않는다
s2 = Stub(); s2.core = SimpleNamespace(defendant="AI1", players={})
shown.clear()
r_other = s2._show_ai_utt("AI2", "#fff", "끼어들기")
r_def = s2._show_ai_utt("AI1", "#fff", "억울해요")
check("변론 중 다른 AI의 발언은 폐기", r_other is False and not any(x[1] == "AI2" for x in shown))
check("변론 중 피고인의 발언은 통과", r_def and any(x[1] == "AI1" for x in shown))
root.destroy()
print("AI PACING", "PASSED" if ok_all else "FAILED"); sys.exit(0 if ok_all else 1)
