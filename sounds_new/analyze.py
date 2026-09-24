"""사운드 정량 분석 도구 (numpy + 표준 라이브러리만 사용).

사용:
    python analyze.py                # ../sounds, ./ , 알림음, 시스템 비프를 분석해 analysis.json 저장 + 표 출력
모듈로 import 해서 analyze_samples()/analyze_file() 을 써도 된다.

측정 항목
- 길이, 샘플레이트, 비트, 채널
- 피크 dBFS, RMS dBFS(전체), 활성 RMS(피크 대비 -40dB 이상 구간만), 크레스트 팩터
- DC 오프셋
- 시작/끝 클릭: 첫/마지막 샘플 절대값, 첫/끝 2ms 안의 최대 샘플 간 점프
- 엔벨로프: 어택 시간(10ms RMS 엔벨로프가 피크 -3dB 에 닿는 시간), 끝 부분 잔향 여부(마지막 50ms 레벨)
- 스펙트럼: 중심 주파수(centroid), 롤오프 85%, 대역별 에너지 비율, 주요 피크 5개,
  '배음 풍부도'(최대 피크 대비 -30dB 안에 드는 뚜렷한 스펙트럼 피크 수)
"""
import ast
import io
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
EPS = 1e-12


def db(x):
    return float(20 * np.log10(max(float(x), EPS)))


def read_wav_bytes(data):
    with wave.open(io.BytesIO(data), "rb") as w:
        return _read(w)


def read_wav(path):
    with wave.open(path, "rb") as w:
        return _read(w)


def _read(w):
    ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float64) / (1 << 23)
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr, sw * 8, ch


def envelope(x, sr, win_ms=10.0):
    hop = max(1, int(sr * win_ms / 1000))
    n = len(x) // hop
    if n == 0:
        return np.array([np.sqrt(np.mean(x ** 2))]), hop
    seg = x[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(seg ** 2, axis=1)), hop


