# -*- coding: utf-8 -*-
"""build_exe.py — MAFIA(마피아 게임 포함 랜톡) 독립실행파일 빌드 파이프라인.

1) PyInstaller가 설치된 파이썬 선택 (현재 인터프리터 우선, 없으면 hermes 가상환경)
2) 모듈 컴파일 체크 (mafia_* 포함)
3) PyInstaller onefile/noconsole 빌드 (엔트리: lan_messenger.py)
   - 쓰지 않는 numpy는 제외(Pillow 훅이 끌어와 exe가 20MB → 31MB로 불어난다)
   - 생성 spec은 build/ 로 보낸다(그냥 두면 저장소가 추적하는 MAFIA.spec을 덮어쓴다)
4) 스모크: 임시 데이터 폴더로 exe를 띄워 살아있는지 확인 → 종료(트레이 잔상 정리)
5) SHA-256 출력 (릴리즈 노트용)

사용: python build_exe.py [--no-smoke]

참고: 스모크는 임시 폴더(--datadir)를 쓰므로 dist/data, dist/secret.key는 건드리지 않는다.
dist의 exe를 그대로 실행해 쓰는 경우 그 옆 data/(설정·API 키)가 남아 있으니 지우지 말 것.
"""
import argparse
import ctypes
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
VENV_PY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe")
APP_NAME = "MAFIA"
MODS = ["lan_messenger.py", "constants.py", "netutils.py", "canvas_utils.py",
        "crypto_layer.py", "applog.py", "winapi.py", "engine.py", "widgets.py",
        "stickers.py", "dnd_handler.py", "chat_search.py", "chat_renderer.py",
        "dialogs.py", "mafia_config.py", "mafia_core.py", "mafia_ai.py",
        "mafia_net.py", "mafia_ui.py", "colors_compat.py", "app.py"]
HIDDEN = ["engine", "crypto_layer", "applog", "mafia_config", "mafia_core",
          "mafia_ai", "mafia_net", "mafia_ui", "colors_compat"]
EXCLUDE = ["numpy"]          # 코드가 쓰지 않는데 Pillow 훅이 끌어오는 무거운 패키지
SMOKE_SECONDS = 8            # exe가 이 시간 뒤에도 살아있어야 통과


def run(cmd, timeout=900):
    # 출력에 한글 경로가 섞이면 기본 인코딩(cp949)으로 읽다 실패해 stdout이 None이 되므로
    # 인코딩을 고정하고 깨진 글자는 대체한다(PyInstaller는 UTF-8로 내보낸다).
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).splitlines()


def has_pyinstaller(py):
    try:
        return subprocess.run([py, "-c", "import PyInstaller"], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pick_python():
    """PyInstaller가 실제로 설치된 파이썬을 고른다(예전엔 없는 가상환경을 먼저 골라 빌드가 실패했다)."""
    for py in (sys.executable, VENV_PY):
        if py and os.path.exists(py) and has_pyinstaller(py):
            return py
    sys.exit("PyInstaller가 설치된 파이썬을 찾지 못했습니다 — `pip install pyinstaller` 후 다시 실행하세요.")


def sweep_tray():
    """강제 종료한 프로세스가 트레이에 남긴 죽은 아이콘을 지운다(마우스 이동 메시지 → 셸이 정리). 실패해도 무시."""
    try:
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u32.FindWindowExW.restype = wintypes.HWND
        u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.SendMessageW.restype = wintypes.LPARAM

        def sweep(toolbar):
            if not toolbar:
                return
            rect = wintypes.RECT()
            if not u32.GetClientRect(toolbar, ctypes.byref(rect)):
                return
            for x in range(0, rect.right, 4):
                for y in range(0, rect.bottom, 4):
                    u32.SendMessageW(toolbar, 0x0200, 0, (y << 16) | (x & 0xFFFF))   # WM_MOUSEMOVE

        tray = u32.FindWindowW("Shell_TrayWnd", None)
        notify = u32.FindWindowExW(tray, None, "TrayNotifyWnd", None)
        pager = u32.FindWindowExW(notify, None, "SysPager", None)
        sweep(u32.FindWindowExW(pager, None, "ToolbarWindow32", None))
        overflow = u32.FindWindowW("NotifyIconOverflowWindow", None)
        sweep(u32.FindWindowExW(overflow, None, "ToolbarWindow32", None))
    except Exception:
        pass


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke(exe):
    """임시 데이터 폴더로 exe를 띄워 SMOKE_SECONDS 뒤에도 살아있고 데이터 폴더가 만들어졌는지 본다."""
    tmp = tempfile.mkdtemp(prefix="mafia_smoke_")
    proc = subprocess.Popen([exe, "--datadir", tmp, "--port", str(free_udp_port()), "--name", "smoke"])
    try:
        time.sleep(SMOKE_SECONDS)
        alive = proc.poll() is None
        made_data = os.path.isdir(os.path.join(tmp, "avatars"))    # 앱이 초기화되면 만드는 폴더
        return alive and made_data, alive, made_data
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        sweep_tray()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="MAFIA exe 빌드")
    ap.add_argument("--no-smoke", action="store_true", help="빌드 후 실행 확인(스모크)을 건너뜀")
    args = ap.parse_args()

    py = pick_python()
    print(f"[1/5] 빌드용 파이썬: {py}")

    print("[2/5] 모듈 컴파일 체크")
    rc, out = run([py, "-m", "py_compile", *MODS])
    if rc:
        print("\n".join(out[-15:]))
        sys.exit("COMPILE FAIL")
    print("  all ok")

    print("[3/5] PyInstaller onefile 빌드")
    spec_dir = os.path.join(HERE, "build")
    os.makedirs(spec_dir, exist_ok=True)
    # --specpath를 build/로 옮기면 상대 경로가 spec 위치 기준으로 해석될 수 있어 전부 절대 경로로 준다
    cmd = [py, "-m", "PyInstaller", "-y", "--noconfirm", "--clean", "--onefile",
           "--noconsole", "--name", APP_NAME, "--specpath", spec_dir,
           "--icon", os.path.join(HERE, "app.ico"),
           "--add-data", os.path.join(HERE, "sounds") + ";sounds"]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    cmd.append(os.path.join(HERE, "lan_messenger.py"))
    rc, out = run(cmd, timeout=900)
    print("  " + "\n  ".join([l for l in out if l][-3:]))
    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    if rc or not os.path.exists(exe):
        sys.exit("BUILD FAIL")
    print(f"  exe: {os.path.getsize(exe) / 1024 / 1024:.1f} MB")

    if args.no_smoke:
        print("[4/5] 스모크: 건너뜀(--no-smoke)")
    else:
        print(f"[4/5] 스모크: 임시 폴더로 {SMOKE_SECONDS}초간 실행 → 종료")
        ok, alive, made_data = smoke(exe)
        print(f"  살아있음={alive} 데이터폴더생성={made_data} → {'PASS' if ok else 'FAIL'}")
        if not ok:
            sys.exit("SMOKE FAIL — exe가 정상 기동하지 않습니다(직접 실행해 확인하세요).")

    print("[5/5] SHA-256")
    with open(exe, "rb") as f:
        print(f"  {hashlib.sha256(f.read()).hexdigest()}")
    print(f"완료: dist\\{APP_NAME}.exe")


if __name__ == "__main__":
    main()
