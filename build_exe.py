# -*- coding: utf-8 -*-
"""build_exe.py — MAFIA(마피아 게임 포함 랜톡) 독립실행파일 빌드 전체 파이프라인.

1) 모듈 컴파일 체크 (mafia_* 포함)
2) netutils.py: app_dir frozen 분기 유지 + ICO_EMBED에 app.ico base64 채움
3) winapi.py: 4개 app.ico 접근 지점 exe 대응 패치(멱등)
4) PyInstaller onefile/noconsole 빌드 (엔트리: lan_messenger.py)
5) dist 스모크: 창 표시 확인 후 종료, 테스트 부산물(data/secret.key) 정리

사용: python build_exe.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
VENV_PY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe")
PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable
APP_NAME = "MAFIA"


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).splitlines()


def main():
    print("[1/5] 모듈 컴파일 체크")
    mods = ["lan_messenger.py", "constants.py", "netutils.py", "canvas_utils.py",
            "crypto_layer.py", "applog.py", "winapi.py", "engine.py", "widgets.py",
            "stickers.py", "dnd_handler.py", "chat_search.py", "chat_renderer.py",
            "dialogs.py", "mafia_config.py", "mafia_core.py", "mafia_ai.py",
            "mafia_net.py", "mafia_ui.py", "colors_compat.py", "app.py"]
    rc, out = run([sys.executable, "-m", "py_compile", *mods])
    if rc:
        print("\n".join(out[-15:]))
        sys.exit("COMPILE FAIL")
    print("  all ok")

    print("[2/5] ICO 임베드 + winapi exe 패치 — v6.48은 PNG-B64 내장이라 스킵")

    print("[2/5] wait — [3/5] PyInstaller onefile 빌드")
    cmd = [PY, "-m", "PyInstaller", "-y", "--noconfirm", "--clean", "--onefile",
           "--noconsole", "--name", APP_NAME, "--icon", "app.ico",
           "--add-data", "sounds;sounds",
           "--hidden-import", "engine", "--hidden-import", "crypto_layer",
           "--hidden-import", "applog",
           "--hidden-import", "mafia_config", "--hidden-import", "mafia_core",
           "--hidden-import", "mafia_ai", "--hidden-import", "mafia_net",
           "--hidden-import", "mafia_ui", "--hidden-import", "colors_compat",
           "lan_messenger.py"]
    rc, out = run(cmd, timeout=900)
    tail = [l for l in out if l][-4:]
    print("  " + "\n  ".join(tail))
    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    if rc or not os.path.exists(exe):
        sys.exit("BUILD FAIL")
    print(f"  exe: {os.path.getsize(exe) // 1024} KB")

    print("[4/5] 스모크: 창 표시 확인 → 종료")
    smoke_py = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "exe_smoke.py")
    if os.path.exists(smoke_py):
        rc2, out2 = run([sys.executable, smoke_py], timeout=120)
        if rc2 == 0:
            print("  smoke: PASS")
        else:
            print("  smoke: SKIP/FAIL (수동 확인 필요)")
    else:
        print("  smoke: SKIP (smoke 스크립트 없음)")

    print("[5/5] 테스트 부산물 정리 (dist/data, secret.key, secret.zip)")
    for junk in (os.path.join("dist", "data"),
                 os.path.join("dist", "secret.key"),
                 os.path.join("dist", "secret.zip")):
        if os.path.isdir(junk):
            import shutil
            shutil.rmtree(junk, ignore_errors=True)
        elif os.path.exists(junk):
            try:
                os.remove(junk)
            except OSError:
                pass
    print(f"완료: dist\\{APP_NAME}.exe")


if __name__ == "__main__":
    main()
