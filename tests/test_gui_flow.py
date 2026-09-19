# -*- coding: utf-8 -*-
"""GUI 상호작용 전체 흐름 (v3 텔레그램풍 + 그룹) — 조건 폴링 기반, 고정 딜레이 없음

이전 버전은 root.after(고정 ms)로 클릭·전송을 예약했는데, 사이드바가 presence 이벤트마다
다시 그려지는 타이밍과 겹치면 클릭이 유실되어 간헐적으로 실패했다(개발기록 참고).
이 버전은 매 단계마다 "조건이 실제로 충족될 때까지" root.update()를 반복 호출해 폴링하므로
타이밍에 좌우되지 않는다.
"""
import argparse
import base64
import importlib.util
import socket
import shutil
import sys
import time
import os

if __name__ != "__main__":
    # crypto_layer.py가 ProcessPoolExecutor(멀티프로세싱)를 쓰는데, 이 스크립트가
    # __main__ 가드 없이 자식 프로세스로 재실행되면 전체 로직이 또 한 번 돌면서
    # (같은 포트로 Engine을 또 만들려다 실패하는 등) 타이밍을 흔들어 간헐적 실패를
    # 유발한다(실측: 가십 팬아웃 체크가 이 때문에 흔들렸음). 자식 재실행 시 즉시 종료.
    sys.exit(0)

TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

BASE = os.path.dirname(os.path.abspath(__file__))      # tests/ 자기 폴더 (임시 테스트 데이터용)
APP_ROOT = os.path.dirname(BASE)                        # 프로젝트 루트 (lan_messenger.py 등 실제 코드)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)                        # lan_messenger.py가 import하는 app/engine 등을 찾게 함
spec = importlib.util.spec_from_file_location("lan_messenger", os.path.join(APP_ROOT, "lan_messenger.py"))
lm = importlib.util.module_from_spec(spec)

_real_bind = socket.socket.bind


def _spy(self, addr):
    if isinstance(addr, tuple) and len(addr) == 2 and addr[0] in ("", "0.0.0.0"):
        _real_bind(self, ("127.0.0.1", addr[1]))
    else:
        _real_bind(self, addr)


socket.socket.bind = _spy
spec.loader.exec_module(lm)

import tkinter as tk


