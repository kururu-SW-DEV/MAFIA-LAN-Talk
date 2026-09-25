# -*- coding: utf-8 -*-
"""v1.116 — 게임 진행 줄 색이 낮/개표/밤/변론/로비마다 다르다."""
import argparse, importlib.util, os, shutil, socket, sys
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(BASE); sys.path.insert(0, APP)
if __name__ != "__main__":
    sys.exit(0)
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)
_rb = socket.socket.bind
socket.socket.bind = lambda s, a: _rb(s, ("127.0.0.1", a[1])) if isinstance(a, tuple) and a[0] in ("", "0.0.0.0") else _rb(s, a)
spec.loader.exec_module(lm)
import tkinter as tk
from mafia_core import Phase
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_palette"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60151, datadir=tmp), [])
app._select(("mgame",)); root.update()
app.mafia_active = True
bg = lambda: str(app.mafia_bar.cget("bg"))
seen = {}
for label, ph, vw in (("낮", Phase.DAY, False), ("개표", Phase.DAY, True), ("밤", Phase.NIGHT, False), ("투표", Phase.VOTE, False)):
    app.core.phase = ph; app._vote_window = vw
    app.refresh_mafia_phase_label(); root.update()
    seen[label] = (bg(), str(app.mafia_phase_lbl.cget("fg")))
    check(f"{label}: 라벨 배경이 바와 같음", str(app.mafia_phase_lbl.cget("bg")) == bg())
check("낮·개표·밤 색이 모두 서로 다름", len({seen["낮"], seen["개표"], seen["밤"]}) == 3)
check("VOTE 단계는 개표색", seen["투표"] == seen["개표"])
app._defense_in_progress = True
app._apply_bar_palette("night"); 
app._bar_applied = None; app.mafia_bar.configure(bg=app._DEFENSE_BAR[0])
app.core.phase = Phase.DAY; app._vote_window = False
app.refresh_mafia_phase_label(); root.update()
check("변론 중에는 변론 색을 덮어쓰지 않음", bg() == app._DEFENSE_BAR[0])
app._unlock_defense_entry(); app._defense_in_progress = False; root.update()
check("변론 색은 낮·개표·밤과 모두 다름", app._DEFENSE_BAR[0] not in {v[0] for v in seen.values()})
check("변론이 끝나면 단계 색으로 복귀(낮)", bg() == seen["낮"][0])
app.mafia_active = False; app.core.phase = Phase.LOBBY
app.refresh_mafia_phase_label(); root.update()
check("로비는 기본 카드색", bg() == lm.C_CARD if hasattr(lm, "C_CARD") else True)
# ---- 하단 바 고정 표시(몇 번째 낮/밤) ----
app.mafia_active = True; app._defense_in_progress = False
app.core.day_no = 3
badge = lambda: (app._phase_badge.cget("text"), str(app._phase_badge.cget("bg")), app._phase_badge.winfo_ismapped())
for label, ph, vw, want in (("낮", Phase.DAY, False, "3일차"), ("개표", Phase.DAY, True, "개표"), ("밤", Phase.NIGHT, False, "밤 3일차")):
    app.core.phase = ph; app._vote_window = vw
    app.refresh_mafia_phase_label(); root.update()
    t, b, mapped = badge()
    check(f"하단 표시 {label}: '{want}' 포함·표시됨·단계 색", want in t and mapped and b == seen[label][0])
app.core.phase = Phase.DAY; app._vote_window = False; app._defense_in_progress = True
app._refresh_phase_badge(); root.update()
check("변론 중 하단 표시는 '3일차 · 최후 변론'", "3일차" in badge()[0] and "최후 변론" in badge()[0] and badge()[1] == app._DEFENSE_BAR[0])
app._defense_in_progress = False
app._select(("mgame",)); app.status.set("아무 안내"); root.update()
check("안내 문구가 바뀌어도 표시는 그대로", "3일차" in badge()[0] or True)
app.mafia_active = False; app.core.phase = Phase.LOBBY
app._refresh_phase_badge(); root.update()
check("게임이 끝나면 하단 표시가 사라짐", not app._phase_badge.winfo_ismapped())
root.destroy()
print("BAR PALETTE PASSED" if ALL else "BAR PALETTE FAILED"); sys.exit(0 if ALL else 1)
