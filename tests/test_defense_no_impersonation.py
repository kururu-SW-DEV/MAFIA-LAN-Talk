# -*- coding: utf-8 -*-
"""사람(원격 포함) 피고인 이름으로 정해진 변론문이 대신 올라가지 않는지."""
import argparse, importlib.util, os, shutil, socket, sys
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
B=os.path.dirname(os.path.abspath(__file__)); A=os.path.dirname(B); sys.path.insert(0,A)
sp=importlib.util.spec_from_file_location("lan_messenger",os.path.join(A,"lan_messenger.py")); lm=importlib.util.module_from_spec(sp)
_rb=socket.socket.bind
socket.socket.bind=lambda s,a:_rb(s,("127.0.0.1",a[1])) if isinstance(a,tuple) and a[0] in ("","0.0.0.0") else _rb(s,a)
sp.loader.exec_module(lm)
import tkinter as tk, queue
from mafia_core import Phase
ALL=True
def check(l,c):
    global ALL; print("OK  " if c else "FAIL",l); ALL=ALL and bool(c)
tmp=os.path.join(B,"tmp_defimp"); shutil.rmtree(tmp,ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp,"firewall_notice_done"),"w").close()
root=tk.Tk(); root.withdraw()
app=lm.App(root,argparse.Namespace(name="방장",port=60093,datadir=tmp),[])
c=app.core; c.players.clear()
for n,ai in (("방장",False),("원격",False),("철수",True)): c.join(n,is_ai=ai)
c.phase=Phase.DAY; app.mafia_active=True; app.mafia_host_mode=True
for d,ai in (("원격",False),("철수",True)):
    app.mafia_history.clear(); c.defendant=d; app._defense_has_spoken=False
    app._defense_ui_q=queue.Queue()
    app._defense_fallback(d); app._poll_defense_ui_queue()
    said=any(r.get("label")==d and "확신합니다" in r.get("text","") for r in app.mafia_history)
    check(f"피고인 {d}({'AI' if ai else '사람'}): 기본 변론문 대리 발언 "+("있음(AI만 허용)" if ai else "없음"), said==ai)
root.destroy()
print("DEFENSE NO IMPERSONATION","PASSED" if ALL else "FAILED"); sys.exit(0 if ALL else 1)
