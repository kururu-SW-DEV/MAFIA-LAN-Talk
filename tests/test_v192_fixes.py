# -*- coding: utf-8 -*-
"""v1.92 — Opus 4차 리뷰 반영 항목 검증."""
import argparse, importlib.util, os, shutil, socket, sys
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
B=os.path.dirname(os.path.abspath(__file__)); A=os.path.dirname(B); sys.path.insert(0,A)
sp=importlib.util.spec_from_file_location("lan_messenger",os.path.join(A,"lan_messenger.py")); lm=importlib.util.module_from_spec(sp)
_rb=socket.socket.bind
socket.socket.bind=lambda s,a:_rb(s,("127.0.0.1",a[1])) if isinstance(a,tuple) and a[0] in ("","0.0.0.0") else _rb(s,a)
sp.loader.exec_module(lm)
import tkinter as tk
from mafia_core import GameCore, Phase
from mafia_net import encode
import mafia_ui
ALL=True
def check(l,c):
    global ALL; print("OK  " if c else "FAIL",l); ALL=ALL and bool(c)
# 1) 경찰은 한 밤에 한 명만
c=GameCore("t")
for n,r in (("경",  "police"),("가","citizen"),("나","mafia"),("다","citizen")):
    c.join(n,False); 
for n,r in (("경","police"),("가","citizen"),("나","mafia"),("다","citizen")): c.players[n]["role"]=r
c.phase=Phase.NIGHT; c.day_no=1
check("첫 조사는 결과가 나옴", c.police_investigate("나")=="mafia")
check("같은 밤 두 번째 조사는 거부", c.police_investigate("가") is None)
c.day_no=2
check("다음 밤에는 다시 조사 가능", c.police_investigate("가")=="citizen")
# 2) AI 인격이 사람 이름과 겹치지 않음
names={p["name"] for p in mafia_ui.ALL_PERSONAS}
ex=set(list(names)[:5])
ok=all(not ({p["name"] for p in mafia_ui.MafiaUIMixin._pick_ai_personas(8,exclude=ex)} & ex) for _ in range(30))
check("사람 이름과 겹치는 AI 인격은 뽑히지 않음", ok)
# 3) 모집 알림·로컬 안내
tmp=os.path.join(B,"tmp_v192"); shutil.rmtree(tmp,ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp,"firewall_notice_done"),"w").close()
root=tk.Tk(); root.withdraw()
app=lm.App(root,argparse.Namespace(name="클라",port=60094,datadir=tmp),[])
app.core.join("방장",False); app.core.phase=Phase.DAY
app.mafia_active=True; app._recruiter_host="방장"
app._on_mafia_proto_msg(encode("recruit_start",host="방장",players=["방장"]),"방장",None)
check("진행 중이던 판의 방장이 새 모집을 열면(end 유실) 로비로 복귀", app.mafia_active is False and app.core.phase==Phase.LOBBY and app._recruiting)
sent=[]; app._mafia_broadcast=lambda *a,**k: sent.append(a)
app.mafia_host_mode=True; app.mafia_active=True
app.add_mafia_system("공개 안내"); app.add_mafia_system("내 안내",local=True)
check("local=True 안내는 방송하지 않음", len(sent)==1 and sent[0][0]=="sys")
# 4) v1.93 — 원격 경찰 조사 결과 기록·낮 타이머 동기화·투표 창 닫힘
app.mafia_host_mode=False; app.mafia_active=True; app._recruiter_host="방장"; app.core.phase=Phase.DAY
app.core.join("경찰",False) if False else None
app.core.players.setdefault("나",{"role":None,"alive":True,"is_ai":False})
me=app.engine.name; app.core.players.setdefault(me,{"role":"police","alive":True,"is_ai":False})
app._on_mafia_proto_msg(encode("hdm",target=me,text="🕵 [조사 결과 — 나에게만 보임] 철수님은 마피아입니다!"),"방장",None)
check("원격 경찰: 조사 결과가 내 core.police_invest에 기록됨", app.core.police_invest.get("철수")=="mafia")
app._on_mafia_proto_msg(encode("day_timer",sec=150),"방장",None)
import time as _t
check("day_timer로 낮 남은 시간이 맞춰짐", 140 < app._day_deadline-_t.time() <= 150)
opened=[]
app._vote_lbl=object(); app._cancel_vote_popup=lambda: opened.append(1)
app._on_mafia_proto_msg(encode("vote_close"),"방장",None)
check("vote_close를 받으면 투표 팝업을 닫음", opened==[1])
# 5) v1.95 — 끊긴 사망 마피아 지목 제외, 밤 안내 로컬, 토큰으로 주소 재묶음, 사이드바 없이도 say_relay
c2=GameCore("t2")
for n,r in (("가","mafia"),("나","mafia"),("다","citizen"),("라","citizen"),("마","citizen")):
    c2.join(n,False); c2.players[n]["role"]=r
c2.phase=Phase.NIGHT; c2.day_no=1
c2.mafia_night_vote("가","다"); c2.mafia_night_vote("나","다")
c2.players["나"]["alive"]=False
c2.night_targets["나"]="라"
check("죽은 마피아의 다른 지목이 남아도 살아 있는 마피아 합의는 유효", c2.night_kill_agree()=="다")
sent2=[]; app._mafia_send_private=lambda n,t,**k: sent2.append((n,t))
app.mafia_host_mode=True; app.mafia_active=True; app.core.phase=Phase.DAY
for n in ("원격1","원격2"): app.core.players[n]={"role":"citizen","alive":True,"is_ai":False}
app._mafia_relay_say("원격1","안녕")
check("호스트는 발언을 보낸 사람을 뺀 다른 사람 참가자에게 중계", "원격2" in [x[0] for x in sent2] and "원격1" not in [x[0] for x in sent2])
root.destroy()
print("V192 FIXES","PASSED" if ALL else "FAILED"); sys.exit(0 if ALL else 1)
