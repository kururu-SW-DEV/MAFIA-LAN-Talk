# -*- coding: utf-8 -*-
"""test_phase_label_vote.py — 투표 창이 열리면 상단 안내가 '토론 중'에서 '개표 중'으로 넘어가는지(클라이언트·호스트)."""
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


tmp = os.path.join(BASE, "tmp_phaselbl"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60171, datadir=tmp), [])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("나", False), ("방장", False), ("철수", True), ("영희", True)):
    core.join(n, is_ai=a)
for n, r in (("나", "citizen"), ("방장", "citizen"), ("철수", "mafia"), ("영희", "doctor")):
    core.players[n]["role"] = r
core.phase = Phase.DAY; core.day_no = 1
app.mafia_active = True; app.mafia_host_mode = False; app._recruiter_host = "방장"; app.mafia_bar_is_game = True
app._my_mafia_role = "citizen"


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


def lbl():
    return app.mafia_phase_lbl.cget("text")


# ---- 클라이언트: 낮 토론 → vote_open ----
app._client_start_day_countdown(); pump(1.3)
check(f"낮에는 '토론 중' 카운트다운이 표시됨: {lbl()!r}", "토론 중" in lbl() and "남은" in lbl())
app._on_mafia_proto_msg(encode("vote_open"), "방장", None); pump(0.5)
check(f"vote_open을 받으면 상단 안내가 '개표 중'으로 넘어감: {lbl()!r}", "개표 중" in lbl() and "토론 중" not in lbl())
check("내 직업 표시는 그대로 붙어 있음", "내 직업" in lbl())
check("클라이언트의 낮 카운트다운 틱이 멈춤", getattr(app, "_day_tick", None) is None)
pump(2.5)
check(f"시간이 지나도 다시 '토론 중'으로 되돌아가지 않음: {lbl()!r}", "개표 중" in lbl() and "토론 중" not in lbl())
app.refresh_mafia_phase_label()
check("다른 경로에서 안내를 갱신해도 '개표 중'이 유지됨", "개표 중" in lbl())
check("헤더 부제와 하단 상태 표시줄도 같이 바뀜", "개표 중" in app.ch_sub.cget("text"))
check("투표 자체는 낮 상태에서 받을 수 있음(core.phase 유지)", core.phase == Phase.DAY)
app._mafia_overlay_close(); pump(0.2)

# ---- 새 낮이 시작되면 다시 '토론 중' ----
core.phase = Phase.NIGHT
app._on_mafia_proto_msg(encode("day", victim=None, role=None), "방장", None); pump(1.5)
check(f"다음 날이 시작되면 다시 '토론 중'으로 돌아옴: {lbl()!r}", "토론 중" in lbl() and "개표 중" not in lbl())
try:
    app._mafia_overlay_close()
except Exception:
    pass

# ---- 호스트: open_the_vote ----
app.mafia_host_mode = True
core.phase = Phase.DAY
app._mafia_broadcast = lambda *a, **k: None
app.start_day_timer(); pump(1.2)
check(f"호스트: 낮 토론 중 표시: {lbl()!r}", "토론 중" in lbl())
app.open_the_vote(); pump(0.5)
check(f"호스트: 투표 창이 열리면 '개표 중': {lbl()!r}", "개표 중" in lbl())
app._cancel_vote_popup(); pump(0.2)
try:
    root.destroy()
except Exception:
    pass
print("PHASE LABEL", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
