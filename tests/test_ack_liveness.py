# -*- coding: utf-8 -*-
"""v1.94 — 상대의 presence가 안 와도 내가 보낸 패킷의 ack가 오면 생존으로 본다."""
import importlib.util, os, shutil, socket, sys, time
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
B=os.path.dirname(os.path.abspath(__file__)); A=os.path.dirname(B); sys.path.insert(0,A)
sp=importlib.util.spec_from_file_location("lan_messenger",os.path.join(A,"lan_messenger.py")); lm=importlib.util.module_from_spec(sp)
_rb=socket.socket.bind
socket.socket.bind=lambda s,a:_rb(s,("127.0.0.1",a[1])) if isinstance(a,tuple) and a[0] in ("","0.0.0.0") else _rb(s,a)
sp.loader.exec_module(lm)
tmp=os.path.join(B,"tmp_ackl"); shutil.rmtree(tmp,ignore_errors=True)
for n in "AB": os.makedirs(os.path.join(tmp,n)); open(os.path.join(tmp,n,"firewall_notice_done"),"w").close()
ea=lm.Engine("A",port=60095,datadir=os.path.join(tmp,"A"),on_event=lambda e:None,instance_id="A"*10)
eb=lm.Engine("B",port=60096,datadir=os.path.join(tmp,"B"),on_event=lambda e:None,instance_id="B"*10)
ok=True
def check(l,c):
    global ok; print("OK  " if c else "FAIL",l); ok=ok and bool(c)
key=("127.0.0.1",60096)
with ea.plock:
    ea.peers[key]={"name":"B","ip":key[0],"port":key[1],"last":0,"static":False}
ea.send_message(key[0],key[1],"안녕")
end=time.time()+5
while time.time()<end and ea.peers[key]["last"]==0: time.sleep(0.05)
check("presence 없이도 ack만으로 상대의 last가 갱신됨", ea.peers[key]["last"]>0)
# v1.95 — 메시지를 보내지 않아도 ping 응답으로 "접속 중"이 됨(프로그램을 막 켠 직후 상황)
with ea.plock:
    ea.peers[key]["last"]=0
end=time.time()+15
while time.time()<end and ea.peers.get(key,{}).get("last",0)==0: time.sleep(0.1)
check("메시지 없이도 ping 응답으로 접속 중 표시", ea.peers.get(key,{}).get("last",0)>0)
ea.stop(); eb.stop(); shutil.rmtree(tmp,ignore_errors=True)
print("ACK LIVENESS","PASSED" if ok else "FAILED"); sys.exit(0 if ok else 1)
