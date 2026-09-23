# -*- coding: utf-8 -*-
"""test_peer_refresh_event.py — 대기로 보이던 상대가 살아나거나 끊길 때 peer 이벤트가 나가는지."""
import os, shutil, sys, time
if __name__ != "__main__":
    sys.exit(0)
sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(BASE))
import engine as E
tmp = os.path.join(BASE, "tmp_peer_refresh"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
evs = []
eng = E.Engine("A", port=60091, datadir=tmp, on_event=evs.append, instance_id="A" * 10)
ok = True
def check(l, c):
    global ok; print("OK  " if c else "FAIL", l); ok = ok and bool(c)
try:
    key = ("127.0.0.1", 60092)
    eng._upsert_peer(*key, "B")
    with eng.plock:
        eng.peers[key]["last"] = time.time() - 100; eng.peers[key]["static"] = True
    time.sleep(0.2); evs.clear()
    eng._upsert_peer(*key, "B")
    time.sleep(0.2)
    check("대기 → 접속 중 전환 시 peer 이벤트", any(e.get("ev") == "peer" for e in evs))
    evs.clear(); eng._upsert_peer(*key, "B"); time.sleep(0.2)
    check("이미 접속 중이면 이벤트 없음", not any(e.get("ev") == "peer" for e in evs))
    with eng.plock:
        eng.peers[key]["last"] = time.time() - 100
    evs.clear(); eng._prune(); time.sleep(0.2)
    check("접속 중 → 대기 전환 시 peer 이벤트", any(e.get("ev") == "peer" for e in evs))
finally:
    eng.stop(); shutil.rmtree(tmp, ignore_errors=True)
print("PEER REFRESH", "PASSED" if ok else "FAILED"); sys.exit(0 if ok else 1)
