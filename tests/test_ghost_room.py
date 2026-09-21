# -*- coding: utf-8 -*-
"""test_ghost_room.py — 유령방: ① 우하단 [👻 유령방] 버튼 ② 게임이 끝나기 전까지 대화 누적(열 때마다 초기화 안 함)
③ 상단에 유령 현황과 직업 ④ 사람 사망자끼리 중계."""
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


tmp = os.path.join(BASE, "tmp_ghostroom"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("1100x700"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60141, datadir=tmp), [])
app._select(("mgame",)); root.update()
core = app.core
core.players.clear()
for n, ai_ in (("나", False), ("친구", False), ("레오", True), ("미나", True), ("철수", True)):
    core.join(n, is_ai=ai_)
for n, r in (("나", "citizen"), ("친구", "police"), ("레오", "mafia"), ("미나", "doctor"), ("철수", "citizen")):
    core.players[n]["role"] = r
core.phase = Phase.DAY; core.day_no = 2
app.mafia_active = True; app.mafia_host_mode = True; app.mafia_bar_is_game = True
app.ai.players = []
kicks = []
app._kick_ghost_ai_chat = lambda: kicks.append(1)


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        root.update(); time.sleep(0.02)


# ---- 1) 버튼: 죽었을 때만, 우하단에 ----
app._reset_ghost_state()
app._refresh_ghost_button(); pump()
check("살아 있으면 유령방 버튼이 없음", getattr(app, "_ghost_btn", None) is None or not app._ghost_btn.winfo_ismapped())
core.players["나"]["alive"] = False
app._refresh_ghost_button(); pump(0.4)
btn = app._ghost_btn
check("죽으면 [👻 유령방] 버튼이 표시됨", btn.winfo_ismapped())
cw, ch = app.chat.winfo_width(), app.chat.winfo_height()
bx = btn.winfo_rootx() - app.chat.winfo_rootx() + btn.winfo_width()
by = btn.winfo_rooty() - app.chat.winfo_rooty() + btn.winfo_height()
check(f"버튼이 채팅 화면 우하단에 있음 (우측 끝 {bx}/{cw}, 하단 {by}/{ch})", cw - 40 <= bx <= cw and ch - 90 <= by <= ch - 20)
sb = getattr(app, "scroll_btn", None)
check("맨 아래로 버튼과 겹치지 않게 그 위에 놓임", sb is None or not sb.winfo_ismapped() or by <= sb.winfo_rooty() - app.chat.winfo_rooty() + 2)

# ---- 2) 열기: 상단 현황 + 직업 ----
core.players["철수"]["alive"] = False
app._open_ghost_chat(); pump(0.4)
rost = app._ghost_roster.get("1.0", "end")
check("상단 현황에 생존·사망 인원이 표시됨", "생존 3명" in rost and "사망 2명" in rost)
check("상단 현황에 유령(나, 철수)과 직업이 표시됨", "나 (나) — 시민" in rost.replace("👻 ", "") and "철수 🤖 — 시민" in rost.replace("👻 ", ""))
check("아직 살아 있는 사람의 직업은 현황에 나오지 않음(친구=경찰, 레오=마피아)", "경찰" not in rost and "마피아" not in rost)
check("첫 입장에서는 AI 수다를 한 번 시작함", len(kicks) == 1)

