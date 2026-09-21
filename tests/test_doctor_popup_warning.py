# -*- coding: utf-8 -*-
"""test_doctor_popup_warning.py — 의사가 어젯밤 치료한 사람을 또 고르면 팝업 안에 중복 경고가 뜨는지."""
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

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_docwarn"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60161, datadir=tmp), [])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("나", False), ("철수", True), ("영희", True), ("미나", True), ("레오", True)):
    core.join(n, is_ai=a)
for n, r in (("나", "doctor"), ("철수", "citizen"), ("영희", "police"), ("미나", "mafia"), ("레오", "citizen")):
    core.players[n]["role"] = r
core.phase = Phase.NIGHT; core.day_no = 2
app.mafia_active = True; app.mafia_host_mode = True
core.last_protect = "철수"                  # 어젯밤 치료한 사람


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


def texts(w, out=None):
    out = [] if out is None else out
    try:
        t = w.cget("text")
        if t:
            out.append(str(t))
    except Exception:
        pass
    for c in w.winfo_children():
        texts(c, out)
    return out


app._show_night_panel(); pump(0.4)
ov = app._mafia_overlay
allt = texts(ov)
check("의사 팝업이 열림", ov is not None)
check("어젯밤 치료한 사람 버튼에 '어제 치료' 표시(미리 선택 불가)", any("철수" in t and "어제 치료" in t for t in allt))
check("팝업 안에 연속 치료 불가 안내 문구가 있음", any("연속으로 치료할 수 없습니다" in t for t in allt))
check("경고 줄(빈 상태)이 팝업 안에 준비됨", getattr(app, "_night_warn_lbl", None) is not None)

# 억지로 눌렀을 때(단축/경합) — 호스트가 거절하고 팝업 안에 경고가 뜬다
app._apply_night_pick("철수", "doctor"); pump(0.3)
warn = app._night_warn_lbl.cget("text")
check(f"중복 선택 시 팝업 안에 경고가 표시됨: {warn!r}", "중복 치료는 안 됩니다" in warn and "철수" in warn)
check("경고 문구에서 내부 표기 '(나 의사)'는 빠짐", "(나 의사)" not in warn)
check("팝업은 닫히지 않고 다시 고를 수 있음", app._mafia_overlay is not None and app._mafia_overlay.winfo_exists())
check("같은 경고가 채팅 쪽지로도 남음", any("중복 치료는 안 됩니다" in (r.get("text") or "") for r in app.mafia_history))
check("치료 대상은 바뀌지 않음", core.night_saved != "철수")

# 다른 사람을 고르면 접수되고 팝업이 닫힘
app._apply_night_pick("영희", "doctor"); pump(0.3)
check("다른 사람을 고르면 접수되고 팝업이 닫힘", core.night_saved == "영희" and app._mafia_overlay is None)

# 원격 참가자: 호스트의 거절 쪽지(hdm)가 오면 팝업 안에 경고
core.night_saved = None
app.mafia_host_mode = False; app._recruiter_host = "방장"
app._show_night_panel(); pump(0.3)
app._on_mafia_proto_msg(encode("hdm", target="나", text="⚠ (나 의사) 철수님은 어젯밤 이미 치료한 사람입니다 — 중복 치료는 안 됩니다. 다른 대상을 선택하세요"), "방장", None)
pump(0.3)
check("원격 참가자: 거절 쪽지가 오면 팝업 안에 경고가 뜸", "중복 치료는 안 됩니다" in app._night_warn_lbl.cget("text"))
check("원격 참가자: 거절이면 팝업이 유지됨", app._mafia_overlay is not None and app._mafia_overlay.winfo_exists())

# 채팅 명령으로 입력한 경우의 문구도 같은 표현
check("경고 문구에 '중복'이 명시됨(소스 확인)", "중복 치료는 안 됩니다" in open(os.path.join(APP, "mafia_ui_ai.py"), encoding="utf-8").read())
try:
    root.destroy()
except Exception:
    pass
print("DOCTOR POPUP WARN", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
