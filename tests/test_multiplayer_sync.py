# -*- coding: utf-8 -*-
"""test_multiplayer_sync.py — "복수 인간(2명 이상) + AI" 플레이 회귀 테스트.

전수 검토 보고서(2026-09-19)가 지적한 항목들을, 실제 호스트(A, 완전한 App+GUI)와
실제 원격 참가자(B)가 진짜 localhost UDP로 [MAFIA1] 패킷을 주고받으며 검증한다.

B는 완전한 App(아바타 원형 이미지 등 GUI 전체)을 새로 만드는 대신, 같은
프로세스 안에서 두 번째 App을 만들면 위젯 캐시 일부가 Tcl 인터프리터 경계를
넘어가며 깨지는 문제가 있어(app.py/canvas_utils.py의 아바타 원형 이미지
모듈 전역 캐시가 첫 App의 Tk 인터프리터에 묶인 PhotoImage를 두 번째 App의
다른 인터프리터에 재사용하려다 "image ... doesn't exist"로 죽음 — 실측),
MafiaUIMixin/DialogsMixin만 가진 경량 스텁으로 대신한다. 실제 네트워크
송수신·프로토콜 디스패치(_on_mafia_proto_msg)·core 반영은 전부 진짜 코드
그대로 태운다 — 가짜인 건 아바타 등 B의 나머지 채팅 UI뿐이다.

검증 항목:
  ① 게임 시작 후 사람 발언이 서로에게 실제로 전달된다(user_say).
  ② 원격 참가자(B)의 core.players가 "start" 수신 후 실제로 채워진다.
  ③ B가 낮 투표 팝업에서 고른 표가 A(호스트)의 core에 실제로 반영된다(vote_cast).
  ④ B가 밤 행동(마피아 살해)을 고르면 A의 core에 실제로 반영된다(night_action).
  ⑤ B가 찬반(처형여부) 투표를 하면 A의 core에 실제로 반영된다(defense_vote_cast).
  ⑥ B의 접속이 끊기면 A가 자동으로 사망 처리하고, 그 사실이 방송된다(death).
"""
import argparse
import importlib.util
import os
import queue
import random
import re
import shutil
import socket
import sys
import time

if __name__ != "__main__":
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
from mafia_core import GameCore, Phase
from mafia_ui import MafiaUIMixin
from dialogs import DialogsMixin
from app import App as RealApp


NIGHT_PROMPTS = []            # 밤 행동 프롬프트 수집(실제로 LLM 경로를 탔는지 확인용)


def fake_llm_call(messages, max_tokens=350, timeout=45):
    sys_c = messages[0]["content"] if messages else ""
    user_c = messages[-1]["content"] if messages else ""
    m = re.search(r"참가자 '([^']+)'", sys_c)
    name = m.group(1) if m else "AI"
    if "[밤 행동" in user_c:
        NIGHT_PROMPTS.append(user_c)
        cd = re.search(r"후보(?:\(시민 쪽 생존자\))?:\s*([^.\n]+)\.", user_c)
        pool = [c.strip() for c in (cd.group(1).split(",") if cd else []) if c.strip()]
        return "선택 " + (sorted(pool)[-1] if pool else "")
    if "준비완료" in user_c:
        return f"{name} 준비완료"
    if "투표" in user_c and "생존 후보" in user_c:
        cm = re.search(r"생존 후보(?:\(당신 제외\))?:\s*([^\n]+)", user_c)
        pool = [c.strip() for c in (cm.group(1).split(",") if cm else []) if c.strip()]
        pick = random.choice(pool) if pool else name
        return f"투표 {pick}"
    if "마피아 비밀 채팅" in user_c:
        # 공개 채팅 유출 검사용 고유 문구. 확정 지시가 있으면 그 이름으로 동의, 아니면 후보 중 하나 제안.
        cf = re.search(r"'([^']+)'을\(를\) 오늘 밤 목표로 정했습니다", user_c)
        if cf:
            return f"비밀응답 좋아 {cf.group(1)}로 가자"
        cd = re.search(r"살해 후보\(시민 쪽 생존자\):\s*([^.\n]+)\.", user_c)
        pool = [c.strip() for c in (cd.group(1).split(",") if cd else []) if c.strip() and c.strip() != "없음"]
        return f"비밀응답 {random.choice(pool)} 어때?" if pool else "비밀응답 ㅋㅋ"
    return "음 좀 더 지켜볼게요 ㅋㅋ"


mafia_ai._llm_call = fake_llm_call

tmp = os.path.join(BASE, "tmp_multiplayer_sync")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(os.path.join(tmp, "A"), exist_ok=True)
os.makedirs(os.path.join(tmp, "B"), exist_ok=True)
open(os.path.join(tmp, "A", "firewall_notice_done"), "w", encoding="utf-8").close()
open(os.path.join(tmp, "B", "firewall_notice_done"), "w", encoding="utf-8").close()

A_PORT, B_PORT = 60031, 60032

rootA = tk.Tk(); rootA.withdraw()
# v1.61 — 두 번째 tk.Tk()를 따로 만들면 ImageTk.PhotoImage가 master를 안 줬을 때
# tkinter._default_root(마지막에 만든 Tk가 됨)에 잘못 묶여, A쪽 위젯에 쓰는
# 이미지가 B의 인터프리터에 만들어지는 등 뒤섞여 "image ... doesn't exist"로
# 깨진다(실측). Toplevel(rootA)로 만들면 인터프리터가 하나뿐이라 이 문제 자체가
# 없다 — B는 완전한 App이 아니라 경량 스텁이라 이렇게 해도 실제 검증 내용은
# 그대로다.
rootB = tk.Toplevel(rootA); rootB.withdraw()

