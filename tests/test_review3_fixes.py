# -*- coding: utf-8 -*-
"""test_review3_fixes.py — 외부 분석 보고서의 지적을 코드로 확인한 뒤 고친 항목 검증:
① 원격 의사의 어제 치료 대상 동기화 ② 낮 카운트다운 중 내 직업 표시 ③ _vote_window 해제
④ 유령 중계 큐의 개별 실패 격리 ⑤ AI 색 캐시 ⑥ engine.stop()의 Timer 정리."""
import argparse, importlib.util, os, shutil, socket, sys, threading, time
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


tmp = os.path.join(BASE, "tmp_review3"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60181, datadir=tmp), [])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, a in (("나", False), ("방장", False), ("철수", True), ("영희", True), ("미나", True)):
    core.join(n, is_ai=a)
for n, r in (("나", "doctor"), ("방장", "citizen"), ("철수", "citizen"), ("영희", "police"), ("미나", "mafia")):
    core.players[n]["role"] = r
core.phase = Phase.DAY; core.day_no = 1
app.mafia_active = True; app.mafia_host_mode = False; app._recruiter_host = "방장"; app.mafia_bar_is_game = True
app._my_mafia_role = "doctor"


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


def lbl():
    return app.mafia_phase_lbl.cget("text")


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


# ---- ② 낮 카운트다운 중에도 내 직업이 유지됨 ----
app._client_start_day_countdown(); pump(1.5)
check(f"낮 토론 카운트다운 중에도 '내 직업'이 표시됨: {lbl()!r}", "토론 중" in lbl() and "내 직업: 💉 의사" in lbl())
app._set_night_count(20)
check("밤 카운트다운 중에도 '내 직업'이 표시됨", "내 직업" in lbl())

# ---- ③ 투표 창 플래그 해제 ----
app._on_mafia_proto_msg(encode("vote_open"), "방장", None); pump(0.4)
check("투표 창이 열리면 '개표 중'", "개표 중" in lbl() and app._vote_window is True)
app._mafia_overlay_close(); pump(0.2)
app._on_mafia_proto_msg(encode("defense_start", name="철수"), "방장", None); pump(0.3)
check("최후 변론이 시작되면 투표 창 표시가 꺼짐(클라이언트)", app._vote_window is False)
app.refresh_mafia_phase_label()
check(f"이후 안내를 갱신해도 '개표 중'이 되살아나지 않음: {lbl()!r}", "개표 중" not in lbl())
app._clear_client_defense()
app._vote_window = True
app._on_mafia_proto_msg(encode("verdict", result="spared", name="철수", role="citizen", yes=0, no=1), "방장", None); pump(0.3)
check("판결을 받으면 투표 창 표시가 꺼짐(클라이언트)", app._vote_window is False)
app._vote_window = True
core.phase = Phase.NIGHT
app._on_mafia_proto_msg(encode("night"), "방장", None); pump(0.3)
check("밤이 되면 투표 창 표시가 꺼짐", app._vote_window is False)
try:
    app._mafia_overlay_close()
except Exception:
    pass
# 호스트: 개표 시작·변론 시작 시 해제
app.mafia_host_mode = True
app._mafia_broadcast = lambda *a, **k: None
core.phase = Phase.DAY
app._vote_window = True
app._tally_full = lambda: None
app._tally_in_progress = False
app._tally_and_reveal()
check("호스트: 개표가 시작되면 투표 창 표시가 꺼짐", app._vote_window is False)
app._vote_window = True
core.phase = Phase.DAY
app._defense_in_progress = False
app._start_defense_visuals = lambda n: None
app._poll_defense_ui_queue = lambda: None
app._open_defense_for("철수")
check("호스트: 최후 변론이 시작되면 투표 창 표시가 꺼짐", app._vote_window is False)
for t_ in ("_defense_fallback_timer", "_defense_end_timer", "_defense_deadline"):
    try:
        root.after_cancel(getattr(app, t_))
    except Exception:
        pass
app.mafia_host_mode = False
core.defendant = None; app._defense_in_progress = False

# ---- ① 원격 의사: 어제 치료 대상 동기화 ----
core.phase = Phase.NIGHT
core.last_protect = None
app._mafia_send_to_host = lambda *a, **k: None
app._show_night_panel(); pump(0.3)
check("첫날 밤에는 어제 치료한 사람이 없어 모든 버튼이 활성", not any("어제 치료" in t for t in texts(app._mafia_overlay)))
app._apply_night_pick("철수", "doctor")
check("선택을 보내면 확정 전 대기 값으로 기억함", app._pending_heal == "철수")
app._on_mafia_proto_msg(encode("hdm", target="나", text="💉 (나 의사 신청 접수) 철수 구조 지시 접수"), "방장", None); pump(0.2)
check("호스트의 접수 쪽지를 받으면 치료 대상이 확정됨", getattr(app, "_confirmed_heal", None) == "철수")
app._on_mafia_proto_msg(encode("day", victim=None, role=None), "방장", None); pump(0.4)
check("밤이 끝나면 내 core.last_protect가 어제 치료 대상으로 기록됨", core.last_protect == "철수")
core.phase = Phase.NIGHT
app._show_night_panel(); pump(0.3)
allt = texts(app._mafia_overlay)
check("다음 밤 팝업을 열면 어제 치료한 사람이 미리 비활성으로 표시됨(원격 의사)", any("철수" in t and "어제 치료" in t for t in allt))
app._mafia_overlay_close(); pump(0.2)
app._on_mafia_proto_msg(encode("day", victim=None, role=None), "방장", None); pump(0.3)
check("행동하지 않은 밤이 지나면 last_protect가 비워짐(연속 금지는 바로 다음 밤까지만)", core.last_protect is None)
app._apply_night_pick("철수", "doctor")
app._on_mafia_proto_msg(encode("hdm", target="나", text="⚠ (나 의사) 철수님은 어젯밤 이미 치료한 사람입니다"), "방장", None)
check("거절(⚠)이면 치료가 확정되지 않음", getattr(app, "_confirmed_heal", None) is None)