def wait_gui(root, cond, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return False
        if cond():
            return True
        time.sleep(0.01)
    return False


def chat_dump(app):
    out = []
    for iid in app.chat.find_all():
        if app.chat.type(iid) == "text":
            out.append(app.chat.itemcget(iid, "text"))
    return "\n".join(out)


tmp = os.path.join(BASE, "tmp_flow")
shutil.rmtree(tmp, ignore_errors=True)

for sub in ("GUI_A", "GUI_B", "GUI_C"):
    os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    open(os.path.join(tmp, sub, "firewall_notice_done"), "w", encoding="utf-8").close()

B_events = []
B = lm.Engine("이팀장B", port=60002, datadir=os.path.join(tmp, "GUI_B"),
              on_event=lambda e: B_events.append(e), instance_id="B" * 10)
B.set_static_targets({("127.0.0.1", 60001)})

C = lm.Engine("박대리C", port=60003, datadir=os.path.join(tmp, "GUI_C"),
              on_event=lambda e: None, instance_id="C" * 10)
C.set_static_targets({("127.0.0.1", 60001)})

args = argparse.Namespace(name="김재무A", port=60001, datadir=os.path.join(tmp, "GUI_A"),
                          peers="127.0.0.1:60002,127.0.0.1:60003")

root = tk.Tk()
root.withdraw()
app = lm.App(root, args, ["127.0.0.1:60002", "127.0.0.1:60003"])

ok_all = True


def check(label, cond):
    global ok_all
    result = bool(cond)
    print(("OK  " if result else "FAIL"), label)
    if not result:
        ok_all = False
    return result


try:
    # ---- 1) 1:1 대화: 사이드바 등장 -> 클릭 -> 전송 -> 상대 이름 반영 확인 ----
    check("사이드바에 최소 2개 행 등장",
          wait_gui(root, lambda: len(app.pinner.winfo_children()) >= 2))

    dm_key = ("dm", "127.0.0.1", 60002)  # B — 명시적으로 특정 상대를 골라 뒤 단계에서 B의 응답과 매칭
    check("B의 사이드바 행 발견", wait_gui(root, lambda: dm_key in app._rows))
    dm_row = app._rows.get(dm_key)

    if dm_key:
        dm_row["frame"].event_generate("<Button-1>")
        check("클릭 후 선택 반영", wait_gui(root, lambda: app.current == dm_key))
        # 상대 이름이 presence로 채워질 때까지 대기 후 제목 확인(경합 없이 안정적으로 갱신되는지 검증)
        check("헤더 제목이 IP가 아닌 실제 이름으로 반영",
              wait_gui(root, lambda: app.ch_title.cget("text") not in ("", dm_key[1])))

        app.entry.insert("1.0", "GUI 통합 테스트")
        ret = app._send()
        check("_send()가 break 반환", ret == "break")
        check("입력창 비워짐", wait_gui(root, lambda: app.entry.get("1.0", "end-1c").strip() == ""))
        check("내 말풍선이 캔버스에 렌더됨", wait_gui(root, lambda: "GUI 통합 테스트" in chat_dump(app)))

        B.send_message("127.0.0.1", 60001, "돌아오는 메시지")
        check("상대 메시지 수신 후 캔버스에 렌더됨",
              wait_gui(root, lambda: "돌아오는 메시지" in chat_dump(app)))
        check("B가 실제로 수신함",
              wait_gui(root, lambda: any(e.get("ev") == "msg" and e["text"] == "GUI 통합 테스트"
                                        for e in B_events)))

    # ---- 2) 그룹 채팅: 생성 -> 팬아웃 -> 렌더 확인 ----
    check("A가 B·C를 모두 발견", wait_gui(root, lambda: len(app.engine.peers) >= 2))
    member_keys = {k for k in app.engine.peers.keys() if k[1] in (60002, 60003)}
    gid = app.engine.create_group("테스트그룹", member_keys)
    check("그룹 생성 후 사이드바에 그룹 행 등장",
          wait_gui(root, lambda: ("grp", gid) in app._rows))

    grp_row = app._rows.get(("grp", gid))
    if grp_row:
        grp_row["frame"].event_generate("<Button-1>")
        check("그룹 선택 반영", wait_gui(root, lambda: app.current == ("grp", gid)))
        check("그룹 헤더에 이름 표시", wait_gui(root, lambda: app.ch_title.cget("text") == "테스트그룹"))

        check("B가 초대(roster) 수신", wait_gui(root, lambda: gid in B.groups, timeout=5))
        check("C가 초대(roster) 수신", wait_gui(root, lambda: gid in C.groups, timeout=5))

        app.entry.insert("1.0", "전체 공지입니다")
        app._send()
        check("그룹 메시지가 캔버스에 렌더됨", wait_gui(root, lambda: "전체 공지입니다" in chat_dump(app)))

        B.send_group_message(gid, "B의 그룹 답장")
        check("B의 그룹 답장이 C에게도 전달(가십 팬아웃)",
              wait_gui(root, lambda: any(r.get("text") == "B의 그룹 답장"
                                        for r in C.load_group_history(gid))))

    # ---- 3) 대화 삭제(숨김) -> 목록에서 사라짐 -> 상대가 메시지 보내면 자동 재등장 ----
    app._select(dm_key)
    wait_gui(root, lambda: app.current == dm_key)
    app._hide_conversation(dm_key)
    check("숨긴 대화가 채팅 탭 목록에서 사라짐", dm_key not in app._rows)
    check("숨긴 대화 선택 해제(빈 화면으로 복귀)", app.current is None)

    B.send_message("127.0.0.1", 60001, "숨겨진 대화함 다시 살아나야 함")
    check("상대가 메시지를 보내면 자동으로 목록에 재등장",
          wait_gui(root, lambda: dm_key in app._rows))
    check("숨김 해제되어 engine.hidden에서 빠짐", dm_key not in app.engine.hidden)

    # ---- 4) 친구 탭: 삭제가 채팅 탭과 통일 적용 -> [숨긴 대화 모두 표시]로 복구 -> 클릭하면 열림 ----
    app._hide_conversation(dm_key)
    check("다시 숨긴 직후 채팅 탭에서 사라짐", dm_key not in app._rows)
    app._set_tab("friend")
    check("친구 탭 전환 반영", app._tab == "friend")
    check("친구 탭도 삭제(숨김)가 동일하게 적용됨(더 이상 무조건 보이지 않음)",
          dm_key not in app._rows)

    app._unhide_all()
    check("[숨긴 대화 모두 표시]로 친구 탭에도 복구됨", wait_gui(root, lambda: dm_key in app._rows))
    friend_row = app._rows.get(dm_key)
    if friend_row:
        friend_row["frame"].event_generate("<Button-1>")
        check("친구 탭에서 클릭 시 해당 대화가 선택됨", wait_gui(root, lambda: app.current == dm_key))
    app._set_tab("chat")
    check("채팅 탭으로 복귀", app._tab == "chat")

    # ---- 5) 파일 전송: 일반 파일(파일카드) + 작은 PNG(이미지 말풍선) ----
    # 실제 프로덕션 경로(App._attach_file)를 그대로 태우기 위해 파일 선택 다이얼로그만 모킹.
    app._select(dm_key)
    wait_gui(root, lambda: app.current == dm_key)
    fdir = os.path.join(tmp, "files")
    os.makedirs(fdir, exist_ok=True)
    txt_path = os.path.join(fdir, "보고서.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("월간 보고서 초안")

    import tkinter.filedialog as _fd
    _orig_askopen = _fd.askopenfilename
    _fd.askopenfilename = lambda **kw: txt_path
    try:
        app._attach_file()
    finally:
        _fd.askopenfilename = _orig_askopen
    check("일반 파일 첨부 시 pending 등록됨", len(app._pending_sends) >= 1)
    check("B가 파일을 수신함(file_recv)",
          wait_gui(root, lambda: any(e.get("ev") == "file_recv" for e in B_events)))
    check("전송 완료 후 파일카드가 캔버스에 렌더됨(파일명 텍스트 포함)",
          wait_gui(root, lambda: "보고서.txt" in chat_dump(app)))

    png_path = os.path.join(fdir, "스크린샷.png")
    with open(png_path, "wb") as f:
        f.write(base64.b64decode(TINY_PNG_B64))
    n_images_before = sum(1 for i in app.chat.find_all() if app.chat.type(i) == "image")
    _fd.askopenfilename = lambda **kw: png_path
    try:
        app._attach_file()
    finally:
        _fd.askopenfilename = _orig_askopen
    check("PNG 이미지가 실제 image 캔버스 아이템으로 렌더됨(인라인 미리보기)",
          wait_gui(root, lambda: sum(1 for i in app.chat.find_all()
                                    if app.chat.type(i) == "image") > n_images_before))

finally:
    if app.engine:
        app.engine.stop()
    B.stop()
    C.stop()
    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)

print("GUI FLOW", "PASSED" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
