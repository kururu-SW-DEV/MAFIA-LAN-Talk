# -*- coding: utf-8 -*-
"""test_vote_flow.py — 마피아 게임 '투표 익명성 + 유저 우선 투표' 회귀 테스트.

실제 vLLM 백엔드 없이(네트워크 불필요) mafia_ai._llm_call을 즉답 스텁으로
바꿔서, 개표 팝업이 뜬 뒤의 실제 투표 로직(mafia_ui.py)을 그대로 태운다.

검증 항목:
  A) 유저가 즉시 투표하면 — AI 투표가 유저보다 먼저 core.votes에 반영되지
     않는다(동시/이후만 허용).
  B) 유저가 투표하지 않고 가만히 있으면 — AI 투표는 팝업이 열리고 약 5초
     유예시간이 지나기 전까지는 반영되지 않는다(무한 대기는 아님).
  C) 투표 관련 시스템 메시지 어디에도 '누가 누구에게' 투표했는지(화살표 →
     + 대상 이름)가 노출되지 않는다 — 익명 개표.
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
    # crypto_layer.py가 ProcessPoolExecutor(멀티프로세싱)를 쓰므로, 이 스크립트가
    # 자식 프로세스로 재실행될 때 아래 전체 로직이 다시 도는 것을 막는다.
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

# ---------------------------------------------------------------
# LLM 스텁 — 네트워크 호출 없이 즉시 응답(부트/투표/잡담 패턴 인식)
# ---------------------------------------------------------------
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

tmp = os.path.join(BASE, "tmp_vote_flow")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
open(os.path.join(tmp, "firewall_notice_done"), "w", encoding="utf-8").close()

root = tk.Tk()
root.withdraw()
args = argparse.Namespace(name="테스터", port=60011, datadir=tmp)
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
    """root.mainloop()/quit() 기반 대기 — 대기 중에도 Tk가 계속 '메인루프 안'에
    있으므로, 백그라운드 스레드(AI 부트/투표)가 거는 root.after() 콜백이
    'main thread is not in main loop' 없이 항상 안전하게 등록된다."""
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


def pump_for(seconds, interval=20):
    """조건 없이 지정 시간만큼 Tk 메인루프를 계속 돌린다(안전한 sleep 대용)."""
    return wait_gui(lambda: False, timeout=seconds, interval=interval)


# 시스템 메시지 수집(익명성 검증용)
sys_msgs = []
_orig_add_sys = app.add_mafia_system


def _spy_add_sys(text, local=False):
    sys_msgs.append(text)
    return _orig_add_sys(text, local=local)


app.add_mafia_system = _spy_add_sys

# core.cast_vote 호출 순서/시각 기록(투표 순서 검증용)
vote_log = []  # (voter, target, ts)
_orig_cast_vote = app.core.cast_vote


def _spy_cast_vote(voter, target):
    ok = _orig_cast_vote(voter, target)
    if ok:
        vote_log.append((voter, target, time.time()))
    return ok


app.core.cast_vote = _spy_cast_vote

# 개표(tally)로 넘어가면 이후 단계(변론/재투표/밤)까지 줄줄이 태워야 하므로,
# 이 테스트에서는 투표 '접수' 메커니즘만 검증하고 실제 개표 진행은 막아둔다.
app._tally_and_reveal = lambda *a, **kw: None
app._silent_tally_if_pending = lambda *a, **kw: None

def do(fn):
    """fn을 root.after(0, ...)로 예약만 해둔다 — 실제 실행은 바로 뒤따르는
    wait_gui()가 mainloop을 도는 동안 이뤄지므로, 백그라운드 스레드가 거는
    root.after() 콜백과 경쟁해도 'mainloop 밖' 구간이 생기지 않는다."""
    root.after(0, fn)


try:
    # ---- 로비: 방 선택 -> 모집 시작 -> 게임 시작(인간 1 + AI 4) ----
    do(lambda: app._select(("mgame",)))
    check("마피아 방 선택됨", wait_gui(lambda: app.current == ("mgame",)))

    do(app.mafia_start_clicked)   # 1단계: 모집 시작
    check("모집 상태 진입", wait_gui(lambda: getattr(app, "_recruiting", False) is True))

    do(app.mafia_start_clicked)   # 2단계: 모집 마감 + 게임 시작(백그라운드 부트 스레드 기동)
    check("게임 활성화(mafia_active)", wait_gui(lambda: app.mafia_active))
    check("낮 1일차로 진입", wait_gui(lambda: app.core.phase == Phase.DAY))

    check("AI 4명 전원 부트 완료",
          wait_gui(lambda: len(app.ai.players) == 4 and all(p.booted for p in app.ai.players),
                   timeout=30))

    me = app.engine.name
    alive_others = [n for n in app.core.alive_players() if n != me]
    check("생존 AI 후보 존재", len(alive_others) >= 3)

    # =========================================================
    # 시나리오 A — 유저가 팝업이 열리자마자 즉시 투표
    # =========================================================
    do(app.open_the_vote)
    check("[A] 투표 팝업 오픈",
          wait_gui(lambda: getattr(app, "_vote_popup_open_ts", 0) > 0))

    target_a = alive_others[0]
    user_vote_ts = {"t": None}

    def _cast_user_vote_a():
        user_vote_ts["t"] = time.time()
        app._popup_vote(target_a)   # 유저 즉시 클릭 시뮬레이션

    do(_cast_user_vote_a)
    check("[A] 유저 표가 core.votes에 즉시 반영됨", wait_gui(lambda: me in app.core.votes))

    # AI 전원이 투표를 마칠 때까지 대기(익명 개표이므로 최종 tally는 막아둔 채 votes만 관찰)
    check("[A] AI 전원 투표 반영",
          wait_gui(lambda: all(n in app.core.votes or n in app.core.abstains for n in alive_others),
                   timeout=15))

    ai_vote_times = [ts for (voter, tgt, ts) in vote_log if voter != me]
    check("[A] AI 투표가 유저 투표보다 먼저 반영된 경우 없음(유저 우선)",
          bool(ai_vote_times) and all(ts >= user_vote_ts["t"] for ts in ai_vote_times))

    # =========================================================
    # 시나리오 B — 유저가 투표하지 않고 방치(유예시간 5초 확인)
    # =========================================================
    def _reset_for_b():
        app.core.votes.clear()
        app.core.abstains.clear()
        vote_log.clear()
        try:
            app._mafia_overlay_close()
        except Exception:
            pass
        app._show_vote_popup()

    do(_reset_for_b)
    check("[B] 재오픈 시각 기록됨", wait_gui(lambda: getattr(app, "_vote_popup_open_ts", 0) > 0))
    popup_open_ts = app._vote_popup_open_ts

    # 유예시간(5초)이 지나기 전에는 AI 표가 반영되지 않아야 한다
    pump_for(3.0)
    early_votes = [v for v in vote_log if v[0] != me]
    check("[B] 유예시간(5초) 이전에는 AI 투표 미반영(유저 방치 3초 시점)",
          len(early_votes) == 0)

    # 유예시간이 지나면 AI들이 알아서 투표를 진행해 게임이 멈추지 않아야 한다
    check("[B] 유예시간 경과 후 AI 전원 투표 반영(게임 정지 방지)",
          wait_gui(lambda: all(n in app.core.votes or n in app.core.abstains for n in alive_others),
                   timeout=15))
    late_votes = [ts for (voter, tgt, ts) in vote_log if voter != me]
    # 4.0초 — 게이트는 5.0초지만 폴링 간격(0.4초)만큼의 오차는 허용(간헐적 타이밍 플레이키 방지)
    check("[B] 모든 AI 투표가 팝업 오픈 후 실제로 유예시간만큼 지연됨(>=4.0초)",
          bool(late_votes) and all((ts - popup_open_ts) >= 4.0 for ts in late_votes))

    # =========================================================
    # 시나리오 C — 투표 관련 시스템 메시지 익명성(대상 비노출) 검증
    # =========================================================
    leak_pattern = re.compile(r"🗳.*→")
    leaks = [t for t in sys_msgs if leak_pattern.search(t)]
    check("[C] 투표/재투표 시스템 메시지에 '→ 대상' 형태의 노출 없음(익명 개표)",
          len(leaks) == 0)
    if leaks:
        print("  누출된 메시지:", leaks)

finally:
    try:
        if app.engine:
            app.engine.stop()
    except Exception:
        pass
    try:
        if getattr(app, "_notifier", None) is not None:
            app._notifier.close()      # 트레이 아이콘 제거 — 안 하면 종료 후에도 죽은 아이콘이 남는다
    except Exception:
        pass
    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)

print("VOTE FLOW", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
