# -*- coding: utf-8 -*-
"""test_ai_police.py — 경찰 AI가 조사 결과를 기억하고, 마피아로 확인된 사람을 지목·투표하며, 커밍아웃할 수 있는지."""
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
import mafia_ai
from mafia_core import Phase

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_aipolice"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("900x600"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60111, datadir=tmp), [])
core = app.core
core.players.clear()
for n, ai_ in (("나", False), ("레오", True), ("미나", True), ("철수", True), ("영희", True)):
    core.join(n, is_ai=ai_)
for n, r in (("나", "citizen"), ("레오", "police"), ("미나", "mafia"), ("철수", "citizen"), ("영희", "doctor")):
    core.players[n]["role"] = r
core.phase = Phase.DAY; core.day_no = 2

cop = mafia_ai.PlayerAgent("레오", "차분함", "#fff"); cop.booted = True
civ = mafia_ai.PlayerAgent("철수", "활발함", "#fff"); civ.booted = True
d = mafia_ai.AIDirector(); d.players = [cop, civ]
d.assign_roles({"레오": "police", "철수": "citizen"}, core)

# 1) 조사 정보의 영구 기록
cop.add_intel("미나", "mafia"); cop.add_intel("영희", "citizen")
civ.add_intel("미나", "mafia")
check("경찰 AI가 조사 결과를 영구 기록함", cop.intel == {"미나": "mafia", "영희": "citizen"})
check("경찰이 아닌 AI는 기록하지 않음", civ.intel == {})
check("확인된 마피아(생존)를 알고 있음", cop.known_mafia_alive() == ["미나"])
core.players["미나"]["alive"] = False
check("확인된 마피아가 죽으면 더 이상 대상이 아님", cop.known_mafia_alive() == [])
core.players["미나"]["alive"] = True
pr = cop._intel_prompt()
check("프롬프트에 마피아·시민 확인 정보와 지목·커밍아웃 지시가 들어감",
      "미나" in pr and "영희" in pr and "커밍아웃" in pr and "2일차" in pr)
check("경찰이 아닌 AI의 프롬프트에는 없음", civ._intel_prompt() == "")

# 2) 실제 발언 시스템 프롬프트
captured = {}
mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: (captured.update(msgs=msgs) or "ㅋㅋ 미나 좀 수상해")
cop.say("[낮 토론] 한마디 하세요")
sysmsg = captured["msgs"][0]["content"]
check("경찰 AI의 발언 프롬프트에 비밀 정보가 실림", "[경찰의 비밀 정보" in sysmsg and "마피아로 확인됨(생존): 미나" in sysmsg)
check("경찰 AI에게 '역할명은 절대 말하지 말라'는 금지가 걸려 있지 않음", "역할명은 절대 말하지 말고" not in sysmsg)
civ.say("[낮 토론] 한마디 하세요")
check("시민 AI의 프롬프트는 그대로(역할 비공개 규칙 유지)", "역할명은 절대 말하지 말고" in captured["msgs"][0]["content"])
# 기억 창을 밀어내도 정보는 유지
for i in range(60):
    cop.observe("누군가", f"잡담 {i}")
check("잡담이 쌓여 기억 창이 밀려도 조사 정보는 프롬프트에 남음",
      "미나" in (cop.say("[낮 토론] 또 한마디") and captured["msgs"][0]["content"]))

# 3) 투표 — 확인한 마피아에게 (호스트 흐름 그대로)
app.mafia_active = True; app.mafia_host_mode = True
app.ai.players = [cop]
votes_for_mafia = 0
TRIALS = 20
for i in range(TRIALS):
    core.votes.clear(); core.abstains.clear()
    core.phase = Phase.DAY
    core.votes["나"] = "철수"                               # 사람은 이미 투표(유예 대기 없음)
    mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: "투표 영희"     # LLM은 다른 사람을 고른다
    cop.busy = False
    st = {"t0": time.time()}

    def _poll():
        if "레오" in core.votes or time.time() - st["t0"] > 4:
            root.quit(); return
        root.after(20, _poll)
    # 워커 스레드가 root.after를 부르려면 mainloop 안이어야 한다(앱과 같은 조건)
    root.after(0, lambda: (app._ai_vote_in_popup(cop), _poll()))
    root.mainloop()
    votes_for_mafia += (core.votes.get("레오") == "미나")
check(f"LLM이 다른 사람을 골라도 경찰 AI는 확인한 마피아에게 투표함 ({votes_for_mafia}/{TRIALS})", votes_for_mafia >= TRIALS * 0.75)

