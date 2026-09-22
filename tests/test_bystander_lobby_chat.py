# -*- coding: utf-8 -*-
"""test_bystander_lobby_chat.py — 게임 시작 전/후 로비 채팅이 실제로 상대에게 가는지,
그리고 게임이 진행 중일 때는 참가 못 한 사람들끼리만 보이고 방장·참가자에게는 안 보이는지."""
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


tmp = os.path.join(BASE, "tmp_bystander_lobby"); shutil.rmtree(tmp, ignore_errors=True)
for sub in ("A", "B", "C"):
    os.makedirs(os.path.join(tmp, sub))
    open(os.path.join(tmp, sub, "firewall_notice_done"), "w").close()
A_PORT, B_PORT, C_PORT = 60081, 60082, 60083

root = tk.Tk(); root.geometry("1000x700"); root.withdraw()
rootB = tk.Toplevel(root); rootB.withdraw()
rootC = tk.Toplevel(root); rootC.withdraw()

app = lm.App(root, argparse.Namespace(name="방장", port=A_PORT, datadir=os.path.join(tmp, "A")),
             [f"127.0.0.1:{B_PORT}", f"127.0.0.1:{C_PORT}"])
app._select(("mgame",)); root.update()


class ClientStub(DialogsMixin, MafiaUIMixin):
    """B·C(구경꾼) 대역 — 실제 네트워크/프로토콜 처리는 진짜 코드를 그대로 타되, 무거운
    캔버스 채팅 렌더링만 없는 경량 스텁(다른 스텁 기반 테스트와 동일한 패턴)."""
    def _on_msg(self, ev):
        return RealApp._on_msg(self, ev)

    def _hide_empty(self):
        pass

    def _mafia_append_live(self, rec):
        pass


def make_stub(name, port, root_, targets):
    q = queue.SimpleQueue()
    eng = lm.Engine(name, port=port, datadir=os.path.join(tmp, name.replace("이팀장", "")[:1] or name),
                    on_event=q.put, instance_id=(name * 10)[:10])
    eng.set_static_targets(targets)
    stub = ClientStub()
    stub.root = root_; stub.engine = eng; stub.core = GameCore("mafia-room")
    stub.ai = mafia_ai.AIDirector(); stub.mafia_active = False; stub.mafia_host_mode = False
    stub.mafia_history = []; stub.current = None; stub._my_mafia_role = None
    stub._recruiting = False; stub._recruited_humans = []; stub._my_joined = False
    stub._mafia_overlay = None; stub.mafia_phase_lbl = tk.Label(root_)
    return q, eng, stub


# 실제 LAN에서는 다들 같은 포트를 써서 UDP 브로드캐스트로 서로를 직접 발견한다(완전
# 메쉬) — 이 테스트도 그걸 재현하려면 B·C가 A뿐 아니라 서로도 정적 대상으로 알아야 한다.
b_q, B_engine, stubB = make_stub("이팀장B", B_PORT, rootB, {("127.0.0.1", A_PORT), ("127.0.0.1", C_PORT)})
c_q, C_engine, stubC = make_stub("김대리C", C_PORT, rootC, {("127.0.0.1", A_PORT), ("127.0.0.1", B_PORT)})


def _drain(q, stub):
    while True:
        try:
            ev = q.get_nowait()
        except queue.Empty:
            break
        if ev.get("ev") == "msg":
            try:
                stub._on_msg(ev)
            except Exception as e:
                print(f"{stub.engine.name}._on_msg 예외:", repr(e))


def pump(cond, timeout=10.0):
    state = {"start": time.time(), "done": False}
    def _poll():
        for rt in (rootB, rootC):
            try: rt.update()
            except tk.TclError: pass
        _drain(b_q, stubB); _drain(c_q, stubC)
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
    check("A가 B·C를 발견", pump(lambda: len(app.engine.peers) >= 2, timeout=10))

    # ---- 1) 게임 시작 전: 로비 채팅이 실제로 상대에게 간다 ----
    app.current = app.mafia_room_key()
    app._mafia_handle_user_text("모집 전 로비 채팅 A->모두")
    check("게임 시작 전: B가 A의 로비 채팅을 받음",
          pump(lambda: any("모집 전 로비 채팅" in r.get("text", "") for r in stubB.mafia_history), timeout=10))
    check("게임 시작 전: C도 A의 로비 채팅을 받음",
          any("모집 전 로비 채팅" in r.get("text", "") for r in stubC.mafia_history))

    stubB.current = stubB.mafia_room_key()
    stubB._mafia_handle_user_text("B가 A에게 답함")
    check("게임 시작 전: A가 B의 로비 채팅을 받음",
          pump(lambda: any("B가 A에게 답함" in r.get("text", "") for r in app.mafia_history), timeout=10))

    # ---- 2) 게임 진행 중: B·C는 게임에 참가하지 못함(구경꾼) ----
    app.core.players.clear()
    for n, is_ai in (("방장", False), ("철수", True), ("영희", True), ("미나", True)):
        app.core.players[n] = {"is_ai": is_ai, "alive": True, "role": "citizen"}
    app.core.phase = Phase.DAY; app.core.day_no = 1
    app.mafia_active = True; app.mafia_host_mode = True; app._recruiting = False
    stubB.mafia_active = False; stubB._in_game = False   # start 수신 후 참가 못 한 클라이언트 상태 재현
    stubC.mafia_active = False; stubC._in_game = False
    a_hist_n = len(app.mafia_history)

    stubB.current = stubB.mafia_room_key()
    stubB._mafia_handle_user_text("게임 중 구경꾼 채팅 B->C")
    check("게임 중: C(다른 구경꾼)는 B의 로비 채팅을 받음",
          pump(lambda: any("게임 중 구경꾼 채팅" in r.get("text", "") for r in stubC.mafia_history), timeout=10))
    check("게임 중: 방장(참가자) 화면에는 구경꾼 채팅이 안 보임(설계대로)",
          not any("게임 중 구경꾼 채팅" in r.get("text", "") for r in app.mafia_history[a_hist_n:]))
finally:
    try:
        app._cancel_mafia_timer()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

print("BYSTANDER LOBBY CHAT", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
