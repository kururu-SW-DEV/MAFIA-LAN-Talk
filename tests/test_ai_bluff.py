# -*- coding: utf-8 -*-
"""test_ai_bluff.py — 마피아 AI의 거짓 경찰/의사 커밍아웃, 그리고 경찰 AI가 정체를 숨기고 옹호만 하는지."""
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
import mafia_ui_ai
from mafia_core import Phase

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_aibluff"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("900x600"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60131, datadir=tmp), [])
core = app.core
core.players.clear()
for n, ai_ in (("나", False), ("레오", True), ("미나", True), ("철수", True), ("영희", True), ("두식", True)):
    core.join(n, is_ai=ai_)
for n, r in (("나", "citizen"), ("레오", "police"), ("미나", "mafia"), ("철수", "citizen"), ("영희", "doctor"), ("두식", "mafia")):
    core.players[n]["role"] = r
core.phase = Phase.DAY; core.day_no = 2
app.mafia_active = True; app.mafia_host_mode = True
app._police_claims = {}; app._doctor_claims = {}; app._bluff_count = 0

def mk(name, role):
    a = mafia_ai.PlayerAgent(name, "성격", "#fff"); a.booted = True; a.role = role; return a
cop, mafia1, doc, mafia2 = mk("레오", "police"), mk("미나", "mafia"), mk("영희", "doctor"), mk("두식", "mafia")
d = mafia_ai.AIDirector(); d.players = [cop, mafia1, doc, mafia2]
d.assign_roles({"레오": "police", "미나": "mafia", "영희": "doctor", "두식": "mafia"}, core, app._claims_for)
app.ai.players = d.players
said = []
app.ai.say_one_async = lambda pl, f: said.append((pl.name, f(pl)))

# ---- 1) 커밍아웃 인식(의사 포함) ----
for txt in ("나 의사야", "저 의사입니다", "내가 진짜 의사거든", "난 의사예요"):
    check(f"의사 커밍아웃 인식: {txt!r}", bool(app._DOCTOR_CLAIM_RE.search(txt)) and not app._DOCTOR_DENY_RE.search(txt))
for txt in ("나 의사 아니야", "의사가 누구야?", "저 사람 의사 같아", "병원 의사 만났어"):
    check(f"의사 커밍아웃 아님: {txt!r}", not app._DOCTOR_CLAIM_RE.search(txt) or bool(app._DOCTOR_DENY_RE.search(txt)))
app._note_police_claim("미나", "나 경찰이야 어제 철수 조사했어")
app._note_police_claim("두식", "나 의사야 어젯밤 미나 지켰어")
check("마피아 AI의 거짓 경찰·의사 커밍아웃도 기록됨", "미나" in app._police_claims and "두식" in app._doctor_claims)

# ---- 2) 누가 어떻게 보는가 ----
check("마피아 시점: 동료(미나)의 가짜 경찰은 표적에서 제외", "미나" not in mafia2.police_claimants())
check("마피아 시점: 동료(두식)의 가짜 의사는 표적에서 제외", "두식" not in mafia1.doctor_claimants())
check("의사 AI는 마피아의 가짜 경찰도 진짜일 수 있다고 보고 보호 후보에 넣음(전지적이지 않음)", doc.public_police_claims() == ["미나"])
check("진짜 경찰 AI는 다른 경찰 자처자를 확실한 마피아(거짓말쟁이)로 앎", cop.known_mafia_alive() == ["미나"])
check("진짜 의사 AI는 다른 의사 자처자를 확실한 마피아로 앎", doc.known_mafia_alive() == ["두식"])
check("시민·마피아 AI는 그런 확신이 없음", mafia1.known_mafia_alive() == [])

# ---- 3) 거짓 커밍아웃 발동 ----
app._police_claims = {}; app._doctor_claims = {}; app._bluff_count = 0
for pl in d.players:
    pl.bluffed = False
_REAL_RM = mafia_ui_ai.random_mod
mafia_ui_ai.random_mod = type("R", (), {"random": staticmethod(lambda: 0.0), "choice": staticmethod(lambda x: x[0]),
                                        "randint": staticmethod(lambda a, b: a)})
ok = app._ai_mafia_bluff()
check("조건이 맞으면 마피아 AI가 거짓 커밍아웃을 시작함", ok and len(said) == 1)
who, prompt = said[0]
check("발언자는 마피아 AI이고, 시민 쪽·경찰·의사 AI가 아님", who in ("미나", "두식"))
check("프롬프트가 거짓 커밍아웃을 지시하고 반드시 '나 경찰이야/나 의사야' 표현을 쓰게 함",
      "거짓 커밍아웃" in prompt and ("'나 경찰이야'" in prompt or "'나 의사야'" in prompt))
check("경찰 사칭이면 동료를 감싸거나 시민을 몰라는 가짜 조사 결과를 지어내게 함",
      "나 경찰이야" not in prompt or "조사 결과를 지어내세요" in prompt)
check("AI마다 1회만(같은 AI는 다시 안 함)", getattr([p for p in d.players if p.name == who][0], "bluffed", False) is True)
app._bluff_count = 0
n1 = len(said)
for _ in range(5):
    app._ai_mafia_bluff()
check("판당 최대 2회, AI당 1회 제한이 지켜짐", app._bluff_count <= 2 and len(said) - n1 <= 1)
core.phase = Phase.NIGHT
before = len(said)
check("낮이 아니면 시작하지 않음", app._ai_mafia_bluff() is False and len(said) == before)
core.phase = Phase.DAY

