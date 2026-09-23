# -*- coding: utf-8 -*-
"""test_progress_disconnect.py — '투표 진행률 표시' + '접속 끊김 처리' 회귀 테스트.

실제 vLLM 백엔드 없이(네트워크 불필요) mafia_ai._llm_call을 즉답 스텁으로 대체해
mafia_ui.py의 실제 로직을 그대로 태운다. 두 번째 실제 Engine("이팀장B")를 인간
참가자로 게임에 합류시킨 뒤 일부러 죽여서(stop) 접속 끊김 감지를 검증한다.

검증 항목:
  A) 투표 팝업에 익명 진행률 라벨(N/M)이 뜨고, 표가 들어올 때마다 갱신된다.
  B) 실제 인간 참가자(이팀장B)가 죽으면(연결 끊김) — 역할을 밝히지 않는 방식으로
     "연결이 끊긴 것 같습니다" 안내가 뜬다.
  C) 그 인간이 다시 살아나면(presence 재개) — "다시 연결되었습니다" 안내가 뜬다.
"""
import argparse
import importlib.util
import os
import random
import re
import shutil
import socket
import sys
import time

if __name__ != "__main__":
    # crypto_layer.py가 ProcessPoolExecutor(멀티프로세싱)를 쓰므로, 자식 프로세스로
    # 재실행될 때 전체 로직이 또 도는 것을 막는다.
    sys.exit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(BASE)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP_ROOT, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)

_real_bind = socket.socket.bind


def _spy(self, addr):
    if isinstance(addr, tuple) and len(addr) == 2 and addr[0] in ("", "0.0.0.0"):
        _real_bind(self, ("127.0.0.1", addr[1]))
    else:
        _real_bind(self, addr)


socket.socket.bind = _spy
spec.loader.exec_module(lm)

import tkinter as tk
import mafia_ai
from mafia_core import Phase


def fake_llm_call(messages, max_tokens=350, timeout=45):
    sys_c = messages[0]["content"] if messages else ""
    user_c = messages[-1]["content"] if messages else ""
    m = re.search(r"참가자 '([^']+)'", sys_c)
    name = m.group(1) if m else "AI"
    if "준비완료" in user_c:
        return f"{name} 준비완료"
    if "투표" in user_c and ("생존 후보" in user_c):
        cm = re.search(r"생존 후보(?:\(당신 제외\))?:\s*([^\n]+)", user_c)
        pool = [c.strip() for c in (cm.group(1).split(",") if cm else []) if c.strip()]
        pick = random.choice(pool) if pool else name
        return f"투표 {pick}"
    return "음 좀 더 지켜볼게요 ㅋㅋ"


mafia_ai._llm_call = fake_llm_call

tmp = os.path.join(BASE, "tmp_progress_disc")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A"), exist_ok=True)
os.makedirs(os.path.join(tmp, "B"), exist_ok=True)
open(os.path.join(tmp, "A", "firewall_notice_done"), "w", encoding="utf-8").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w", encoding="utf-8").close()

A_PORT, B_PORT = 60021, 60022

B = lm.Engine("이팀장B", port=B_PORT, datadir=os.path.join(tmp, "B"),
              on_event=lambda e: None, instance_id="B" * 10)
B.set_static_targets({("127.0.0.1", A_PORT)})

root = tk.Tk()
root.withdraw()
args = argparse.Namespace(name="테스터", port=A_PORT, datadir=os.path.join(tmp, "A"))
app = lm.App(root, args, [])

ok_all = True


def check(label, cond):
    global ok_all
    result = bool(cond)
    print(("OK  " if result else "FAIL"), label)
    if not result:
        ok_all = False
    return result


def wait_gui(cond, timeout=20.0, interval=20):
    state = {"start": time.time(), "done": False}

    def _poll():
        try:
            if cond():
                state["done"] = True
                root.quit()
                return
        except tk.TclError:
            root.quit()
            return
        if time.time() - state["start"] > timeout:
            root.quit()
            return
        root.after(interval, _poll)

    root.after(0, _poll)
    root.mainloop()
    return state["done"]


def do(fn):
    root.after(0, fn)


sys_msgs = []
_orig_add_sys = app.add_mafia_system


def _spy_add_sys(text):
    sys_msgs.append(text)
    return _orig_add_sys(text)


app.add_mafia_system = _spy_add_sys
app._tally_and_reveal = lambda *a, **kw: None
app._silent_tally_if_pending = lambda *a, **kw: None