# ============ 마피아 AI의 경찰 공격 ============
import random as _random
for txt in ("나 경찰이야", "저 경찰입니다", "내가 진짜 경찰이거든", "난 경찰이라고 했잖아"):
    check(f"커밍아웃 인식: {txt!r}", bool(app._POLICE_CLAIM_RE.search(txt)) and not app._POLICE_DENY_RE.search(txt))
for txt in ("나 경찰 아니야", "경찰이 누구야?", "저 사람 경찰 같아", "경찰 조사 결과 나왔어?", "난 시민이야"):
    check(f"커밍아웃 아님: {txt!r}", not app._POLICE_CLAIM_RE.search(txt) or bool(app._POLICE_DENY_RE.search(txt)))

app._police_claims = {}
core.players["철수"]["role"] = "citizen"
app._note_police_claim("영희", "나 경찰이야 미나 조사했어")
check("경찰을 자처한 사람이 기록됨", "영희" in app._police_claims)
app._note_police_claim("철수", "나 경찰 아니야")
check("부인하는 말은 기록하지 않음", "철수" not in app._police_claims)
core.players["영희"]["alive"] = False
check("죽은 사람은 표적 후보에서 빠짐", app._live_police_claims() == [])
core.players["영희"]["alive"] = True
check("살아 있는 자처자가 표적 후보", app._live_police_claims() == ["영희"])
core.players["영희"]["role"] = "mafia"
check("마피아 팀원의 가짜 커밍아웃은 표적이 아님(동료)", app._live_police_claims() == [])
core.players["영희"]["role"] = "doctor"

mafia = mafia_ai.PlayerAgent("미나", "장난꾸러기", "#fff"); mafia.booted = True
d2 = mafia_ai.AIDirector(); d2.players = [mafia, civ]
core.players["미나"]["role"] = "mafia"
d2.assign_roles({"미나": "mafia", "철수": "citizen"}, core, app._live_police_claims)
check("마피아 AI가 경찰 자처자를 알고 있음", mafia.police_claimants() == ["영희"])
check("시민 AI는 모름", civ.police_claimants() == [])
mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: (captured.update(msgs=msgs) or "ㅋㅋ 영희 좀 수상해")
mafia.say("[낮 토론] 한마디 하세요")
ms = captured["msgs"][0]["content"]
check("마피아 AI 발언 프롬프트에 경찰 자처자 정보와 몰아가기 지시가 들어감", "[마피아의 비밀 정보]" in ms and "영희" in ms)
check("그래도 역할 비공개 규칙은 유지됨", "역할명은 절대 말하지 말고" in ms)
app._police_claims = {}
mafia.say("[낮 토론] 한마디 하세요")
check("자처자가 없으면 프롬프트에 없음", "[마피아의 비밀 정보]" not in captured["msgs"][0]["content"])
app._police_claims = {"영희": 1}

# 낮 투표 — LLM이 다른 사람을 골라도 대부분 자처자에게
app.ai.players = [mafia]
hit = 0
for i in range(TRIALS):
    core.votes.clear(); core.abstains.clear(); core.phase = Phase.DAY
    core.votes["나"] = "철수"
    mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: "투표 철수"
    mafia.busy = False
    st = {"t0": time.time()}

    def _poll2():
        if "미나" in core.votes or time.time() - st["t0"] > 4:
            root.quit(); return
        root.after(20, _poll2)
    root.after(0, lambda: (app._ai_vote_in_popup(mafia), _poll2()))
    root.mainloop()
    hit += (core.votes.get("미나") == "영희")
check(f"마피아 AI가 경찰 자처자에게 표를 모음 ({hit}/{TRIALS})", hit >= TRIALS * 0.45)   # 기대 75%(확률 규칙이라 여유를 둔다)

# 밤 — 기본 살해 대상
kills = []
_orig_apply = app._apply_night_actions
app._apply_night_actions = lambda results: kills.append(results.get("kill"))
core.phase = Phase.NIGHT; core.day_no = 2
core.players["나"]["alive"] = True; core.players["철수"]["alive"] = True
for i in range(30):
    app._trigger_night_actions()
    root.update()
end = time.time() + 1.0
while time.time() < end:
    root.update(); time.sleep(0.02)
app._apply_night_actions = _orig_apply
share = sum(1 for k in kills if k == "영희") / max(1, len(kills))
check(f"마피아 AI의 밤 기본 살해 대상이 대부분 경찰 자처자 ({share:.2f})", len(kills) >= 25 and share >= 0.8)

# 4) 새 판이면 조사 정보 초기화
d.assign_roles({"레오": "police", "철수": "citizen"}, core)
check("새 판이 시작되면 이전 조사 정보가 지워짐", cop.intel == {})
try:
    root.destroy()
except Exception:
    pass
print("AI POLICE", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