# ---- 3) 대화 누적: 닫았다 열어도 이어짐 ----
app._ghost_ent.insert(0, "안녕 유령들")
app._ghost_send()
app._append_ghost("👻 철수: 나도 죽었어 ㅠㅠ")
pump(0.2)
first_view = app._ghost_list.get("1.0", "end")
check("대화가 유령방에 표시됨", "안녕 유령들" in first_view and "나도 죽었어" in first_view)
app._mafia_overlay_close(); pump(0.3)
app._append_ghost("👻 철수: 닫혀 있는 동안 온 말")                # 창이 닫힌 사이에 도착한 말
check("닫혀 있는 동안 온 말도 기록되고 버튼에 새 글 표시(●)가 뜸", app._ghost_unread is True)
app._refresh_ghost_button(); pump(0.2)
check("버튼 문구에 ●가 붙음", "●" in app._ghost_btn_label)
app._open_ghost_chat(); pump(0.4)
second_view = app._ghost_list.get("1.0", "end")
check("다시 열어도 이전 대화가 그대로 남아 있음(초기화 안 됨)", "안녕 유령들" in second_view and "나도 죽었어" in second_view)
check("닫혀 있는 동안 온 말도 보임", "닫혀 있는 동안 온 말" in second_view)
check("열면 새 글 표시가 사라짐", app._ghost_unread is False)
check("유령 구성이 그대로면 AI 수다를 다시 시작하지 않음(반복 인사 방지)", len(kicks) == 1)
app._mafia_overlay_close(); pump(0.2)
for i in range(5):                                                # 여러 턴이 지나도 누적
    app._open_ghost_chat(); pump(0.15); app._mafia_overlay_close(); pump(0.1)
app._open_ghost_chat(); pump(0.3)
check("여러 번 열고 닫아도 계속 누적됨", "안녕 유령들" in app._ghost_list.get("1.0", "end"))
core.players["레오"]["alive"] = False
app._render_ghost_list(); pump(0.2)
check("새 사망자가 생기면 현황이 갱신됨(레오 마피아 공개)", "레오" in app._ghost_roster.get("1.0", "end") and "마피아" in app._ghost_roster.get("1.0", "end"))
app._mafia_overlay_close(); pump(0.2)
app._open_ghost_chat(); pump(0.3)
check("유령 구성이 바뀌면(새 사망자) 다시 AI 수다를 시작함", len(kicks) == 2)

# ---- 4) 게임 시작/종료 때만 초기화 ----
check("초기화 전에는 기록이 남아 있음", len(app._ghost_log) >= 3)
app._reset_ghost_state()
check("게임이 끝나거나 시작하면 유령방 기록이 비워짐", app._ghost_log == [] and app._ghost_unread is False)
app._mafia_overlay_close(); pump(0.2)

# ---- 5) 사람 사망자끼리 중계(호스트) ----
core.players["친구"]["alive"] = False
sent = []
app._mafia_send_private = lambda target, typ, **kw: sent.append((target, typ, kw))
app._ghost_relay_humans("나", "유령끼리 얘기해요")
check("호스트(사망)가 쓴 말이 원격 사망자(친구)에게 전달됨", any(n == "친구" and t == "ghost_say" and kw.get("text") == "유령끼리 얘기해요" and kw.get("name") == "나" for n, t, kw in sent))
sent.clear()
app._ghost_relay_humans("친구", "나도 여기 있어")
check("원격 사망자(친구)의 말은 호스트 화면에 기록됨(호스트도 사망)", any("친구: 나도 여기 있어" in ln for ln in app._ghost_log))
check("말한 사람에게는 다시 보내지 않음", not any(n == "친구" for n, t, kw in sent))
core.players["친구"]["alive"] = True
sent.clear()
app._ghost_relay_humans("나", "살아 있는 사람에겐 안 감")
check("살아 있는 사람에게는 유령방 말이 가지 않음", not sent)
core.players["친구"]["alive"] = False

# ---- 6) ghost_say 수신(창 닫힘) → 누적 ----
app._mafia_overlay_close(); pump(0.2)
app._reset_ghost_state()
app.mafia_host_mode = False; app._recruiter_host = "방장"
app._on_mafia_proto_msg(encode("ghost_say", name="영희", text="호스트가 보낸 유령 대화"), "방장", None); pump(0.2)
check("호스트가 보낸 ghost_say가 유령방이 닫혀 있어도 기록됨", any("영희: 호스트가 보낸 유령 대화" in ln for ln in app._ghost_log))

try:
    root.destroy()
except Exception:
    pass
print("GHOST ROOM", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