try:
    # ---- A가 B를 실제 LAN 피어로 발견 ----
    check("A가 B를 발견", wait_gui(lambda: len(app.engine.peers) >= 1, timeout=10))

    do(lambda: app._select(("mgame",)))
    check("마피아 방 선택됨", wait_gui(lambda: app.current == ("mgame",)))

    do(app.mafia_start_clicked)   # 1단계: 모집 시작(호스트 자동 참가)
    check("모집 상태 진입", wait_gui(lambda: getattr(app, "_recruiting", False) is True))

    # 실제 인간 'B'가 참가 신청한 상태를 흉내(실제 UDP 참가 프로토콜 왕복 대신 직접 등록)
    def _seed_b_join():
        if "이팀장B" not in app._recruited_humans:
            app._recruited_humans.append("이팀장B")
    do(_seed_b_join)
    check("B가 모집 명단에 등록됨", wait_gui(lambda: "이팀장B" in app._recruited_humans))

    do(app.mafia_start_clicked)   # 2단계: 모집 마감 + 게임 시작(인간 2 + AI 4)
    check("게임 활성화(mafia_active)", wait_gui(lambda: app.mafia_active))
    check("낮 1일차로 진입", wait_gui(lambda: app.core.phase == Phase.DAY))
    check("B가 실제 게임 참가자로 등록됨(is_ai=False)",
          wait_gui(lambda: "이팀장B" in app.core.players
                   and not app.core.players["이팀장B"].get("is_ai")))
    check("AI 4명 전원 부트 완료",
          wait_gui(lambda: len(app.ai.players) == 4 and all(p.booted for p in app.ai.players),
                   timeout=30))

    # 게임 시작 시 호스트 본인에게 뜨는 '당신의 역할' 팝업(100ms 지연 예약)이 아직 안
    # 떴다면 여기서 실제로 흘려보내고 닫아둔다 — 그래야 이후 투표 팝업을 열 때
    # 그 지연 타이머가 뒤늦게 튀어나와 투표 팝업을 덮어쓰는 일이 없다.
    wait_gui(lambda: False, timeout=0.5)
    try:
        app._mafia_overlay_close()
    except Exception:
        pass

    me = app.engine.name
    alive_others = [n for n in app.core.alive_players() if n != me]

    # =========================================================
    # 시나리오 A — 투표 진행률 표시
    # =========================================================
    do(app.open_the_vote)
    check("[A] 투표 팝업 오픈", wait_gui(lambda: getattr(app, "_vote_progress_lbl", None) is not None))
    total_voters = len(app.core.alive_players())
    check("[A] 초기 진행률 라벨이 0/총원으로 표시됨",
          wait_gui(lambda: app._vote_progress_lbl.cget("text") == f"🗳 투표 진행률 0/{total_voters}"))

    target_a = [n for n in alive_others if n != "이팀장B"][0] if len(alive_others) > 1 else alive_others[0]
    do(lambda: app._popup_vote(target_a))
    check("[A] 유저 투표 후 진행률 라벨이 즉시 증가", wait_gui(
        lambda: app._vote_progress_lbl is not None
        and app._vote_progress_lbl.cget("text") != f"🗳 투표 진행률 0/{total_voters}"))
    check("[A] 시스템 메시지에 '진행률 N/M' 문구가 포함됨(대상 노출 없이)",
          any(re.search(r"진행률 \d+/\d+", t) and "→" not in t for t in sys_msgs))

    # 이후 tally를 막아뒀으므로 core.votes에 계속 쌓이기만 함 — B는 아직 투표 안 한 채로 둔다
    # (아래 접속끊김 시나리오에서 그대로 쓰기 위해 core.phase는 DAY로 유지되도록 개표를 계속 억제)

    # =========================================================
    # 시나리오 B — 실제 인간(B) 접속 끊김 감지
    # =========================================================
    b_key = app._mafia_peer_of("이팀장B")
    check("[B] B의 (ip,port)를 정상적으로 찾음", b_key is not None)

    def _kill_b():
        try:
            B.stop()
        except Exception:
            pass
    do(_kill_b)

    check("[B] B 연결 끊김이 감지되어 시스템 메시지로 안내됨(역할 비노출)",
          wait_gui(lambda: any("이팀장B" in t and "끊긴 것 같습니다" in t for t in sys_msgs),
                   timeout=32))   # v1.94 — ack도 생존 신호라 마지막 응답이 조금 늦게 갱신된다
    leak = [t for t in sys_msgs if "이팀장B" in t and "끊긴 것 같습니다" in t
            and any(r in t for r in ("마피아", "의사", "경찰", "시민"))]
    check("[B] 끊김 안내 메시지가 역할 정보를 노출하지 않음", len(leak) == 0)

    # =========================================================
    # 시나리오 C — 재접속 감지(가짜 presence 갱신으로 시뮬레이션)
    # =========================================================
    def _revive_b():
        # v1.90 — 이 시점이면 B가 끊긴 지 이미 PEER_TIMEOUT(12초)을 넘긴 뒤라, A의
        # 백그라운드 정리(_prune, 3초 주기)가 그 사이 peers에서 항목 자체를 지워버렸을
        # 수 있다(실측 — b_key가 더는 app.engine.peers에 없는 채로 이 함수가 불림).
        # 그러면 아래 "if b_key in peers" 가드가 있는 예전 방식은 아무 일도 안 해서
        # 재접속 시뮬레이션 자체가 무산된다 — 지워졌으면 새로 만들어 넣는다.
        if not b_key:
            return
        with app.engine.plock:
            entry = app.engine.peers.get(b_key)
            if entry:
                entry["last"] = time.time()
            else:
                app.engine.peers[b_key] = {"name": "이팀장B", "ip": b_key[0], "port": b_key[1],
                                           "last": time.time(), "static": False}
    do(_revive_b)
    # v1.61 — 끊긴 사람은 사망 처리되므로, 그 사람이 마피아였다면 그 즉시 승패가
    # 갈려 게임이 끝나고(감시 루프도 종료) 재접속 안내가 안 나올 수 있다 — 역할이
    # 무작위라 둘 중 하나가 정상 결과다.
    check("[C] B 재접속이 감지되어 안내됨(또는 사망 처리로 게임이 정상 종료됨)",
          wait_gui(lambda: any(("이팀장B" in t and "다시 연결되었습니다" in t) or "게임 종료" in t
                               for t in sys_msgs), timeout=10))

finally:
    try:
        if app.engine:
            app.engine.stop()
    except Exception:
        pass
    try:
        B.stop()
    except Exception:
        pass
    try:
        if getattr(app, "_notifier", None) is not None:
            app._notifier.close()      # 트레이 아이콘 제거 — 안 하면 종료 후에도 죽은 아이콘이 남는다
    except Exception:
        pass
    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)

print("PROGRESS+DISCONNECT", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