def spectrum_stats(x, sr):
    n = len(x)
    nfft = 1 << int(np.ceil(np.log2(max(n, 4096))))
    win = np.hanning(n)
    mag = np.abs(np.fft.rfft(x * win, nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    p = mag ** 2
    tot = p.sum() + EPS
    centroid = float((freqs * p).sum() / tot)
    cum = np.cumsum(p) / tot
    rolloff = float(freqs[np.searchsorted(cum, 0.85)])
    bands = {"<150Hz": (0, 150), "150-500": (150, 500), "500-2k": (500, 2000),
             "2k-5k": (2000, 5000), ">5k": (5000, sr / 2 + 1)}
    band_pct = {k: round(float(p[(freqs >= a) & (freqs < b)].sum() / tot * 100), 1) for k, (a, b) in bands.items()}
    # 스펙트럼 피크 (국소 최대 + 최대치 대비 -30dB 이내 + 최소 간격 20Hz)
    mdb = 20 * np.log10(mag + EPS)
    top = mdb.max()
    loc = np.where((mdb[1:-1] > mdb[:-2]) & (mdb[1:-1] >= mdb[2:]) & (mdb[1:-1] > top - 30))[0] + 1
    loc = loc[np.argsort(-mdb[loc])]
    picked = []
    for i in loc:
        f = freqs[i]
        if f < 20:
            continue
        if all(abs(f - q) > 20 for q, _ in picked):
            picked.append((float(f), float(mdb[i] - top)))
    return {
        "centroid_hz": round(centroid, 1),
        "rolloff85_hz": round(rolloff, 1),
        "band_energy_pct": band_pct,
        "n_peaks_30db": len(picked),
        "top_peaks": [(round(f, 1), round(d, 1)) for f, d in picked[:6]],
    }


def analyze_samples(x, sr, bits=16, ch=1, name=""):
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    env, hop = envelope(x, sr)
    emax = env.max() if len(env) else 0
    active = env > emax * 10 ** (-40 / 20)
    active_rms = float(np.sqrt(np.mean(env[active] ** 2))) if active.any() else 0.0
    # 어택: 엔벨로프가 최대치 -3dB에 처음 닿는 시각
    atk_idx = int(np.argmax(env >= emax * 10 ** (-3 / 20))) if emax > 0 else 0
    # 클릭(경계 불연속): 첫/마지막 샘플 값 + 파일 경계 0.3ms 안의 최대 샘플 간 점프.
    #   페이드 없이 시작/끝나면 이 값이 커진다(0.02 이상이면 '틱' 잡음 가능).
    # onset_jump: 첫 2ms 안의 최대 점프 — 의도된 빠른 타격(나무·노이즈)도 커지므로 참고용.
    kb = max(2, int(sr * 0.0003))
    k = max(2, int(sr * 0.002))
    d = np.abs(np.diff(x))
    start_jump = float(max(abs(x[0]), d[:kb].max() if len(d) else 0))
    end_jump = float(max(abs(x[-1]), d[-kb:].max() if len(d) else 0))
    onset_jump = float(d[:k].max()) if len(d) else 0.0
    # 소형 스피커(노트북) 근사: 200Hz 2차 고역통과 후 활성 RMS — 저역 위주 소리가 실제로 얼마나 작게 들릴지
    n2 = 1 << int(np.ceil(np.log2(len(x) + sr // 4)))
    X = np.fft.rfft(np.concatenate([x, np.zeros(n2 - len(x))]))
    f = np.fft.rfftfreq(n2, 1 / sr)
    H = 1 / np.sqrt(1 + (200.0 / np.maximum(f, 1e-9)) ** 4)
    H[0] = 0
    xs = np.fft.irfft(X * H, n2)[: len(x)]
    es, _ = envelope(xs, sr)
    spk_rms = float(np.sqrt(np.mean(es[active[: len(es)]] ** 2))) if active.any() else 0.0
    tail50 = x[-int(sr * 0.05):]
    tail_db = db(np.sqrt(np.mean(tail50 ** 2))) if len(tail50) else -120
    # 무음 앞/뒤(피크 -50dB 이하)
    thr = peak * 10 ** (-50 / 20)
    nz = np.where(np.abs(x) > thr)[0]
    lead = float(nz[0] / sr) if len(nz) else 0.0
    trail = float((len(x) - 1 - nz[-1]) / sr) if len(nz) else 0.0
    # 고역 '날카로움' 지표: 2kHz 이상 에너지 비율
    res = {
        "name": name,
        "duration_s": round(len(x) / sr, 3),
        "sr": sr, "bits": bits, "channels": ch,
        "peak_dbfs": round(db(peak), 2),
        "rms_dbfs": round(db(rms), 2),
        "active_rms_dbfs": round(db(active_rms), 2),
        "crest_db": round(db(peak) - db(rms), 2),
        "dc_offset": round(float(np.mean(x)), 5),
        "attack_ms": round(atk_idx * hop / sr * 1000, 1),
        "spk_rms_dbfs": round(db(spk_rms), 2),
        "start_click": round(start_jump, 4),
        "end_click": round(end_jump, 4),
        "onset_jump": round(onset_jump, 4),
        "first_sample": round(float(x[0]), 4),
        "last_sample": round(float(x[-1]), 4),
        "tail50ms_dbfs": round(tail_db, 1),
        "lead_silence_s": round(lead, 3),
        "trail_silence_s": round(trail, 3),
        "clipped_samples": int(np.sum(np.abs(x) >= 0.9999)),
    }
    res.update(spectrum_stats(x, sr))
    return res


def analyze_file(path, name=None):
    x, sr, bits, ch = read_wav(path)
    return analyze_samples(x, sr, bits, ch, name or os.path.splitext(os.path.basename(path))[0])


def legacy_notify_wav():
    """app.py 의 _soft_whistle_wav() 소스만 AST로 떼어 실행(앱 import 없이) → WAV bytes."""
    src = open(os.path.join(PROJ, "app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_soft_whistle_wav")
    mod = ast.Module(body=[fn], type_ignores=[])
    g = {"_WHISTLE_WAV_CACHE": None}
    exec(compile(mod, "app.py:_soft_whistle_wav", "exec"), g)
    return g["_soft_whistle_wav"]()


def system_beep_files():
    """winsound.MessageBeep 폴백이 실제로 재생하는 시스템 사운드 파일(레지스트리 매핑)."""
    out = {}
    try:
        import winreg
        for ev in ("SystemExclamation", "SystemAsterisk", "SystemHand", ".Default"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    rf"AppEvents\Schemes\Apps\.Default\{ev}\.Current") as k:
                    v, _ = winreg.QueryValueEx(k, "")
                    out[ev] = os.path.expandvars(v)
            except OSError:
                out[ev] = ""
    except ImportError:
        pass
    return out


def beep_tone(freq=1200, ms=80, sr=44100):
    """winsound.Beep(1200, 80) 근사(스피커 비프 = 경계 페이드 없는 순음)."""
    t = np.arange(int(sr * ms / 1000)) / sr
    return np.sin(2 * np.pi * freq * t) * 0.5


NAMES = ["night", "day", "trial", "guilty", "innocent", "tick", "citizen_win", "mafia_win"]


def main():
    result = {"old": {}, "new": {}, "legacy_extra": {}}
    old_dir = os.path.join(PROJ, "sounds")
    for n in NAMES:
        p = os.path.join(old_dir, n + ".wav")
        if os.path.isfile(p):
            result["old"][n] = analyze_file(p)
    try:
        x, sr, bits, ch = read_wav_bytes(legacy_notify_wav())
        result["old"]["notify"] = analyze_samples(x, sr, bits, ch, "notify(_soft_whistle_wav)")
    except Exception as e:  # pragma: no cover
        print("notify 분석 실패:", e)
    for ev, path in system_beep_files().items():
        if path and os.path.isfile(path):
            result["legacy_extra"]["MessageBeep:" + ev] = analyze_file(path, f"{ev} → {os.path.basename(path)}")
    result["legacy_extra"]["Beep(1200,80)"] = analyze_samples(beep_tone(), 44100, 16, 1, "Beep(1200,80) 근사")
    for f in sorted(os.listdir(HERE)):
        if f.endswith(".wav"):
            result["new"][f[:-4]] = analyze_file(os.path.join(HERE, f))
    with open(os.path.join(HERE, "analysis.json"), "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    cols = ["duration_s", "sr", "peak_dbfs", "rms_dbfs", "active_rms_dbfs", "spk_rms_dbfs", "crest_db", "attack_ms",
            "start_click", "end_click", "onset_jump", "tail50ms_dbfs", "centroid_hz", "rolloff85_hz", "n_peaks_30db"]
    for sec in ("old", "legacy_extra", "new"):
        print(f"\n=== {sec} ===")
        print("name".ljust(34) + " ".join(c[:10].rjust(10) for c in cols))
        for n, r in result[sec].items():
            print(n[:33].ljust(34) + " ".join(str(r[c]).rjust(10) for c in cols))
        for n, r in result[sec].items():
            print(f"  {n}: bands={r['band_energy_pct']} peaks={r['top_peaks']} dc={r['dc_offset']} "
                  f"lead={r['lead_silence_s']} trail={r['trail_silence_s']} clip={r['clipped_samples']}")
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
