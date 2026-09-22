# -*- coding: utf-8 -*-
"""test_force_quit.py — 방장의 [게임 강제 종료] 버튼: 확인 팝업을 거쳐야 하고,
확인하면 즉시 로비로 돌아가며 원격 참가자에게도 전달된다."""
import argparse, importlib.util, os, queue, shutil, socket, sys, time
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
from mafia_core import GameCore, Phase
from mafia_ui import MafiaUIMixin
from dialogs import DialogsMixin
from app import App as RealApp

ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_force_quit"); shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A")); os.makedirs(os.path.join(tmp, "B"))
open(os.path.join(tmp, "A", "firewall_notice_done"), "w").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w").close()
A_PORT, B_PORT = 60071, 60072

root = tk.Tk(); root.geometry("1000x700"); root.withdraw()
rootB = tk.Toplevel(root); rootB.withdraw()

app = lm.App(root, argparse.Namespace(name="방장", port=A_PORT, datadir=os.path.join(tmp, "A")),
             [f"127.0.0.1:{B_PORT}"])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("방장", False), ("이팀장B", False), ("철수", True), ("영희", True), ("미나", True)):
    core.join(n, is_ai=a)
core.phase = Phase.DAY; core.day_no = 1
app.mafia_active = True
app.mafia_host_mode = True
app._recruiting = False


class ClientStub(DialogsMixin, MafiaUIMixin):
    def _on_msg(self, ev):
        return RealApp._on_msg(self, ev)


b_q = queue.SimpleQueue()
B_engine = lm.Engine("이팀장B", port=B_PORT, datadir=os.path.join(tmp, "B"), on_event=b_q.put, instance_id="B" * 10)
B_engine.set_static_targets({("127.0.0.1", A_PORT)})
stubB = ClientStub()
stubB.root = rootB; stubB.engine = B_engine; stubB.core = GameCore("mafia-room")
stubB.ai = mafia_ai.AIDirector(); stubB.mafia_active = True; stubB.mafia_host_mode = False
stubB.mafia_history = []; stubB.current = None; stubB._my_mafia_role = None
stubB._recruiting = False; stubB._recruited_humans = []; stubB._my_joined = True
stubB._mafia_overlay = None; stubB._in_game = True; stubB._recruiter_host = "방장"
stubB.mafia_phase_lbl = tk.Label(rootB)
for n, a in (("방장", False), ("이팀장B", False), ("철수", True)):
    stubB.core.join(n, is_ai=a)
stubB.core.phase = Phase.DAY


def _drain_b():
    while True:
        try:
            ev = b_q.get_nowait()
        except queue.Empty:
            break
        if ev.get("ev") == "msg":
            try:
                stubB._on_msg(ev)
            except Exception as e:
                print("stubB._on_msg 예외:", repr(e))


def pump(cond, timeout=10.0):
    state = {"start": time.time(), "done": False}
    def _poll():
        try: rootB.update()
        except tk.TclError: pass
        _drain_b()
        try:
            if cond():
                state["done"] = True; root.quit(); return
        except tk.TclError:
            root.quit(); return
        if time.time() - state["start"] > timeout:
            root.quit(); return
        root.after(15, _poll)
    root.after(0, _poll)
    root.mainloop()
    return state["done"]


try:
    check("A가 B를 발견", pump(lambda: len(app.engine.peers) >= 1, timeout=10))
    # v1.89 — "발견"은 정적 대상 등록만으로도 바로 참이 될 수 있다(실제 presence 패킷
    # 교환 전). 방송 범위 제한(v1.88 #7)은 core.players의 이름을 eng.peers의 이름/별칭과
    # 맞춰보므로, 실제로 B의 표시 이름이 A에 도착할 때까지(첫 presence 왕복, 최대 3초
    # 주기) 기다려야 broadcast가 B에게 실제로 나간다.
    check("A가 B의 실제 표시 이름을 앎(presence 왕복 완료)",
          pump(lambda: app._mafia_peer_of("이팀장B") is not None, timeout=10))

    # ---- 1) 버튼은 방장에게만, 게임 중에만 보인다 ----
    app._select_mafia_room(("mgame",)); root.update()
    check("게임 진행 중 + 방장 → 강제 종료 버튼이 보임",
          app.mafia_force_quit_btn.winfo_ismapped())
    app.mafia_active = False
    app._select_mafia_room(("mgame",)); root.update()
    check("게임 진행 중이 아니면 버튼이 숨음", not app.mafia_force_quit_btn.winfo_ismapped())
    app.mafia_active = True
    app._select_mafia_room(("mgame",)); root.update()

    # ---- 2) 취소하면 아무 일도 없다 ----
    app._embed_confirm = lambda *a, **k: False
    n_hist = len(app.mafia_history)
    app.mafia_force_quit_clicked()
    check("취소하면 게임이 그대로 진행 중", app.mafia_active is True)
    check("취소하면 시스템 메시지도 안 남음", len(app.mafia_history) == n_hist)

    # ---- 3) 확인하면 즉시 로비로, 원격에게도 전달 ----
    app._embed_confirm = lambda *a, **k: True
    app.mafia_force_quit_clicked()
    check("확인하면 mafia_active가 꺼짐", app.mafia_active is False)
    check("확인하면 방장 신분도 내려놓음(다음 판 클라이언트 전환 대비)", app.mafia_host_mode is False)
    check("강제 종료 시스템 메시지가 남음",
          any("강제로 종료" in r.get("text", "") for r in app.mafia_history))
    check("승패 문구는 남지 않음(승부가 난 게 아니므로)",
          not any("팀 승리" in r.get("text", "") for r in app.mafia_history))
    check("버튼이 다시 숨음", not app.mafia_force_quit_btn.winfo_ismapped())

    check("원격 참가자(B)도 로비로 돌아감",
          pump(lambda: stubB.mafia_active is False and stubB.core.phase == Phase.LOBBY, timeout=10))
    check("원격 참가자 화면에도 강제 종료 안내가 뜸",
          any("방장이 게임을 강제로 종료" in r.get("text", "") for r in stubB.mafia_history))

    # ---- 4) 방장이 아니면(=참가자·구경꾼) 버튼 자체가 없다 ----
    app.mafia_active = True
    app.mafia_host_mode = False
    app._select_mafia_room(("mgame",)); root.update()
    check("방장이 아니면 게임 중이어도 버튼이 안 보임", not app.mafia_force_quit_btn.winfo_ismapped())
    app.mafia_host_mode = True
finally:
    try:
        app._cancel_mafia_timer()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

print("FORCE QUIT", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
