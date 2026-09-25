# -*- coding: utf-8 -*-
"""v1.117 — Opus 9차 리뷰 반영분: 판 번호 확인(부팅 스레드·늦은 AI 발언·롤백), 참가자 판 정리 통합, 방장 끊김 포기 시
leave_game 통지, presence 스레드 생존, 렌더 캐시, 개표 가드, _cancel_after."""
import argparse, importlib.util, os, shutil, socket, sys, threading, time
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
import emoji_render
from mafia_core import Phase
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


tmp = os.path.join(BASE, "tmp_v1117"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1000x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60171, datadir=tmp), [])

# ---- 1) 이전 판의 부팅 스레드는 새 판을 건드리지 않는다
app.mafia_active = True; app._game_epoch = 5
spawned = []
app.ai.spawn_all = lambda *a, **k: spawned.append(1) or []
app._host_opening_now = lambda ep=None: setattr(app, "_game_epoch", 6)      # 개회 멘트를 기다리는 사이 판이 바뀜
app._host_then_bootstrap_bg(5)
check("판 번호가 바뀌면 AI를 새로 만들지 않음", not spawned)
app.mafia_active = False; app._game_epoch = 7
app._host_opening_now = lambda ep=None: None
app._host_then_bootstrap_bg(7)
check("판이 끝났으면(mafia_active=False) 부팅하지 않음", not spawned)
shown = []
app.mafia_active = True
app.add_mafia_host = lambda t, **k: shown.append(t)
del app._host_opening_now
timers = []
app.start_day_timer = lambda *a, **k: timers.append(1)
app._game_epoch = 8
app.finish_host_opening(3)
check("finish_host_opening은 낡은 판 번호면 낮 타이머를 다시 시작하지 않음", not timers)
app.finish_host_opening(8)
check("현재 판 번호면 낮 타이머를 시작함", timers == [1])

# ---- 2) 늦게 도착한 AI 발언은 요청 시점의 판 번호로 걸러진다
app._game_epoch = 10; app._ai_utt_q = []
called = []
app._show_ai_utt = lambda n, c, t: called.append(t) or True
app._ai_utt_next = 0
app._on_ai_utt("루카", "#fff", "옛 판 발언", 9); root.update()
check("옛 판 번호 발언은 표시되지 않음", "옛 판 발언" not in called)
app._ai_utt_next = 0
app._on_ai_utt("루카", "#fff", "이번 판 발언", 10); root.update()
check("현재 판 번호 발언은 표시됨", "이번 판 발언" in called)
import mafia_ai
d = mafia_ai.AIDirector(); got = []
d.on_utt = lambda *a: got.append(a); d.epoch_fn = lambda: 42
pl = mafia_ai.PlayerAgent("가", "성격", "#fff"); pl.say = lambda prompt: "안녕"
d._say_worker(pl, lambda p: "x")
check("AIDirector가 요청 시점 판 번호를 발언에 실어 보냄", got and got[0][-1] == 42)

# ---- 3) 참가자 정리: 변론 표시·비밀방 정리가 한 경로로
app._defense_in_progress = True; app.mafia_active = True
app._client_reset_to_lobby()
check("_client_reset_to_lobby가 _defense_in_progress를 지움", app._defense_in_progress is False)

# ---- 4) 방장 끊김 포기: leave_game 통지, hb 없는 옛 방장은 접속 목록에 살아 있으면 포기 안 함
sent = []
app._mafia_send_private = lambda to, t, **kw: sent.append((to, t))
app.mafia_active = True; app._recruiter_host = "방장"
app._client_watch_reset(); app._note_host_alive("hb")
app._mafia_last_host_ts = time.time() - 60
app._client_host_watch()
check("포기할 때 방장에게 leave_game을 보냄", ("방장", "leave_game") in sent and app.mafia_active is False)
sent.clear()
app.mafia_active = True; app._recruiter_host = "방장"; app._client_watch_reset()       # hb 못 받은 옛 방장
app._mafia_peer_of = lambda n: ("127.0.0.1", 1)
app.engine.get_peer = lambda a: {"last": time.time()}
app._mafia_last_host_ts = time.time() - 60
app._client_host_watch()
check("hb 없는 방장이 접속 목록에 살아 있으면 45초가 지나도 포기하지 않음", app.mafia_active is True and not sent)
app.mafia_active = False

# ---- 5) presence 스레드는 예외로 죽지 않는다
eng = app.engine; calls = []
def _boom():
    calls.append(1)
    if len(calls) == 1:
        raise RuntimeError("dictionary changed size during iteration")
    eng._stop.set()
eng._presence_tick = _boom
old_wait = eng._stop.wait
eng._stop.wait = lambda t=None: False
eng._stop.clear()
eng._presence_loop()
eng._stop.wait = old_wait
check("presence 루프가 첫 예외 뒤에도 계속 돎", len(calls) == 2)
eng._stop.set()

# ---- 6) 렌더 캐시: 같은 입력은 사본을 돌려주고 원본을 오염시키지 않음
a = emoji_render.render_pill("🎮 테스트", emoji_render.FONT_PATH_BOLD, 10, "white", "#334155")
b = emoji_render.render_pill("🎮 테스트", emoji_render.FONT_PATH_BOLD, 10, "white", "#334155")
check("같은 알약 렌더는 같은 크기", a is not None and b is not None and a.size == b.size and a is not b)
a.putpixel((1, 1), (255, 0, 0, 255))
c = emoji_render.render_pill("🎮 테스트", emoji_render.FONT_PATH_BOLD, 10, "white", "#334155")
check("한 호출부가 이미지를 고쳐도 캐시가 오염되지 않음", c.getpixel((1, 1)) != (255, 0, 0, 255))

# ---- 7) 개표는 게임이 끝났으면 아무것도 하지 않음
app.mafia_active = False
before = len(app.mafia_history)
app._tally_full()
check("판이 끝난 뒤의 _tally_full은 로비에 아무 줄도 남기지 않음", len(app.mafia_history) == before)

# ---- 8) _cancel_after
fired = []
app._tmr = app.root.after(200, lambda: fired.append(1))
app._cancel_after("_tmr", "_없는속성")
root.update(); time.sleep(0.3); root.update()
check("_cancel_after가 예약을 취소하고 속성을 비움", not fired and app._tmr is None)

# ---- 9) 찬반 투표 중에는 방장도 발언 잠금, 판결 때 풀림
app._select(("mgame",)); root.update()
app._unlock_defense_entry(keep_lock=True)
check("찬반 투표 시작: 방장도 발언 잠금·입력창 비활성", app._defense_entry_locked is True and str(app.entry.cget("state")) == "disabled"
      and str(app.send_btn.cget("state")) == "disabled")
app._unlock_defense_entry()
check("판결(잠금 해제): 다시 발언 가능", app._defense_entry_locked is False and str(app.entry.cget("state")) == "normal")

root.destroy()
print("V1117 FIXES PASSED" if ALL else "V1117 FIXES FAILED"); sys.exit(0 if ALL else 1)