# 맞불: 진짜일 수 있는 경찰 자처자(사람)가 있으면 반박하며 나야말로 진짜라고 주장
app._bluff_count = 0
for pl in d.players:
    pl.bluffed = False
app._police_claims = {"철수": 2}
said.clear()
core.players["철수"]["role"] = "citizen"
app._ai_mafia_bluff()
check("경찰 자처자가 이미 있으면 맞불(나야말로 진짜)을 지시함", said and "맞불" in said[0][1] and "철수" in said[0][1])

# ---- 4) 밤 표적: 의사 자처자도 노린다 ----
app._police_claims = {}; app._doctor_claims = {"철수": 2}
hits = sum(1 for _ in range(200) if app._mafia_claim_target(["나", "철수", "영희"]) == "철수")
check(f"경찰 자처자가 없으면 의사 자처자를 표적으로 삼음 ({hits}/200, 기대 70%)", 100 <= hits <= 180)
app._police_claims = {"나": 2}
hits2 = sum(1 for _ in range(200) if app._mafia_claim_target(["나", "철수", "영희"]) == "나")
check(f"경찰 자처자가 있으면 그 사람이 우선 ({hits2}/200, 기대 90%)", hits2 >= 160)

# ---- 5) 경찰 AI: 정체를 숨기고 옹호만 ----
mafia_ui_ai.random_mod = _REAL_RM          # 아래부터는 실제 난수 사용
app._police_claims = {}; app._doctor_claims = {}
cop.intel = {"영희": "citizen", "미나": "mafia"}
pr = cop._intel_prompt()
check("경찰 프롬프트: 정체·조사 사실을 숨기고 마피아를 폭로하지 말라고 지시", "경찰이라는 사실과 '조사했다'는 말은 절대 하지 마세요" in pr and "폭로하지도 마세요" in pr)
check("경찰 프롬프트: 커밍아웃 지시가 없음", "커밍아웃" not in pr)
check("경찰 프롬프트: 마피아가 아닌 사람이 몰릴 때만 옹호", "의심받거나 몰릴 때만" in pr and "마피아 아닌 것 같아요" in pr)
mafia_ai._llm_call = lambda msgs, max_tokens=350, timeout=45: "ㅋㅋ 그렇지"
said.clear()
cd = app.__dict__.setdefault("_defend_cooldown", {})
scheduled = []
app.root.after = lambda ms, fn=None, *a: (scheduled.append(fn) if fn else None) or "x"
triggered = 0
for i in range(40):
    cd.clear(); scheduled.clear()
    app._maybe_police_defend("철수", "영희 좀 수상하지 않아?")
    triggered += bool(scheduled)
check(f"마피아가 아님이 확인된 사람이 의심받으면 경찰 AI가 옹호에 나섬 ({triggered}/40, 기대 70%)", triggered >= 20)
for _ in range(60):                        # 70% 확률이라 발동할 때까지 반복
    cd.clear(); scheduled.clear()
    app._maybe_police_defend("철수", "영희 좀 수상하지 않아?")
    if scheduled:
        break
for fn in scheduled[:1]:
    fn()
check("옹호 프롬프트: 정체·조사 언급 금지 + 다른 사람을 마피아로 지목하지 말라", said and "절대 하지 말고" in said[-1][1] and "지목하지 마세요" in said[-1][1] and "영희" in said[-1][1])
cd.clear(); scheduled.clear()
app._maybe_police_defend("철수", "미나 좀 수상하지 않아?")
check("확인된 마피아(미나)를 의심하는 말에는 끼어들지 않음(폭로 없음)", not scheduled)
app._maybe_police_defend("철수", "오늘 날씨 좋다")
check("의심 발언이 아니면 반응하지 않음", not scheduled)
cd.clear()
app._maybe_police_defend("영희", "영희 나는 아니야 수상하지 않아")
check("본인이 자기변호하는 말에는 옹호하지 않음", not scheduled)

# ---- 거짓 커밍아웃 롤백(LLM이 말하지 못한 경우) ----
mafia_ui_ai.random_mod = type("R", (), {"random": staticmethod(lambda: 0.0), "choice": staticmethod(lambda x: x[0]),
                                        "randint": staticmethod(lambda a, b: a)})
app._police_claims = {}; app._doctor_claims = {}; app._bluff_count = 0
for pl in d.players:
    pl.bluffed = False
said.clear()
app._ai_mafia_bluff()
who_pl = [p for p in d.players if p.name == said[0][0]][0]
check("거짓 커밍아웃을 시작하면 횟수·표식이 소모됨", app._bluff_count == 1 and who_pl.bluffed)
app._bluff_rollback(who_pl)        # 말이 채팅에 안 나온 채 30초가 지난 상황
check("실제로 말하지 못했다면(기록 없음) 횟수와 표식이 되돌아옴", app._bluff_count == 0 and who_pl.bluffed is False)
app._ai_mafia_bluff()
who_pl = [p for p in d.players if p.name == said[-1][0]][0]
app._note_police_claim(who_pl.name, "나 경찰이야 어제 조사했어")
app._bluff_rollback(who_pl)
check("커밍아웃이 실제로 기록됐다면 되돌리지 않음", app._bluff_count == 1 and who_pl.bluffed is True)
mafia_ui_ai.random_mod = _REAL_RM

try:
    root.destroy()
except Exception:
    pass
print("AI BLUFF", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
