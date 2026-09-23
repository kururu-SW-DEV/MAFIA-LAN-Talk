# -*- coding: utf-8 -*-
"""test_bar_button_order.py — 게임바 버튼 좌우 순서가 pack 순서에 상관없이 항상 [참가 신청] 왼쪽, [게임 설정] 맨 오른쪽."""
import argparse, importlib.util, os, shutil, socket, sys
if __name__ != "__main__": sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
BASE=os.path.dirname(os.path.abspath(__file__)); APP=os.path.dirname(BASE); sys.path.insert(0,APP)
spec=importlib.util.spec_from_file_location("lan_messenger",os.path.join(APP,"lan_messenger.py")); lm=importlib.util.module_from_spec(spec)
_rb=socket.socket.bind
socket.socket.bind=lambda s,a:_rb(s,("127.0.0.1",a[1])) if isinstance(a,tuple) and a[0] in ("","0.0.0.0") else _rb(s,a)
spec.loader.exec_module(lm)
import tkinter as tk
ALL=True
def check(l,c):
    global ALL; print("OK  " if c else "FAIL",l); ALL=ALL and bool(c)
tmp=os.path.join(BASE,"tmp_bar_btn"); shutil.rmtree(tmp,ignore_errors=True); os.makedirs(tmp)
open(os.path.join(tmp,"firewall_notice_done"),"w").close()
root=tk.Tk(); root.geometry("1000x700"); root.withdraw()
app=lm.App(root,argparse.Namespace(name="나",port=60094,datadir=tmp),[])
try:
    app._select_mafia_room(("mgame",)); root.update()
    # 버그 재현: 참가 신청을 먼저, 설정을 나중에 pack해서 순서를 뒤집는다
    app.mafia_join_btn.pack_forget(); app.mafia_cfg_btn.pack_forget()
    app.mafia_join_btn.pack(side="right"); app.mafia_cfg_btn.pack(side="right"); root.update()
    app._mafia_bar_fix_order(); root.update()
    check("[게임 설정]이 맨 오른쪽", app.mafia_cfg_btn.winfo_x() > app.mafia_join_btn.winfo_x())
    app._recruiting = True; app._recruiter_host = "남"
    app.mafia_start_btn.pack_forget()
    app.mafia_join_btn.pack_forget(); app.mafia_join_btn.pack(side="right")
    app.mafia_cfg_btn.pack_forget(); app.mafia_cfg_btn.pack(side="right")
    app.refresh_mafia_phase_label(); root.update()
    check("모집 중 클라이언트: 설정이 오른쪽, 참가 신청이 왼쪽", app.mafia_cfg_btn.winfo_x() > app.mafia_join_btn.winfo_x())
    app._recruiting = False; app._select_mafia_room(("mgame",)); root.update()
    xs = [app.mafia_cfg_btn.winfo_x(), app.mafia_start_btn.winfo_x(), app.mafia_join_btn.winfo_x()]
    check("로비: 설정 > 모집 > 참가 신청 순(오른쪽→왼쪽)", xs[0] > xs[1] > xs[2])
finally:
    root.destroy(); shutil.rmtree(tmp, ignore_errors=True)
print("BAR BUTTON ORDER","PASSED" if ALL else "FAILED"); sys.exit(0 if ALL else 1)