# ---- ④ 유령 중계 큐: 한 건이 실패해도 나머지 처리 ----
import queue as _q
app.mafia_host_mode = True
app._ghost_relay_q = _q.Queue(); app._ghost_relay_pending = 2; app._ghost_relay_on = True
sent = []


def _send(target, typ, **kw):
    if target == "나쁜":
        raise RuntimeError("boom")
    sent.append((target, kw.get("text")))


app._mafia_send_private = _send
_saved_players = dict(core.players)
for _nm in ("나쁜", "친구"):                 # 유령방의 다른 사람 사망자들
    core.players[_nm] = dict(next(iter(core.players.values())), is_ai=False, alive=False)
app._ghost_relay_q.put(("나쁜", "철수", "첫째"))
app._ghost_relay_q.put(("친구", "영희", "둘째"))
app._poll_ghost_relay(); pump(0.2)
check("한 명에게 보내다 실패해도 큐의 다음 답장은 전달됨", ("친구", "둘째") in sent)
core.players.clear(); core.players.update(_saved_players)
check("대기 카운터가 남지 않음", app._ghost_relay_pending == 0)

# ---- ⑤ AI 색 캐시 ----
app.mafia_host_mode = False
core.phase = Phase.DAY
c1 = app._ai_distinct_color("철수"); c2 = app._ai_distinct_color("영희"); c3 = app._ai_distinct_color("미나")
check("AI마다 서로 다른 색을 받음", len({c1, c2, c3}) == 3 and None not in (c1, c2, c3))
check("사람(나)은 색 배정 대상이 아님", app._ai_distinct_color("나") is None)
cache = app._ai_color_cache
check("결과가 캐시되어 다음 호출은 같은 객체를 재사용함", app._ai_color_cache is cache and app._ai_distinct_color("철수") == c1)
core.players["두식"] = dict(core.players["철수"])          # 명단에 AI 한 명 추가(join은 로비에서만 가능)
c4 = app._ai_distinct_color("두식")
check("AI 명단이 바뀌면 캐시가 갱신되어 새 AI도 색을 받음", c4 is not None and app._ai_color_cache is not cache)
names = sorted(["철수", "영희", "미나", "두식"])
check("배정 규칙(이름 정렬 순서)이 이전과 같음", [app._ai_distinct_color(n) for n in names] == [app.AI_DISTINCT_COLORS[i] for i in range(4)])

# ---- ⑤-2 사람·시스템 라벨도 락 없이 즉시 반환 ----
class _CountLock:
    def __init__(self, inner):
        self.inner, self.n = inner, 0

    def __enter__(self):
        self.n += 1
        return self.inner.__enter__()

    def __exit__(self, *a):
        return self.inner.__exit__(*a)


_real_lock = core.lock
cl = core.lock = _CountLock(_real_lock)
app._ai_distinct_color("철수")                       # 워밍업(캐시 채움)
cl.n = 0
r_h = app._ai_distinct_color("나"); r_h2 = app._ai_distinct_color("방장")
r_sys = app._ai_distinct_color("🖥 사회자"); r_sys2 = app._ai_distinct_color("시스템")
r_ai = app._ai_distinct_color("영희")
check("사람·사회자·시스템 라벨은 None이고 AI는 색을 받음", r_h is None and r_h2 is None and r_sys is None and r_sys2 is None and r_ai is not None)
check(f"사람·시스템 라벨을 반복 조회해도 락에 들어가지 않음(락 진입 {cl.n}회)", cl.n == 0)
app._reset_ghost_state()
check("게임 시작·종료 때 색 캐시가 비워짐", app._ai_color_cache is None)
old_map = {n: app._ai_distinct_color(n) for n in ("철수", "영희", "미나", "두식")}
# 같은 인원수로 이름만 바뀐 새 판 — 캐시가 낡은 명단을 붙잡지 않는다
for n in ("철수", "영희", "미나", "두식"):
    core.players.pop(n)
for n in ("가", "나다", "다라", "라마"):
    core.players[n] = {"is_ai": True, "alive": True, "role": "citizen"}
app._reset_ghost_state()
new_colors = [app._ai_distinct_color(n) for n in sorted(["가", "나다", "다라", "라마"])]
check("같은 인원수의 새 명단도 이름 정렬 순서대로 올바른 색을 받음", new_colors == [app.AI_DISTINCT_COLORS[i] for i in range(4)])
core.lock = _real_lock

# ---- ⑥ engine.stop()이 대기 Timer를 취소 ----
eng = app.engine
tm = threading.Timer(60, lambda: None); tm.daemon = True; tm.start()
eng._read_ack_timer[("1.1.1.1", 1)] = tm
tg = threading.Timer(60, lambda: None); tg.daemon = True; tg.start()
eng._gread_ack_timer["g"] = tg
tb = threading.Timer(60, lambda: None); tb.daemon = True; tb.start()
eng._burn_start_timer[("2.2.2.2", 2)] = tb
eng._cancel_pending_timers()
check("stop 시 읽음·그룹읽음·자동삭제 Timer가 모두 취소되고 표가 비워짐",
      tm.finished.is_set() and tg.finished.is_set() and tb.finished.is_set()
      and not eng._read_ack_timer and not eng._gread_ack_timer and not eng._burn_start_timer)
try:
    root.destroy()
except Exception:
    pass
print("REVIEW3 FIXES", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
