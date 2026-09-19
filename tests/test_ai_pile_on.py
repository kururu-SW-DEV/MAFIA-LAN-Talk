# -*- coding: utf-8 -*-
"""test_ai_pile_on.py — AI가 사람 한 명에게 몰려 "다구리" 치지 않도록 한 조치 검증.

  1) 사람 발언 하나에 대답하는 AI는 상한(기본 1명)을 넘지 않고, 일부는 무반응
  2) 게임 상황 반응(max_replies 없음)은 예전처럼 say_async로 전원 대상
  3) 프롬프트에 '몰아가기 금지' 지시가 들어간다
  4) 몰표 방지: 이미 AI 표가 절반 이상 쌓인 대상은 뒤의 AI가 일정 확률로 다른 후보로 돌리고,
     사람이든 AI든 똑같이 적용된다
"""
import os
import random
import sys

if __name__ != "__main__":
    sys.exit(0)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mafia_ai
from mafia_core import GameCore, Phase
from mafia_ui import MafiaUIMixin

ok_all = True


def check(label, cond):
    global ok_all
    print(("OK  " if cond else "FAIL"), label)
    ok_all = ok_all and bool(cond)


class Pl:
    def __init__(self, name, freq=50):
        self.name, self.freq, self.alive, self.booted, self.busy = name, freq, True, True, False


class FakeRoot:
    def after(self, ms, fn=None, *a):
        if fn:
            fn(*a)


class Stub(MafiaUIMixin):
    def __init__(self, names, human="나"):
        self.root = FakeRoot()
        self.core = GameCore("t")
        self.core.day_no = 1
        self.core.join(human, is_ai=False)      # join은 LOBBY 단계에서만 가능 — 등록 뒤에 DAY로
        self.engine = type("E", (), {"name": human})()
        self.ai = mafia_ai.AIDirector()
        self.ai.players = [Pl(n, f) for n, f in names]
        for n, _ in names:
            self.core.join(n, is_ai=True)
        self.core.phase = Phase.DAY
        self.said_one, self.said_all, self.prompts = [], 0, []
        self.ai.say_one_async = lambda pl, fac: (self.said_one.append(pl.name), self.prompts.append(fac(pl)))
        self.ai.say_async = lambda fac: (setattr(self, "said_all", self.said_all + 1), self.prompts.append(fac(self.ai.players[0])))

    def _show_ai_typing_hint(self):
        pass

    def _host_quick_comment_bg(self, context=""):
        pass


random.seed(7)
AIS = [("루카", 70), ("미나", 60), ("제이", 20), ("레오", 85)]

# 1) 발언당 대답 수 상한 + 무반응 비율 + 발언 확률 가중
s = Stub(AIS)
import mafia_ui
mafia_ui.threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()   # 사회자 LLM 스레드 차단
N = 2000
per_call = []
for _ in range(N):
    before = len(s.said_one)
    s._trigger_ai_reactions("플레이어 '나'의 발언: \"안녕\"", max_replies=1)
    per_call.append(len(s.said_one) - before)
check("1) 발언 하나에 대답하는 AI는 최대 1명", max(per_call) <= 1)
skip = per_call.count(0) / N
check(f"1) 약 15%는 무반응 (실측 {skip:.2f})", 0.10 <= skip <= 0.20)
counts = {n: s.said_one.count(n) for n, _ in AIS}
check(f"1) 발언 확률이 높은 AI가 더 자주 답함 (레오 {counts['레오']} > 제이 {counts['제이']})",
      counts["레오"] > counts["제이"])
s2 = Stub(AIS)
before = len(s2.said_one)
mafia_ui.AI_REACT_SKIP_PROB = 0.0
s2._trigger_ai_reactions("플레이어 '나'의 발언: \"안녕\"", max_replies=2)
check("1) max_replies=2면 서로 다른 AI 2명", len(set(s2.said_one[before:])) == 2)
mafia_ui.AI_REACT_SKIP_PROB = 0.15

# 2) 게임 상황 반응은 전원 대상(기존 동작 유지)
s3 = Stub(AIS)
s3._trigger_ai_reactions("사회자가 개회를 선언했습니다.")
check("2) max_replies 없으면 say_async(전원)로 처리", s3.said_all == 1 and s3.said_one == [])

# 3) 프롬프트에 몰아가기 금지
check("3) 반응 프롬프트에 몰아가기 금지 지시 포함", any("한 사람에게만 집중해서" in p for p in s.prompts[:5] + s3.prompts))
check("3) AI 시스템 프롬프트에도 다구리 금지 규칙이 있음",
      "다구리 금지" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mafia_ai.py"), encoding="utf-8").read())


# 4) 몰표 방지 — 표적이 사람이든 AI든 동일
def simulate(target_of_all, trials=3000):
    hits, full = 0, 0
    for _ in range(trials):
        st = Stub(AIS)
        for pl in st.ai.players:
            t = st._pile_on_redirect(pl.name, target_of_all if pl.name != target_of_all else "나")
            st.core.votes[pl.name] = t
        n = sum(1 for v in st.core.votes.values() if v == target_of_all)
        hits += n
        full += (n == (4 if target_of_all == "나" else 3))
    return hits / trials, full / trials


mean_h, full_h = simulate("나")
mean_a, full_a = simulate("레오")
check(f"4) 모두 사람을 찍으려 해도 평균 {mean_h:.2f}표(4표 전원 몰표 비율 {full_h:.2f})로 분산됨", mean_h < 3.3 and full_h < 0.30)
check(f"4) AI 한 명에게 몰려도 똑같이 분산(사람만 봐주지 않음): 평균 {mean_a:.2f}표", mean_a < 2.7)
st = Stub(AIS)
st.core.votes.update({"루카": "나"})
check("4) 표가 1개뿐이면(한계 미만) 그대로 통과", all(st._pile_on_redirect("미나", "나") == "나" for _ in range(50)))
st.core.votes.update({"루카": "나", "미나": "나"})
redirected = sum(1 for _ in range(1000) if st._pile_on_redirect("제이", "나") != "나") / 1000
check(f"4) 이미 2표 쌓이면 약 60% 다른 후보로 돌림 (실측 {redirected:.2f})", 0.5 <= redirected <= 0.7)
check("4) 돌린 대상은 자기 자신/원래 대상이 아님", all(
    st._pile_on_redirect("제이", "나") not in ("제이",) for _ in range(200)))

print("AI PILE-ON", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
