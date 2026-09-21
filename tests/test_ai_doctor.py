# -*- coding: utf-8 -*-
"""test_ai_doctor.py — 의사 AI가 밤 결과를 기록하고, 경찰 자처자를 지키며(밤 보호·낮 투표 회피), 정체는 숨기는지."""
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


tmp = os.path.join(BASE, "tmp_aidoctor"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("900x600"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60121, datadir=tmp), [])
core = app.core
core.players.clear()
for n, ai_ in (("나", False), ("레오", True), ("미나", True), ("철수", True), ("영희", True)):
    core.join(n, is_ai=ai_)
for n, r in (("나", "citizen"), ("레오", "doctor"), ("미나", "mafia"), ("철수", "citizen"), ("영희", "police")):
    core.players[n]["role"] = r
core.phase = Phase.NIGHT; core.day_no = 2
app.mafia_active = True; app.mafia_host_mode = True
app._police_claims = {}

doc = mafia_ai.PlayerAgent("레오", "차분함", "#fff"); doc.booted = True
civ = mafia_ai.PlayerAgent("철수", "활발함", "#fff"); civ.booted = True
d = mafia_ai.AIDirector(); d.players = [doc, civ]
d.assign_roles({"레오": "doctor", "철수": "citizen"}, core, app._claims_for)
app.ai.players = [doc, civ]

# 1) 프롬프트
check("아무 정보가 없으면 의사 프롬프트 블록이 없음", doc._doctor_intel_prompt() == "")
app._police_claims = {"영희": 1}
p = doc._doctor_intel_prompt()
check("경찰 자처자가 있으면 의사 프롬프트에 정보와 '감싸라'는 지시가 들어감", "영희" in p and "감싸" in p)
check("의사임을 밝히지 말라는 지시가 있음", "절대 쓰지 마세요" in p)
check("의사가 아닌 AI에는 없음", civ._doctor_intel_prompt() == "")
captured = {}
mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: (captured.update(msgs=msgs) or "영희 믿어볼래")
doc.say("[낮 토론] 한마디 하세요")
sysmsg = captured["msgs"][0]["content"]
check("의사 AI 발언 프롬프트에 정보가 실리고 역할 비공개 규칙도 유지됨",
      "[의사의 비밀 정보" in sysmsg and "역할명은 절대 말하지 말고" in sysmsg)

# 2) 밤 결과 기록
core.last_protect = "영희"; core.night_target = "영희"
app._record_doctor_night(False, None)
check("보호 대상이 노려졌다가 살아남으면 그렇게 기록됨", any("살려냈습니다" in o for _, _, o in doc.doctor_log))
core.last_protect = "철수"; core.night_target = "미나"
app._record_doctor_night(True, "미나")
check("다른 사람이 죽으면 사망으로 기록됨", any("미나님이 사망" in o for _, _, o in doc.doctor_log))
core.night_target = None
app._record_doctor_night(False, None)
check("희생자가 없고 노려진 것도 없으면 단순 기록", doc.doctor_log[-1][2] == "희생자가 없었습니다")
for i in range(8):
    doc.add_doctor_result(i, "철수", "x")
check("기록은 최근 5건만 유지", len(doc.doctor_log) == 5)
d.assign_roles({"레오": "doctor", "철수": "citizen"}, core, app._claims_for)
check("새 판이면 기록이 초기화됨", doc.doctor_log == [])
p2 = doc._doctor_intel_prompt()
doc.add_doctor_result(2, "영희", "마피아가 바로 그 사람을 노렸지만 살려냈습니다(희생자 없음)")
p3 = doc._doctor_intel_prompt()
check("기록이 프롬프트에 들어감", "영희님을 보호했고" in p3 and "살려냈습니다" in p3)

# 3) 밤 보호 선택 — 경찰 자처자를 지킨다
saved = []
app._apply_night_actions = lambda r: saved.append(r.get("save"))
app._night_ai_decide = lambda pl, n, prompt, cands, apply_fn, fb=None: apply_fn("철수" if "철수" in cands else cands[0])
core.last_protect = None
prompts = []
_orig_dec = app._night_ai_decide
app._night_ai_decide = lambda pl, n, prompt, cands, apply_fn, fb=None: (prompts.append(prompt), apply_fn("철수"))
N = 40
for i in range(N):
    app._night_ai_doctor(doc, 2)
share = sum(1 for s in saved if s == "영희") / N
check(f"LLM이 다른 사람을 골라도 의사 AI가 경찰 자처자를 지킴 ({share:.2f})", share >= 0.65)
check("밤 프롬프트에 경찰 자처자 정보와 지난 밤 기록이 들어감", "경찰이라고 밝힌 사람: 영희" in prompts[0] and "지난 밤 기록" in prompts[0])
app._police_claims = {}
saved.clear()
for i in range(20):
    app._night_ai_doctor(doc, 2)
check("자처자가 없으면 LLM의 선택을 그대로 따름", all(s == "철수" for s in saved))
app._police_claims = {"영희": 1}

# 4) 낮 투표 — 경찰 자처자에게는 잘 투표하지 않는다
app.ai.players = [doc]
core.phase = Phase.DAY
avoid = 0
TR = 20
for i in range(TR):
    core.votes.clear(); core.abstains.clear(); core.phase = Phase.DAY
    core.votes["나"] = "철수"
    mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: "투표 영희"     # LLM은 자처자를 고른다
    doc.busy = False
    st = {"t0": time.time()}

    def _poll():
        if "레오" in core.votes or time.time() - st["t0"] > 4:
            root.quit(); return
        root.after(20, _poll)
    root.after(0, lambda: (app._ai_vote_in_popup(doc), _poll()))
    root.mainloop()
    avoid += (core.votes.get("레오") not in (None, "영희"))
check(f"LLM이 경찰 자처자를 골라도 의사 AI는 대부분 다른 사람에게 투표함 ({avoid}/{TR})", avoid >= TR * 0.55)
try:
    root.destroy()
except Exception:
    pass
print("AI DOCTOR", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
