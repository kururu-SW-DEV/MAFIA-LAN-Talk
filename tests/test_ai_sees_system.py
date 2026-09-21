# -*- coding: utf-8 -*-
"""test_ai_sees_system.py — AI가 사망·처형·직업 공개 같은 게임 결과 시스템 안내를 기억하는지(잡음은 제외)."""
import argparse, importlib.util, os, shutil, socket, sys
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

tmp = os.path.join(BASE, "tmp_aisys"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp, "firewall_notice_done"), "w").close()
root = tk.Tk(); root.geometry("900x600"); root.update()
app = lm.App(root, argparse.Namespace(name="나", port=60101, datadir=tmp), [])
ALL = True


def check(label, cond):
    global ALL
    print("OK  " if cond else "FAIL", label); ALL = ALL and bool(cond)


pl = mafia_ai.PlayerAgent("레오", {}, "#fff") if False else None


class FakePl:
    def __init__(self, n):
        self.name, self.alive, self.booted, self.memory = n, True, True, []

    def observe(self, who, text):
        self.memory.append((who, text))


a, b = FakePl("레오"), FakePl("미나")
app.ai.players = [a, b]
app.mafia_active = True; app.mafia_host_mode = True

seen_msgs = [
    "🕯 밤 사망 — 철수", "🎭 직업 공개 — 철수 (의사)", "⚖ 찬성 3 : 반대 1 — '영희' 님 처형 확정!",
    "⚖ 찬성 1 : 반대 3 — 처형 부결, '영희' 님은 살아남았습니다.", "🗳 유효표 없음 — 전원 기권, 처형 무효.",
    "🗳 최다 득표 동률 (철수, 영희) — 동률 후보만으로 1회 재투표!", "🎭 정체 공개 — 철수(마피아)",
]
noise = ["🗳 철수님 투표 접수 완료 (익명 개표) · 진행률 1/5", "⚖ 철수님 찬반 표 접수 (익명) · 진행률 1/4",
         "💭 미나님이 생각 중… (약 4~6초)", "⚠ 방장이 내 투표를 접수했다는 확인이 없습니다", "✅ 철수님이 다시 연결되었습니다.",
         "🌐 AI API 서버: http://x / 모델: y", "🕵 [조사 결과 — 나에게만 보임] 철수님은 마피아입니다!"]
for m in seen_msgs + noise:
    app.add_mafia_system(m)
mem = [t for _, t in a.memory]
check("게임 결과 시스템 안내 7종이 모든 AI 기억에 들어감", all(m in mem for m in seen_msgs) and len(mem) == len(seen_msgs))
check("두 번째 AI에게도 똑같이 전달됨", [t for _, t in b.memory] == mem)
check("진행률·접속·경고·API 안내·비공개 조사 결과 같은 잡음/비밀은 기억하지 않음", not any(n in mem for n in noise))
check("발화자는 '시스템'으로 기록됨", all(w == "시스템" for w, _ in a.memory))
a.memory.clear()
app.mafia_host_mode = False                       # 클라이언트에는 AI가 없으므로 아무 일도 없어야 한다
app.add_mafia_system("🕯 밤 사망 — 철수")
check("호스트가 아니면 기억하지 않음", a.memory == [])
root.destroy()
print("AI SEES SYSTEM", "PASSED" if ALL else "FAILED")
sys.exit(0 if ALL else 1)
