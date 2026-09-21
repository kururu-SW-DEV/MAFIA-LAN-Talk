# -*- coding: utf-8 -*-
"""클라이언트에 지난 판 상태가 남아 있어도 새 모집의 [참가 신청] 버튼이 뜨는지."""
import argparse, importlib.util, os, shutil, socket, sys
sys.stdout.reconfigure(encoding="utf-8")
BASE=os.path.dirname(os.path.abspath(__file__)); APP=os.path.dirname(BASE); sys.path.insert(0,APP)
spec=importlib.util.spec_from_file_location("lan_messenger",os.path.join(APP,"lan_messenger.py")); lm=importlib.util.module_from_spec(spec)
_rb=socket.socket.bind
socket.socket.bind=lambda s,a:_rb(s,("127.0.0.1",a[1])) if isinstance(a,tuple) and a[0] in("","0.0.0.0") else _rb(s,a)
spec.loader.exec_module(lm)
import tkinter as tk
from mafia_net import encode
ALL=True
if __name__!='__main__': sys.exit(0)
tmp=os.path.join(BASE,"tmp_repro"); shutil.rmtree(tmp,ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp,"firewall_notice_done"),"w").close()
root=tk.Tk(); app=lm.App(root,argparse.Namespace(name="클라",port=60031,datadir=tmp),[])
root.geometry("1000x700"); root.update()
app._select(("mgame",)); root.update()

def trial(label, **st):
    for k,v in st.items(): setattr(app,k,v)
    app._on_mafia_proto_msg(encode("recruit_start",host="방장",players=["방장"]),"방장",None); root.update()
    ok=bool(app._recruiting and app.mafia_join_btn.winfo_ismapped())
    print("OK  " if ok else "FAIL", label, "- 참가 신청 버튼 표시")
    global ALL; ALL = ALL and ok
    app._on_mafia_proto_msg(encode("recruit_cancel",host="방장"),"방장",None); root.update()
    for k,v in dict(mafia_host_mode=False,mafia_active=False,_recruiter_host=None,_in_game=True).items(): setattr(app,k,v)
trial("normal")
trial("stale host_mode", mafia_host_mode=True)
trial("stale mafia_active(no host)", mafia_active=True)
import time as _t
trial("stale mafia_active+old host offline", mafia_active=True, _recruiter_host="옛방장", _mafia_last_host_ts=_t.time()-600)
trial("stale recruiter_host==me", _recruiter_host="클라")
trial("stale _in_game False", _in_game=False)
root.destroy()
print('JOIN STALE PASSED' if ALL else 'JOIN STALE FAILED'); sys.exit(0 if ALL else 1)