argsA = argparse.Namespace(name="김재무A", port=A_PORT, datadir=os.path.join(tmp, "A"))
appA = lm.App(rootA, argsA, [f"127.0.0.1:{B_PORT}"])


class ClientStub(DialogsMixin, MafiaUIMixin):
    """B(원격 참가자) 대역 — 실제 네트워크/프로토콜 처리 코드는 App과 동일하게
    타되, 아바타 등 무거운 채팅 UI만 없는 경량 스텁."""

    def _on_msg(self, ev):
        return RealApp._on_msg(self, ev)


b_q = queue.SimpleQueue()
B_engine = lm.Engine("이팀장B", port=B_PORT, datadir=os.path.join(tmp, "B"),
                     on_event=b_q.put, instance_id="B" * 10)
B_engine.set_static_targets({("127.0.0.1", A_PORT)})

stubB = ClientStub()
stubB.root = rootB
stubB.engine = B_engine
stubB.core = GameCore("mafia-room")
# 실제 클라이언트도 mafia_ready()에서 AIDirector()를 만들어 두지만(host만
# spawn_all/assign_roles를 호출해 실제 플레이어를 채운다) 클라이언트 쪽은
# 항상 비어 있다 — 실제 상태를 그대로 재현(None으로 두면 _trigger_ai_reactions
# 류가 클라이언트 자기 자신에게서도 호출될 때 AttributeError로 죽어서 실제
# 앱과 다른 동작이 됨).
stubB.ai = mafia_ai.AIDirector()
stubB.mafia_active = False
stubB.mafia_history = []
stubB.current = None
stubB._my_mafia_role = None
stubB._recruiting = False
stubB._recruited_humans = []
stubB._my_joined = False
stubB._mafia_overlay = None
stubB.mafia_phase_lbl = tk.Label(rootB)  # "recruit_start" 핸들러가 무조건 건드림


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


ok_all = True


def check(label, cond):
    global ok_all
    result = bool(cond)
    print(("OK  " if result else "FAIL"), label)
    if not result:
        ok_all = False
    return result


def pump(cond, timeout=20.0):
    """v1.61 — 단순 root.update() 반복이 아니라 root.mainloop()+quit()로 돌린다.
    호스트(A)는 AI 부트스트랩을 백그라운드 스레드에서 진행하며 거기서
    root.after(...)로 메인스레드에 콜백을 등록하는데, Tk는 mainloop에 한 번도
    안 들어간 인터프리터에 대해 이 크로스스레드 after 등록을 거부한다
    ("main thread is not in main loop") — 실측. mainloop 안에서 매 tick마다
    B쪽(rootB)도 같이 펌프한다."""
    state = {"start": time.time(), "done": False}

    def _poll():
        try:
            rootB.update()
        except tk.TclError:
            pass
        _drain_b()
        try:
            if cond():
                state["done"] = True
                rootA.quit()
                return
        except tk.TclError:
            rootA.quit()
            return
        if time.time() - state["start"] > timeout:
            rootA.quit()
            return
        rootA.after(15, _poll)

    rootA.after(0, _poll)
    rootA.mainloop()
    return state["done"]


A_sys = []
_a_orig_sys = appA.add_mafia_system


def _spyA(t):
    A_sys.append(t); return _a_orig_sys(t)


appA.add_mafia_system = _spyA

