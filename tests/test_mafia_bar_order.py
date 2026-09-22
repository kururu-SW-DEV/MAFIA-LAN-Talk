# -*- coding: utf-8 -*-
"""test_mafia_bar_order.py — 임시 조사: 마피아 게임탭의 상태바/생존자 목록 순서,
그리고 강제 종료 버튼이 게임 시작 즉시(탭을 다시 안 눌러도) 나타나는지."""
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

ALL = True
def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)

tmp = os.path.join(BASE, "tmp_bar_order"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.deiconify(); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60091, datadir=tmp), [])

def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)

# ---- 1) 첫 입장(아직 게임 전이라 생존자 목록은 안 보임 — 순서를 따질 대상이 아님) ----
app._select(("mgame",)); root.update()
check("첫 입장: 상태바는 보이고 생존자 목록은 아직 안 보임(게임 전)",
      app.mafia_bar.winfo_ismapped() and not app.mafia_roster_lbl.winfo_ismapped())

# ---- 2) 방장으로 게임 시작(탭을 다시 누르지 않고, 시작 버튼만) ----
for n in range(6):
    app.mafia_start_clicked() if n == 0 else None
app.mafia_start_clicked()   # 1단계: 모집 시작
root.update()
app.mafia_start_clicked()   # 2단계: 게임 시작(인간 1명 + AI로 시작)
root.update(); pump(0.3)
check("게임 시작 직후(탭 재진입 없이) 강제 종료 버튼이 바로 보임",
      app.mafia_active and app.mafia_force_quit_btn.winfo_ismapped())
check("게임 시작 직후 생존자 목록도 바로 보임", app.mafia_roster_lbl.winfo_ismapped())
check("게임 시작 직후에도 순서 유지: 상태바가 생존자 목록보다 위",
      app.mafia_bar.winfo_y() < app.mafia_roster_lbl.winfo_y())

# ---- 3) 다른 방으로 나갔다가 다시 마피아 탭 재진입 ----
# app._select()의 "마피아방을 떠날 때" 분기(app.py)를 그대로 재현 — 실제 dm 렌더는
# 필요 없고, 이 정리 로직이 실행됐는지가 중요하다.
app.mafia_bar.pack_forget()
if getattr(app, "mafia_roster_lbl", None):
    app.mafia_roster_lbl.pack_forget()
app.mafia_bar_is_game = False
app.current = None
try:
    app._select_mafia_room(("mgame",))
except Exception as e:
    print("re-entry select error:", e)
root.update(); pump(0.2)
check("재진입 후에도 순서 유지: 상태바가 생존자 목록보다 위",
      app.mafia_bar.winfo_y() < app.mafia_roster_lbl.winfo_y())
check("재진입 후에도 강제 종료 버튼이 보임", app.mafia_force_quit_btn.winfo_ismapped())

try:
    app._cancel_mafia_timer()
except Exception:
    pass
root.destroy()
print("MAFIA BAR ORDER", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
