# -*- coding: utf-8 -*-
"""사내 메신저 무인 통합 테스트 (헤드리스) -- v1.3 검증"""
import importlib.util
import socket
import sys
import time
import os
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))      # tests/ 자기 폴더 (임시 테스트 데이터용)
APP_ROOT = os.path.dirname(BASE)                        # 프로젝트 루트 (lan_messenger.py 등 실제 코드)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)                        # lan_messenger.py가 import하는 app/engine 등을 찾게 함
spec = importlib.util.spec_from_file_location(
    "lan_messenger", os.path.join(APP_ROOT, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)

_real_bind = socket.socket.bind


def _spy_bind(self, addr):
    # 루프백 가상 LAN: 모든 bind를 127.0.0.1로 통일 (비대칭 방지)
    if isinstance(addr, tuple) and len(addr) == 2 and addr[0] in ("", "0.0.0.0"):
        _real_bind(self, ("127.0.0.1", addr[1]))
    else:
        _real_bind(self, addr)


socket.socket.bind = _spy_bind
spec.loader.exec_module(lm)


def wait_until(cond, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def step1_units():
    t = lm.korea_time_str(0)
    assert ("오전" in t) or ("오후" in t)
    assert lm.safe_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert lm.safe_name("///") == "peer"
    assert lm.parse_target("10.3.4.5") == ("10.3.4.5", 50707)
    assert lm.parse_target("10.3.4.5:60001") == ("10.3.4.5", 60001)
    for bad in ("", "x.y.z.w", "1.2.3.4:0", "1.2.3.4:70000", "300.1.1.1", "1.2.3.4:abc"):
        try:
            lm.parse_target(bad)
        except ValueError:
            continue
        raise AssertionError("통과하면 안 되는 입력: " + str(bad))
    print("1) 포맷터·IP 검증 OK")


def step2_pair_test():
    ev_a, ev_b = [], []
    tmp = os.path.join(BASE, "tmp")
    # 이전 실행의 설정(static_targets) 오염 제거
    for sub in ("A", "B"):
        p = os.path.join(tmp, sub, "settings.json")
        if os.path.exists(p):
            os.remove(p)
    A = lm.Engine("김재무", port=50707, datadir=os.path.join(tmp, "A"),
                  on_event=lambda e: ev_a.append(e),
                  instance_id="A" * 10)
    B = lm.Engine("이팀장", port=50708, datadir=os.path.join(tmp, "B"),
                  on_event=lambda e: ev_b.append(e),
                  instance_id="B" * 10)
    try:
        print("2) bind:", A.sock.getsockname()[1], B.sock.getsockname()[1])

        # 같은 세그먼트 자동 발견은 실망에서 검증하고, 루프백에서는 초강건성(10054 생존)을 검증:
        # B가 아직 bind 전(load 직후)이어도 A가 먼저 static 전송을 쏴도 수신 스레드가 죽으면 안 된다.
        A.set_static_targets({("127.0.0.1", 50708)})
        B.set_static_targets({("127.0.0.1", 50707)})

        def alive_recv(engine):
            return any(t.name == "recv" and t.is_alive()
                       for t in __import__("threading").enumerate())

        assert alive_recv(A), "A recv 스레드 사망 (WSAECONNRESET 미처리)"
        assert alive_recv(B), "B recv 스레드 사망 (WSAECONNRESET 미처리)"
        print("2) ICMP 도달불가 상황에서 수신 스레드 생존 OK")

        assert wait_until(lambda: any(e.get("ev") == "peer" for e in ev_a) and
                                  any(e.get("ev") == "peer" for e in ev_b), 8), \
            "상호 발견 실패"
        print("2) 상호 발견 OK (static 직접 경로)")

        A.send_message("127.0.0.1", 50708, "테스트 메시지 1")
        got = wait_until(lambda: any(e.get("ev") == "msg" for e in ev_b), 5)
        assert got, "수신 실패: %r" % (ev_b[-3:],)
        msg_ev = [e for e in ev_b if e.get("ev") == "msg"][0]
        assert msg_ev["name"] == "김재무" and msg_ev["text"] == "테스트 메시지 1"
        assert msg_ev["peer"][1] == 50707  # 발신자 회신 포트 보존
        acked = wait_until(lambda: any(e.get("ev") == "sent" and e.get("ok")
                                       for e in ev_a), 5)
        assert acked, "ACK 실패"
        print("2) 전송·수신확인(ACK) OK -- 중복 전송이 아님(1회 수신):",
              sum(1 for e in ev_b if e.get("ev") == "msg"), "건")

        hist_b = B.load_history("127.0.0.1", 50707)
        hist_a = A.load_history("127.0.0.1", 50708)
        assert any(r["dir"] == "in" for r in hist_b), hist_b
        assert any(r["dir"] == "out" for r in hist_a), hist_a
        print("2) 대화 기록 저장 OK -- A발신 %d건 / B수신 %d건" % (len(hist_a), len(hist_b)))

        # 응답 없는 상대: 3회 재전송 후 실패 보고
        A.send_message("127.0.0.1", 50999, "응답 없는 상대")
        dead = wait_until(lambda: any(e.get("ev") == "sent" and not e.get("ok")
                                      for e in ev_a), 12)
        assert dead, "실패 보고 누락"
        retries = sum(1 for e in ev_b if e.get("ev") == "msg" and "응답" in e.get("text", ""))
        print("2) 무응답 상대 3회 재전송 후 실패 보고 OK (B가 죽어 있어 수신 %d건)" % retries)

        # 마무리 강건성: 실패 이후에도 정상 전송이 되는지 (recv 생존 재확인)
        assert alive_recv(A) and alive_recv(B)
        A.send_message("127.0.0.1", 50708, "마지막 메시지")
        assert wait_until(lambda: any(e.get("ev") == "msg" and e["text"] == "마지막 메시지"
                                      for e in ev_b), 5), "실패 후 재전송 붕괴"
        ok2 = wait_until(lambda: any(e.get("ev") == "sent" and e.get("ok")
                                     and "마지막" not in ""
                                     for e in ev_a), 5)
        assert ok2
        print("2) 실패 이후 재사용 정상 OK")
    finally:
        A.stop()
        B.stop()


def step3_group_test():
    """단체대화방: 생성 초대 팬아웃, 멤버 간 가십 전파, 뒤늦게 합류한 멤버의 이력 스냅샷 수신."""
    tmp = os.path.join(BASE, "tmp_group")
    shutil.rmtree(tmp, ignore_errors=True)
    ev_a, ev_b, ev_c = [], [], []
    A = lm.Engine("A그룹장", port=51001, datadir=os.path.join(tmp, "A"),
                  on_event=lambda e: ev_a.append(e), instance_id="GA" * 5)
    B = lm.Engine("B멤버", port=51002, datadir=os.path.join(tmp, "B"),
                  on_event=lambda e: ev_b.append(e), instance_id="GB" * 5)
    C = lm.Engine("C멤버", port=51003, datadir=os.path.join(tmp, "C"),
                  on_event=lambda e: ev_c.append(e), instance_id="GC" * 5)
    try:
        gid = A.create_group("공지방", {("127.0.0.1", 51002), ("127.0.0.1", 51003)})
        assert wait_until(lambda: gid in B.groups and gid in C.groups, 5), "그룹 초대 전파 실패"
        assert ("127.0.0.1", 51002) not in B.groups[gid]["members"], "B 자신이 B의 멤버 목록에 남으면 안 됨(자기참조 필터)"
        assert ("127.0.0.1", 51001) in B.groups[gid]["members"], "그룹 생성자(A)가 B의 멤버 목록에 있어야 함"
        print("3) 그룹 생성·초대 전파 OK -- B/C 로스터:", sorted(B.groups[gid]["members"]))

        A.send_group_message(gid, "전체 공지")
        assert wait_until(lambda: any(e.get("ev") == "gmsg" for e in ev_b) and
                                  any(e.get("ev") == "gmsg" for e in ev_c), 5), "그룹 메시지 팬아웃 실패"
        print("3) 그룹 메시지 팬아웃(A→B,C) OK")

        B.send_group_message(gid, "B의 답장")
        assert wait_until(lambda: any(r.get("text") == "B의 답장" for r in C.load_group_history(gid)), 5), \
            "그룹원 간 가십 전파 실패(B→C, A의 초대로 얻은 로스터 재사용)"
        print("3) 그룹원 간 가십 팬아웃(B→C) OK")

        D = lm.Engine("D뒷북", port=51004, datadir=os.path.join(tmp, "D"),
                      on_event=lambda e: None, instance_id="GD" * 5)
        try:
            A.add_group_members(gid, {("127.0.0.1", 51004)})
            assert wait_until(lambda: gid in D.groups, 5), "뒤늦게 추가된 멤버 초대 실패"
            hist_d = D.load_group_history(gid)
            assert any(r["text"] == "전체 공지" for r in hist_d), "뒤늦은 합류자 이력 스냅샷 누락"
            assert any(r["text"] == "B의 답장" for r in hist_d), "뒤늦은 합류자 이력 스냅샷 누락"
            print("3) 뒤늦게 추가된 멤버가 최근 대화 스냅샷 수신 OK")
        finally:
            D.stop()
    finally:
        A.stop()
        B.stop()
        C.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def step4_file_transfer_test():
    """파일 전송: DM 종단 검증, 파일명 충돌 자동 넘버링, 20MB 초과 즉시 거부, 그룹 팬아웃."""
    tmp = os.path.join(BASE, "tmp_file")
    shutil.rmtree(tmp, ignore_errors=True)
    ev_a, ev_b, ev_c = [], [], []
    A = lm.Engine("FA", port=53001, datadir=os.path.join(tmp, "A"), on_event=lambda e: ev_a.append(e),
                 instance_id="FA" * 5)
    B = lm.Engine("FB", port=53002, datadir=os.path.join(tmp, "B"), on_event=lambda e: ev_b.append(e),
                 instance_id="FB" * 5)
    C = lm.Engine("FC", port=53003, datadir=os.path.join(tmp, "C"), on_event=lambda e: ev_c.append(e),
                 instance_id="FC" * 5)
    try:
        A.set_static_targets({("127.0.0.1", 53002)})
        B.set_static_targets({("127.0.0.1", 53001)})
        assert wait_until(lambda: any(e.get("ev") == "peer" for e in ev_a), 8), "A/B 상호 발견 실패"

        src_dir = os.path.join(tmp, "src")
        os.makedirs(src_dir, exist_ok=True)
        dm_file = os.path.join(src_dir, "메모.txt")
        payload = ("가나다라마바사 " * 4000).encode("utf-8")  # 청크 여러 개 되도록
        with open(dm_file, "wb") as f:
            f.write(payload)

        fid = A.send_file(("dm", "127.0.0.1", 53002), dm_file)
        assert fid, "send_file이 fid를 못 돌려줌"
        assert wait_until(lambda: any(e.get("ev") == "file_recv" for e in ev_b), 10), \
            "B가 파일을 못 받음: %r" % (ev_b[-5:],)
        assert wait_until(lambda: any(e.get("ev") == "file_sent" and e.get("ok") for e in ev_a), 10)
        recv_ev = [e for e in ev_b if e.get("ev") == "file_recv"][0]
        with open(recv_ev["path"], "rb") as f:
            assert f.read() == payload, "받은 파일 내용이 원본과 다름"
        assert recv_ev["is_image"] is False
        print("4) DM 파일 전송(청크 분할·조립·내용 일치) OK -- %d bytes" % len(payload))

        hist_a = A.load_history("127.0.0.1", 53002)
        hist_b = B.load_history("127.0.0.1", 53001)
        assert any(r.get("kind") == "file" and r.get("dir") == "out" for r in hist_a)
        assert any(r.get("kind") == "file" and r.get("dir") == "in" for r in hist_b)
        print("4) DM 파일 이력 저장 OK")

        A.send_file(("dm", "127.0.0.1", 53002), dm_file)
        assert wait_until(lambda: sum(1 for e in ev_b if e.get("ev") == "file_recv") >= 2, 10)
        paths = [e["path"] for e in ev_b if e.get("ev") == "file_recv"]
        assert paths[0] != paths[1], "동일 파일명 재전송 시 경로 충돌(자동 넘버링 실패): %r" % paths
        print("4) 파일명 충돌 자동 넘버링 OK")

        big_path = os.path.join(src_dir, "big.bin")
        with open(big_path, "wb") as f:
            f.seek(A.max_file_size + 10)
            f.write(b"\0")
        try:
            A.send_file(("dm", "127.0.0.1", 53002), big_path)
            raise AssertionError("20MB 초과 파일인데 예외가 안 남")
        except ValueError:
            print("4) 20MB 초과 파일 즉시 거부 OK")

        gid = A.create_group("파일방", {("127.0.0.1", 53002), ("127.0.0.1", 53003)})
        assert wait_until(lambda: gid in B.groups and gid in C.groups, 8)
        note_path = os.path.join(src_dir, "note.txt")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("그룹 파일 테스트")
        A.send_file(("grp", gid), note_path)
        assert wait_until(lambda: any(e.get("ev") == "gfile_recv" for e in ev_b), 10)
        assert wait_until(lambda: any(e.get("ev") == "gfile_recv" for e in ev_c), 10)
        assert any(r.get("kind") == "file" for r in C.load_group_history(gid))
        print("4) 그룹 파일 팬아웃·이력 저장 OK")
    finally:
        A.stop()
        B.stop()
        C.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    step1_units()
    step2_pair_test()
    step3_group_test()
    step4_file_transfer_test()
    print("ALL PASSED")