try:
    check("A가 B를 발견", pump(lambda: len(appA.engine.peers) >= 1, timeout=10))
    check("B가 A를 발견", pump(lambda: len(stubB.engine.peers) >= 1, timeout=10))

    appA._select(("mgame",))

    # ---- 1단계: A가 모집 시작 → B가 실제 네트워크로 수신하고 참가 신청 ----
    appA.mafia_start_clicked()
    check("B가 모집 개시를 실제로 수신함", pump(lambda: stubB._recruiting is True))

    stubB.mafia_toggle_join_clicked()
    check("A가 B의 참가 신청을 실제로 수신함", pump(lambda: "이팀장B" in appA._recruited_humans))
    check("B 자신도 명단 갱신을 수신함", pump(lambda: "이팀장B" in stubB._recruited_humans))

    # ---- 2단계: 모집 마감 + 게임 시작(인간 2 + AI 3) ----
    appA.mafia_ai_count = 3
    appA.mafia_start_clicked()
    check("A: 게임 활성화", pump(lambda: appA.mafia_active))
    check("A: 낮 1일차 진입", pump(lambda: appA.core.phase == Phase.DAY))

    # ===================== ② 원격 참가자 명단/역할 동기화 =====================
    check("② B의 core.players가 start 수신 후 실제로 채워짐",
          pump(lambda: "이팀장B" in stubB.core.players and len(stubB.core.players) >= 2, timeout=15))
    check("② B의 core.phase도 DAY로 동기화됨", pump(lambda: stubB.core.phase == Phase.DAY))
    check("② B 자신도 core.players 안에서 is_ai=False로 정확히 표시됨",
          not stubB.core.players.get("이팀장B", {}).get("is_ai", True))
    check("② B가 자기 자신의 역할을 배정받음(비밀 유지, 본인만)",
          pump(lambda: stubB.core.players.get("이팀장B", {}).get("role") is not None, timeout=10))

    ai_names = [n for n, p in appA.core.players.items() if p.get("is_ai")]
    check("AI 3명 명단 확보", len(ai_names) == 3)

    pump(lambda: False, timeout=0.3)
    try:
        appA._mafia_overlay_close()
    except Exception:
        pass
    try:
        stubB._mafia_overlay_close()
    except Exception:
        pass

    # ===================== ① 게임 시작 후 사람 발언 상호 전달 =====================
    appA._mafia_handle_user_text("안녕하세요 A입니다")
    check("① B가 A의 낮 발언을 실제로 수신함",
          pump(lambda: any("안녕하세요 A입니다" in r.get("text", "") for r in stubB.mafia_history), timeout=10))

    stubB._mafia_handle_user_text("반갑습니다 B입니다")
    check("① A가 B의 낮 발언을 실제로 수신함",
          pump(lambda: any("반갑습니다 B입니다" in r.get("text", "") for r in appA.mafia_history), timeout=10))

    # ===================== ③ 낮 투표 동기화(클라이언트 → 호스트) =====================
    appA._tally_and_reveal = lambda *a, **kw: None
    appA._silent_tally_if_pending = lambda *a, **kw: None
    appA._schedule_tally = lambda *a, **kw: None

    vote_target = ai_names[0]
    stubB._popup_vote(vote_target)
    check("③ A(호스트)의 core.votes에 B의 실제 선택이 반영됨",
          pump(lambda: appA.core.votes.get("이팀장B") == vote_target, timeout=10))
    check("③ B 자신의 로컬 화면에도 투표 완료로 표시됨",
          stubB.core.votes.get("이팀장B") == vote_target)

    # ===================== 투표 개시 동기화(vote_open) =====================
    appA.open_the_vote()
    check("vote_open: 원격(B) 화면에도 투표 팝업이 실제로 열림",
          pump(lambda: getattr(stubB, "_vote_lbl", None) is not None, timeout=10))
    stubB._cancel_vote_popup()
    try:
        appA._cancel_vote_popup()
    except Exception:
        pass

    # ===================== 동률 재투표 동기화(revote_open) =====================
    appA._tally_full = lambda *a, **kw: None   # 실제 2차 개표(최후변론 진행 등)는 이 테스트 범위 밖
    tied = [ai_names[0], ai_names[1]]
    appA._open_revote_popup(tied)
    check("revote_open: 원격(B) 화면에도 재투표 팝업이 실제로 열림",
          pump(lambda: stubB._mafia_overlay is not None and stubB._revote_tied == tied, timeout=10))
    stubB._cast_revote(tied[0])
    check("revote: 원격(B)의 재투표 선택이 호스트(A)에 실제로 반영됨",
          pump(lambda: appA.core.votes.get("이팀장B") == tied[0], timeout=10))
    try:
        appA._mafia_overlay_close()
    except Exception:
        pass
    dl = getattr(appA, "_revote_deadline", None)
    if dl:
        appA.root.after_cancel(dl)
        appA._revote_deadline = None
    appA._revote_tied = None
    appA.core.votes.clear()
    appA.core.abstains.clear()

    # ===================== ④ 밤 행동 동기화(클라이언트 → 호스트) =====================
    # 실제 무작위 역할 배정을 기다리지 않고 시나리오 검증을 위해 마피아로
    # 강제 지정한다(호스트의 core를 직접 바꾸는 것은 테스트 셋업 전용 단축이지,
    # 실제 플레이 경로가 아니다).
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
    # 역할은 무작위 배정이라 ai_names[1]이 마피아 동료일 수 있다(그러면 호스트가
    # 정당하게 거절) — 검증 대상은 '전달·반영'이므로 확실히 비마피아인 대상을 고른다.
    victim = next(n for n, p in appA.core.players.items()
                  if n != "이팀장B" and p.get("role") != "mafia")
    with appA.core.lock:
        appA.core.phase = Phase.NIGHT
    stubB.core.players["이팀장B"]["role"] = "mafia"
    stubB.core.phase = Phase.NIGHT
    stubB._my_mafia_role = "mafia"

    stubB._apply_night_pick(victim, "mafia")
    check("④ A(호스트)의 core.night_target에 B가 보낸 밤 행동이 실제로 반영됨",
          pump(lambda: appA.core.night_target == victim, timeout=10))

    # ===================== 마피아 공모 조율(호스트) =====================
    a_nm = "김재무A"
    with appA.core.lock:
        for n, p_ in appA.core.players.items():
            p_["role"] = "citizen"
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.players[ai_names[0]]["role"] = "mafia"
        appA.core.phase = Phase.NIGHT
        appA.core.night_targets.clear()
        appA.core.night_targets[ai_names[0]] = ai_names[2]      # AI는 먼저, 다른 대상
        appA.core.night_targets["이팀장B"] = ai_names[1]        # 사람 선택이 팀 결정
    appA._reconcile_mafia_night()
    check("공모: 사람 마피아 선택으로 AI 마피아가 통일되어 합의(살해 인정)됨",
          appA.core.night_kill_agree() == ai_names[1])
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "citizen"
        appA.core.players[ai_names[1]]["role"] = "mafia"
        appA.core.night_targets.clear()
        appA.core.night_targets[ai_names[0]] = a_nm
        appA.core.night_targets[ai_names[1]] = ai_names[2]
    appA._reconcile_mafia_night()
    check("공모: AI 마피아끼리도 한 명으로 통일되어 합의됨",
          appA.core.night_kill_agree() in (a_nm, ai_names[2]))
    with appA.core.lock:   # 이후 시나리오 원복
        appA.core.players[ai_names[1]]["role"] = "citizen"
        appA.core.night_targets.clear()

    # 이 아래 시나리오는 오래 걸려서, 호스트의 접속 끊김 감시기가 B를 '응답 없음'으로 보고
    # 사망 처리→게임 종료까지 가버려 AI 대화 검증을 방해한다(테스트 환경 한정 — 실측).
    # ⑥ 직전까지 감시기를 멈춰 두고 ⑥에서 원래대로 되살린다.
    _wd = getattr(appA, "_disconnect_watch_timer", None)
    if _wd:
        appA.root.after_cancel(_wd)
    appA._mafia_poll_disconnects = lambda: None

    # ===================== 마피아 전용 비밀방(자동 생성) =====================
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.players[a_nm]["role"] = "mafia"
        appA.core.phase = Phase.NIGHT
    stubB.core.players["이팀장B"]["role"] = "mafia"
    stubB.core.players[a_nm]["role"] = "mafia"
    stubB.core.phase = Phase.NIGHT
    appA._maybe_open_mafia_room()
    stubB._maybe_open_mafia_room()
    check("비밀방: 사람 마피아 둘 다 밤에 비밀방이 자동으로 생성됨",
          appA._mafia_room is not None and stubB._mafia_room is not None)
    stubB._mafia_room_ent.insert(0, "비밀작전 하나")
    stubB._mafia_room_send()
    check("비밀방: 동료(A)의 비밀방으로 전달됨",
          pump(lambda: ("이팀장B", "비밀작전 하나") in getattr(appA, "_mafia_room_history", []), timeout=10))
    check("비밀방: 공개 게임방 채팅에는 노출되지 않음",
          not any("비밀작전 하나" in r.get("text", "") for r in appA.mafia_history))
    with appA.core.lock:
        appA.core.players[a_nm]["role"] = "citizen"
    stubB._mafia_room_ent.insert(0, "비밀작전 둘")
    stubB._mafia_room_send()
    pump(lambda: False, timeout=1.0)
    check("비밀방: 마피아가 아니면(시민) 내용을 받지 않음",
          ("이팀장B", "비밀작전 둘") not in getattr(appA, "_mafia_room_history", []))
    stubB._mafia_room_close(keep_reopen=True)
    check("비밀방: ✕로 닫으면 다시 열 수 있는 작은 버튼이 남음",
          stubB._mafia_room is None and stubB._mafia_room_mini is not None)
    stubB._mafia_room_open()
    check("비밀방: 재오픈하면 작은 버튼은 사라지고 방이 다시 열림",
          stubB._mafia_room is not None and stubB._mafia_room_mini is None)
    with appA.core.lock:
        appA.core.players[a_nm]["alive"] = False
    stubB.core.players[a_nm]["alive"] = False
    check("비밀방: 동료가 전멸(사망)하면 비밀방이 필요 없다고 판단함",
          not stubB._mafia_room_wanted() and stubB._mafia_team_names("이팀장B") == [])
    with appA.core.lock:
        appA.core.players[a_nm]["alive"] = True
    stubB.core.players[a_nm]["alive"] = True
    appA._mafia_room_close(); stubB._mafia_room_close()
    check("비밀방: 닫기 후 정리됨", appA._mafia_room is None and stubB._mafia_room is None)

    # ===================== 마피아 비밀방 — 사람 ↔ AI 마피아 티키타카 =====================
    ai_m = ai_names[0]
    ai_pl = next(p_ for p_ in appA.ai.players if p_.name == ai_m)
    ai_pl.role, ai_pl.alive, ai_pl.booted = "mafia", True, True
    for other_ai in appA.ai.players:          # 게임 시작 때 무작위로 배정된 다른 AI의 마피아 역할 제거
        if other_ai is not ai_pl:             # (남아 있으면 그 AI가 대신 답해 검사가 흔들린다)
            other_ai.role, other_ai.alive, other_ai.booted = "citizen", True, True
    for n_, p_ in appA.core.players.items():
        p_["role"] = "citizen"
        p_["alive"] = True
    for who_ in (ai_m, "이팀장B"):
        appA.core.players[who_]["role"] = "mafia"
    appA.core.phase = Phase.NIGHT
    appA.mafia_active = True
    for p_ in stubB.core.players.values():
        p_["role"] = "citizen"
        p_["alive"] = True
    stubB.core.players["이팀장B"]["role"] = "mafia"
    stubB.core.players[ai_m]["role"] = "mafia"
    stubB.core.players[ai_m]["is_ai"] = True
    stubB.core.phase = Phase.NIGHT
    stubB._my_mafia_role = "mafia"
    stubB._my_mafia_mates = [ai_m]

    # (1) 원격 사람 마피아 1명 + AI 마피아: 사람 동료가 없어도 비밀방이 열린다
    appA._mafia_secret_log = []
    stubB._mafia_room_history = []
    check("사람↔AI: 사람 동료 없이 AI 마피아만 있어도 비밀방이 필요하다고 판단함",
          stubB._mafia_team_names("이팀장B") == [] and stubB._mafia_room_wanted())
    stubB._maybe_open_mafia_room()
    check("사람↔AI: AI 동료만 있어도 원격 사람의 비밀방이 열림", stubB._mafia_room is not None)
    stubB._mafia_room_ent.insert(0, "오늘은 누구 노릴까")
    stubB._mafia_room_send()
    check("사람↔AI: 원격 사람의 말에 AI 마피아가 비밀방으로 답장함(원격→호스트→AI→원격)",
          pump(lambda: any(w == ai_m for w, _t in stubB._mafia_room_history), timeout=15))
    check("사람↔AI: AI 마피아의 답장은 공개 게임방 채팅에 노출되지 않음",
          not any("비밀응답" in r.get("text", "") for r in appA.mafia_history))
    # 두 번째 말도 이어서 답장(티키타카)
    n_before = sum(1 for w, _t in stubB._mafia_room_history if w == ai_m)
    stubB._mafia_room_ent.insert(0, "그럼 그 사람으로 하자")
    stubB._mafia_room_send()
    check("사람↔AI: 두 번째 말에도 AI가 다시 답장함",
          pump(lambda: sum(1 for w, _t in stubB._mafia_room_history if w == ai_m) > n_before, timeout=15))
    stubB._mafia_room_close();

    # (2) 호스트(사람 마피아) + AI 마피아: 호스트 본인 비밀방에서도 티키타카
    for who_ in ("이팀장B",):
        appA.core.players[who_]["role"] = "citizen"
    appA.core.players[a_nm]["role"] = "mafia"
    appA._mafia_room_history = []
    appA._mafia_secret_log = []
    appA._maybe_open_mafia_room()
    check("사람↔AI: 호스트 사람 마피아도 AI 동료만 있으면 비밀방이 열림", appA._mafia_room is not None)
    appA._mafia_ai_opener()
    check("사람↔AI: AI 마피아가 먼저 말을 꺼냄(opener)",
          pump(lambda: any(w == ai_m for w, _t in appA._mafia_room_history), timeout=15))
    n_before = sum(1 for w, _t in appA._mafia_room_history if w == ai_m)
    appA._mafia_room_ent.insert(0, "난 조용한 사람이 수상해")
    appA._mafia_room_send()
    check("사람↔AI: 호스트 사람의 말에도 AI 마피아가 답장함",
          pump(lambda: sum(1 for w, _t in appA._mafia_room_history if w == ai_m) > n_before, timeout=15))
    # ===================== 대화로 정한 살해 목표가 실제 합의에 반영됨 =====================
    cands_ = appA._mafia_kill_candidates()
    v1, v2, v3 = cands_[0], cands_[1], cands_[2]

    def _quiet_reset():
        pump(lambda: False, timeout=3.0)          # 이전 시나리오의 늦은 AI 답장이 끝나길 기다림
        appA._mafia_kill_plan = None
        appA._mafia_secret_log = []
        appA._mafia_room_history = []
        appA.core.night_targets.clear()
        appA.core.night_target = None

    # (a) 사람이 대화에서 이름을 말하면 확정 → AI 마피아가 따로 골라 둔 다른 대상이 있어도 그 사람으로 합의
    _quiet_reset()
    appA.core.night_targets[ai_m] = v2
    appA._mafia_room_ent.insert(0, f"오늘 밤은 {v1} 노리자")
    appA._mafia_room_send()
    check("목표: 사람이 대화에서 이름을 말하면 확정 목표로 기록됨",
          appA._mafia_kill_plan == {"target": v1, "by": "human", "confirmed": True})
    check("목표: AI 마피아가 확정된 목표에 동의하는 답장을 함(이름 포함)",
          pump(lambda: any(w == ai_m and v1 in t_ for w, t_ in appA._mafia_room_history), timeout=15))
    check("목표: 비밀방에 '오늘 밤 목표 … 확정' 안내가 표시됨",
          any(w == "📌 오늘 밤 목표" and v1 in t_ and "확정" in t_ for w, t_ in appA._mafia_room_history))
    appA._reconcile_mafia_night()
    check("목표: AI가 따로 정해 둔 대상이 있어도 대화로 정한 대상으로 살해가 합의됨",
          appA.core.night_kill_agree() == v1 and appA.core.night_target == v1)

    # (b) AI가 먼저 제안하고 사람이 이름 없이 동의하면 그 제안이 확정
    _quiet_reset()
    appA._mafia_ai_opener()
    check("목표: AI가 먼저 제안하면 '제안' 상태로 기록됨",
          pump(lambda: bool(appA._mafia_kill_plan) and appA._mafia_kill_plan["by"] == "ai"
               and not appA._mafia_kill_plan["confirmed"], timeout=15))
    prop = appA._mafia_kill_plan["target"]
    appA._mafia_room_ent.insert(0, "좋아 그렇게 하자")
    appA._mafia_room_send()
    check("목표: 사람이 이름 없이 동의하면 AI의 제안이 확정됨",
          appA._mafia_kill_plan == {"target": prop, "by": "human", "confirmed": True})
    appA.core.night_targets[ai_m] = next(c for c in cands_ if c != prop)   # AI 개별 지목이 달라도
    appA._reconcile_mafia_night()
    check("목표: 동의로 확정된 AI 제안대로 살해가 합의됨", appA.core.night_kill_agree() == prop)

    # (c) AI가 제안했어도 사람이 다른 이름을 말하면 사람 결정이 우선
    _quiet_reset()
    appA._mafia_ai_opener()
    pump(lambda: bool(appA._mafia_kill_plan), timeout=15)
    ai_prop = (appA._mafia_kill_plan or {}).get("target")
    other = next(c for c in cands_ if c != ai_prop)
    appA._mafia_room_ent.insert(0, f"아니 {other} 로 하자")
    appA._mafia_room_send()
    check("목표: AI 제안이 있어도 사람이 다른 이름을 말하면 사람 결정이 우선",
          appA._mafia_kill_plan == {"target": other, "by": "human", "confirmed": True})
    pump(lambda: False, timeout=3.0)
    check("목표: 확정 뒤 AI가 다른 이름을 말해도 확정 목표가 바뀌지 않음",
          appA._mafia_kill_plan["target"] == other)

    # (d) 사람이 밤 패널에서 직접 고른 대상은 대화 목표보다 우선(기존 동작 유지)
    appA.core.night_targets[a_nm] = v3 if v3 != other else v1
    picked = appA.core.night_targets[a_nm]
    appA._reconcile_mafia_night()
    check("목표: 밤 패널에서 직접 고른 대상이 대화 목표보다 우선함",
          appA.core.night_kill_agree() == picked)

    # (e) 원격 사람 마피아가 대화로 정한 목표도 호스트 합의에 반영
    _quiet_reset()
    appA.core.players["이팀장B"]["role"] = "mafia"
    v1 = appA._mafia_kill_candidates()[0]        # B가 마피아가 된 뒤의 후보(B 본인 제외)
    stubB._my_mafia_mates = [ai_m]
    stubB._mafia_room_history = []
    stubB._mafia_room_open()
    stubB._mafia_room_ent.insert(0, f"{v1} 로 하자")
    stubB._mafia_room_send()
    check("목표: 원격 사람 마피아의 대화 결정도 호스트에서 확정 목표가 됨",
          pump(lambda: bool(appA._mafia_kill_plan) and appA._mafia_kill_plan["target"] == v1
               and appA._mafia_kill_plan["confirmed"], timeout=15))
    check("목표: 원격 사람 비밀방에도 '오늘 밤 목표' 확정 안내가 전달됨",
          pump(lambda: any(w == "📌 오늘 밤 목표" and v1 in t_ for w, t_ in stubB._mafia_room_history), timeout=15))
    appA._reconcile_mafia_night()
    check("목표: 원격 사람의 대화 결정대로 살해가 합의됨", appA.core.night_kill_agree() == v1)
    stubB._mafia_room_close()
    stubB._my_mafia_mates = None
    appA.core.players["이팀장B"]["role"] = "citizen"
    appA.core.night_targets.clear()
    appA._mafia_kill_plan = None

    # ===================== 밤 AI 행동: 경찰·의사·마피아가 LLM으로 판단 (실제 스레드·큐 경로) =====================
    pump(lambda: False, timeout=3.0)              # 앞 시나리오의 늦은 AI 비밀 답장이 끝나길 기다림
    _others = [p_ for p_ in appA.ai.players if p_ is not ai_pl]
    _pol, _doc = _others[0], _others[1]
    for pl_, role_ in ((_pol, "police"), (_doc, "doctor")):
        pl_.role, pl_.alive, pl_.booted = role_, True, True
        appA.core.players[pl_.name]["role"] = role_
        appA.core.players[pl_.name]["alive"] = True
    appA.core.players[a_nm]["role"] = "mafia"
    appA.core.phase = Phase.NIGHT
    appA.core.police_invest.clear(); appA.core.police_report = None
    appA.core.night_saved = None; appA.core.last_protect = None
    appA.core.night_targets.clear(); appA.core.night_target = None
    appA._mafia_kill_plan = None
    NIGHT_PROMPTS.clear()
    appA.NIGHT_MAFIA_LLM_MS = 300                 # 테스트에서는 마피아 재판단을 빨리 실행
    _t0 = time.time()
    appA._trigger_night_actions()
    check("밤 AI(경찰·의사): LLM 답으로 조사/보호가 접수됨(무작위 대체 타이머 11초보다 훨씬 이른 시점)",
          pump(lambda: len(appA.core.police_invest) == 1 and appA.core.night_saved is not None, timeout=8)
          and time.time() - _t0 < 8)
    _cands_pol = [n for n in appA.core.players if n != _pol.name]
    check("밤 AI 경찰: 후보(자기 자신 제외) 중 한 명을 조사함",
          list(appA.core.police_invest)[0] in _cands_pol)
    check("밤 AI: 경찰·의사에게 LLM 프롬프트가 실제로 전달됨",
          any("[밤 행동 — 경찰]" in p_ for p_ in NIGHT_PROMPTS)
          and any("[밤 행동 — 의사]" in p_ for p_ in NIGHT_PROMPTS))
    check("밤 AI 마피아: 비밀 대화 뒤 LLM에게 살해 대상을 다시 묻고 그 결과를 AI 지목으로 기록",
          pump(lambda: any("[밤 행동 — 마피아]" in p_ for p_ in NIGHT_PROMPTS)
               and ai_m in appA.core.night_targets, timeout=8))
    _mk = [p_ for p_ in NIGHT_PROMPTS if "[밤 행동 — 마피아]" in p_]
    check("밤 AI 마피아 프롬프트의 후보에 마피아(호스트 사람·AI 본인)가 없음",
          bool(_mk) and a_nm not in _mk[0].split("후보(시민 쪽 생존자):")[1].split(".")[0]
          and ai_m not in _mk[0].split("후보(시민 쪽 생존자):")[1].split(".")[0])
    # ---- 송신자 검증(실제 네트워크): 원격 참가자 B가 위조 패킷을 호스트 A에게 보낸다 ----
    appA.core.night_targets.pop(a_nm, None)
    _cand_forge = next(n for n in appA._mafia_kill_candidates())
    stubB._mafia_send_private(a_nm, "end", winner="citizen", roles={})                       # 방장 전용 이벤트
    stubB._mafia_send_private(a_nm, "night_action", actor=a_nm, role="mafia", target=_cand_forge)   # 호스트 이름으로 위조
    pump(lambda: False, timeout=2.0)
    check("송신자 검증(실제 네트워크): 원격 참가자가 보낸 가짜 '게임 종료'를 호스트가 무시함",
          appA.mafia_active and appA.core.phase != Phase.END and appA.core.winner is None)
    check("송신자 검증(실제 네트워크): 호스트 이름으로 위조한 밤 행동을 호스트가 무시함",
          a_nm not in appA.core.night_targets)
    appA.core.day_no += 1                         # 남은 대체 타이머/결과가 이후 시나리오에 섞이지 않게
    del appA.NIGHT_MAFIA_LLM_MS
    appA.core.police_invest.clear(); appA.core.night_saved = None; appA.core.night_targets.clear()
    for pl_ in (_pol, _doc):
        pl_.role = "citizen"
        appA.core.players[pl_.name]["role"] = "citizen"
    appA.core.players[a_nm]["role"] = "mafia"

    # 밤이 끝나면(낮) 늦게 도착한 AI 발언은 버려진다
    n_before = len(appA._mafia_room_history)
    appA.core.phase = Phase.DAY
    appA._mafia_ai_post(ai_pl, "늦은 발언")
    check("사람↔AI: 밤이 끝난 뒤 도착한 AI 비밀 발언은 무시됨",
          len(appA._mafia_room_history) == n_before)
    appA._mafia_room_close()
    # 이후 시나리오 원복
    ai_pl.role = None
    appA.core.phase = Phase.NIGHT
    for n_, p_ in appA.core.players.items():
        p_["role"] = "citizen"
    appA.core.players["이팀장B"]["role"] = "mafia"
    stubB._my_mafia_mates = None
    with appA.core.lock:
        appA.core.players["이팀장B"]["role"] = "mafia"
        appA.core.phase = Phase.DAY

    # ===================== ⑤ 찬반(처형여부) 투표 동기화 =====================
    defendant = ai_names[2]
    with appA.core.lock:
        appA.core.phase = Phase.DAY
        appA.core.defendant = defendant
        appA.core.defense_yes = {}
    stubB.core.phase = Phase.DAY
    stubB.core.defendant = defendant

    stubB._cast_defense(defendant, True)
    check("⑤ A(호스트)의 core.defense_yes에 B의 실제 찬반 표가 반영됨",
          pump(lambda: appA.core.defense_yes.get("이팀장B") is True, timeout=10))

    # ===================== AI 피고인의 최후 변론이 원격 참가자에게도 전달됨 =====================
    import queue as _qq
    appA._defense_ui_q = _qq.Queue()
    appA._defense_has_spoken = False
    appA._defense_ui_q.put(("defense", defendant, "변론합니다: 저는 시민이에요 진짜로"))
    appA._poll_defense_ui_queue()
    check("변론: 호스트 화면에 AI 피고인의 변론이 표시됨",
          any("변론합니다" in r.get("text", "") for r in appA.mafia_history))
    check("변론: 원격 참가자(B) 화면에도 AI 피고인의 변론이 전달됨",
          pump(lambda: any("변론합니다" in r.get("text", "") for r in stubB.mafia_history), timeout=10))
    appA._defense_ui_q = None

    # ===================== 원격 사망자의 유령방 → 호스트의 사망 AI가 답장 =====================
    _gb = tk.Text(rootB)                      # B의 유령방 화면 대역
    stubB._ghost_list = _gb
    stubB._ghost_ui_open = True
    for pl_ in appA.ai.players:
        pl_.alive, pl_.booted = True, True
    appA.core.players["이팀장B"]["alive"] = False       # B가 사망(호스트 core 기준)
    stubB._ghost_ai_reply("아무도 없어?")
    check("유령방(원격): 사망 AI가 없으면 '대화 상대가 없어요' 안내가 돌아옴",
          pump(lambda: "대화 상대가 없어요" in _gb.get("1.0", "end"), timeout=10))
    _gb.delete("1.0", "end")
    dead_pl = next(p_ for p_ in appA.ai.players if p_.name == ai_names[1])
    dead_pl.alive = False
    appA.core.players[dead_pl.name]["alive"] = False
    stubB._ghost_ai_reply("나 죽었어 ㅠㅠ 뭐해?")
    check("유령방(원격): 원격 사망자의 말에 호스트의 사망 AI가 답장해 B 화면에 표시됨",
          pump(lambda: f"👻 {dead_pl.name}:" in _gb.get("1.0", "end"), timeout=15))
    check("유령방(원격): 산 사람 B가 아니라 사망 상태일 때만 호스트가 중계함",
          "안내" not in _gb.get("1.0", "end").split(dead_pl.name)[-1])
    appA.core.players["이팀장B"]["alive"] = True       # 산 사람이 보낸 유령방 말은 무시
    _gb.delete("1.0", "end")
    stubB._ghost_ai_reply("살아있는데 보내본다")
    pump(lambda: False, timeout=2.0)
    # 화면 텍스트는 앞서 받은 패킷이 재전송으로 다시 찍힐 수 있어 판단 기준으로 부적절 —
    # 호스트가 이 발언을 처리했다면 원격 대화 기록(_ghost_remote_log)에 남는다.
    check("유령방(원격): 생존자가 보낸 유령방 발언은 호스트가 AI 답장 없이 무시함",
          "살아있는데 보내본다" not in chr(10).join(getattr(appA, "_ghost_remote_log", {}).get("이팀장B", [])))
    dead_pl.alive = True
    appA.core.players[dead_pl.name]["alive"] = True
    stubB._ghost_list = None
    stubB._ghost_ui_open = False

    # ===================== 동료 마피아 통보(마피아에게만) =====================
    mate = ai_names[0]
    appA._mafia_send_private("이팀장B", "hdm", target="이팀장B", text="🃏 테스트 '마피아'", role="mafia", mates=[mate])
    check("동료: B의 core에 동료 마피아 역할이 반영됨",
          pump(lambda: stubB.core.players.get(mate, {}).get("role") == "mafia", timeout=10))
    check("동료: B의 core.mafias()에 동료가 포함되어 밤 후보에서 제외 가능",
          mate in stubB.core.mafias())
    pump(lambda: False, timeout=0.5)
    try:
        stubB._mafia_overlay_close()
    except Exception:
        pass
    civ = next(n for n, p in appA.core.players.items() if p.get("role") not in ("mafia",) and n not in ("이팀장B",) and n != mate)
    # 마피아가 아닌 사람에게 가는 쪽지에는 mates가 없어야 한다(호스트 코드 경로 확인)
    import inspect, mafia_ui
    src = inspect.getsource(mafia_ui.MafiaUIMixin._launch_game_with_recruits)
    check("동료: 호스트가 마피아 직업에게만 mates를 붙이는 경로임", 'if prole == "mafia"' in src)

    # ===================== 찬반 개시 / 처형 판결 동기화 =====================
    appA._mafia_broadcast("defense_vote_open", name=defendant)
    check("defense_vote_open: 원격(B) 화면에 찬반 팝업이 열림",
          pump(lambda: stubB._mafia_overlay is not None, timeout=10))
    stubB._mafia_overlay_close()
    appA._mafia_broadcast("verdict", result="executed", name=defendant, role="citizen", yes=2, no=1)
    check("verdict: 처형된 사람이 원격 core에서도 사망 처리됨",
          pump(lambda: stubB.core.players[defendant]["alive"] is False, timeout=10))
    pump(lambda: False, timeout=0.3)

    # ===================== 게임 종료(end) → 원격 상태 로비 복귀 =====================
    appA._mafia_broadcast("end", winner="citizen",
                          roles={n: p.get("role") for n, p in appA.core.players.items()})
    check("end: 원격(B)의 mafia_active가 꺼지고 core가 로비로 복귀함",
          pump(lambda: stubB.mafia_active is False and stubB.core.phase == Phase.LOBBY, timeout=10))

    # ===================== ⑥ 접속 끊김 → 자동 사망 처리 =====================
    with appA.core.lock:
        appA.core.defendant = None
    appA._mafia_disconnected = set()
    del appA._mafia_poll_disconnects      # 멈춰 둔 감시기를 원래 메서드로 되돌리고
    appA._mafia_poll_disconnects()         # 다시 가동

    try:
        stubB.engine.stop()
    except Exception:
        pass

    check("⑥ A가 B의 접속 끊김을 감지해 사망 처리함",
          pump(lambda: appA.core.players.get("이팀장B", {}).get("alive") is False, timeout=20))
    check("⑥ 사망 처리 사실이 시스템 메시지로 안내됨",
          any("이팀장B" in t and "사망 처리" in t for t in A_sys))
    leak = [t for t in A_sys if "이팀장B" in t and "끊긴 것 같습니다" in t
            and any(r in t for r in ("마피아", "의사", "경찰", "시민"))]
    check("⑥ 끊김/사망 안내가 역할 정보를 노출하지 않음", len(leak) == 0)
    check("⑥ 끊김 안내가 곧바로 사망 처리하면서 '재접속하면 다시 인식'이라고 모순되게 말하지 않음",
          not any("재접속하면" in t for t in A_sys))

finally:
    try:
        appA._cancel_mafia_timer()
    except Exception:
        pass
    try:
        stubB.engine.stop()
    except Exception:
        pass
    try:
        appA.engine.stop()
    except Exception:
        pass
    try:
        if getattr(appA, "_notifier", None) is not None:
            appA._notifier.close()      # 트레이 아이콘 제거 — 안 하면 종료 후에도 죽은 아이콘이 남는다
    except Exception:
        pass
    try:
        rootB.destroy()
    except Exception:
        pass
    try:
        rootA.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print("MULTIPLAYER SYNC", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
