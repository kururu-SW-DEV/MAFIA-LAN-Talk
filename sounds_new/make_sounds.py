"""MAFIA 효과음 재합성 스크립트 (numpy + 표준 라이브러리만, 결정적/seed 고정).

    python make_sounds.py

- 결과: 이 폴더에 *.wav (모노 44.1kHz 16bit), reference/notify_old.wav(기존 알림음 비교용),
  analysis.json(기존/신규 정량 분석), compare.html(나란히 듣기 페이지)
- 외부 샘플·패키지 없이 전부 수식으로 합성 → 라이선스 문제 없음.
- 기존 사운드의 '방향성(음정 윤곽·분위기·길이감)'은 analyze.py로 측정한 값을 근거로 유지했다.
  (예: night 두 번 울림 220→165Hz, day/citizen_win C-E-G-C 상승, guilty 311→233Hz 하강,
   innocent 784→1047Hz 해결, mafia_win 220→175→131Hz 하강, trial 88Hz 타격, tick 1.5kHz)

합성 도구
- 가산 합성(배음별 진폭·감쇠 개별 지정) + 다중 보이스 디튠(코러스)
- ADSR/지수 감쇠 엔벨로프, 배음 밝기가 음량을 따라가는 금관식 엔벨로프
- 종(비정수배 배음), 말렛(비브라폰/마림바), 발현(하프), 팀파니, 나무 타격, 필터 노이즈
- FFT 영역 버터워스형 저역/고역/대역 필터(제로 패딩)
- 직접 구현한 Freeverb형 리버브(댐핑 콤 8 + 올패스 4, 블록 단위 벡터화) + 다중탭 초기 반사
- 마무리: DC/초저역 제거 → '(활성 RMS + 노트북 스피커 근사 RMS)/2' 목표치 정규화 → 룩어헤드 피크 리미터(-1dBFS)
  → 시작 페이드인·끝 페이드아웃(클릭 제거)
"""
import json
import os
import sys
import wave

import numpy as np

import analyze as AZ

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
CEIL_DB = -1.0


# ───────────────────────────── 기본 유틸 ─────────────────────────────
def n_of(sec):
    return int(round(sec * SR))


def buf(sec):
    return np.zeros(n_of(sec))


def add(dst, src, at=0.0, gain=1.0):
    """dst의 at초 위치에 src를 더한다(넘치는 부분은 자름)."""
    i = n_of(at)
    if i >= len(dst):
        return dst
    m = min(len(src), len(dst) - i)
    dst[i:i + m] += src[:m] * gain
    return dst


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def cents(c):
    return 2 ** (c / 1200)


def raised_cos(n):
    return 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, max(n, 1)))


def adsr(total, a=0.01, d=0.1, s=0.7, gate=None, r=0.2, curve=3.0):
    """a/d/r 초, s 레벨. gate(노트 길이) 뒤에 지수형 release. total 길이 배열 반환."""
    N = n_of(total)
    t = np.arange(N) / SR
    gate = total - r if gate is None else gate
    env = np.empty(N)
    na = t < a
    env[na] = raised_cos(n_of(a) + 1)[: na.sum()] if na.any() else env[na]
    nd = (t >= a) & (t < gate)
    env[nd] = s + (1 - s) * np.exp(-(t[nd] - a) / max(d, 1e-4) * 1.0)
    g_level = s + (1 - s) * np.exp(-(max(gate - a, 0)) / max(d, 1e-4))
    nr = t >= gate
    env[nr] = g_level * np.exp(-(t[nr] - gate) / max(r, 1e-4) * curve)
    return env


def expdec(total, tau, attack=0.002):
    N = n_of(total)
    t = np.arange(N) / SR
    env = np.exp(-t / tau)
    na = n_of(attack)
    if na > 1:
        env[:na] *= raised_cos(na)
    return env


# ───────────────────────────── 필터 (FFT 영역) ─────────────────────────────
def fft_filter(x, lp=None, hp=None, order=2, pad_s=0.25):
    """버터워스 크기 응답을 FFT에 곱한다(제로 위상). 끝에 여유를 붙여 원형 겹침 방지."""
    pad = n_of(pad_s)
    n = len(x) + pad
    nfft = 1 << int(np.ceil(np.log2(n)))
    X = np.fft.rfft(np.concatenate([x, np.zeros(nfft - len(x))]))
    f = np.fft.rfftfreq(nfft, 1 / SR)
    H = np.ones_like(f)
    if lp:
        H *= 1 / np.sqrt(1 + (f / lp) ** (2 * order))
    if hp:
        with np.errstate(divide="ignore"):
            H *= 1 / np.sqrt(1 + (hp / np.maximum(f, 1e-9)) ** (2 * order))
        H[0] = 0
    y = np.fft.irfft(X * H, nfft)
    return y[: len(x)]


def tilt(x, db_per_oct, pivot=1000.0):
    """스펙트럼 기울기(+ 밝게 / - 어둡게)."""
    nfft = 1 << int(np.ceil(np.log2(len(x) + n_of(0.1))))
    X = np.fft.rfft(np.concatenate([x, np.zeros(nfft - len(x))]))
    f = np.maximum(np.fft.rfftfreq(nfft, 1 / SR), 20)
    H = 10 ** (db_per_oct * np.log2(f / pivot) / 20)
    return np.fft.irfft(X * H, nfft)[: len(x)]


# ───────────────────────────── 리버브 / 에코 ─────────────────────────────
def _comb(x, D, g, damp):
    """피드백 콤(피드백 경로에 2탭 저역 필터). 블록 길이 D로 벡터화 — 정확한 재귀와 동일."""
    y = np.zeros(len(x))
    for s in range(0, len(x), D):
        e = min(s + D, len(x))
        y[s:e] = x[s:e]
        if s >= D + 1:
            fb = (1 - damp) * y[s - D:e - D] + damp * y[s - D - 1:e - D - 1]
            y[s:e] += g * fb
        elif s >= D:
            y[s:e] += g * y[s - D:e - D]
    return y


def _allpass(x, D, g=0.5):
    y = np.zeros(len(x))
    for s in range(0, len(x), D):
        e = min(s + D, len(x))
        y[s:e] = -g * x[s:e]
        if s >= D:
            y[s:e] += x[s - D:e - D] + g * y[s - D:e - D]
    return y


def reverb(x, t60=1.2, size=1.0, damp=0.35, wet=0.25, predelay=0.012, tail=None, hp=180, lp=6500):
    """Freeverb 구조 모노 리버브. t60: 잔향 60dB 감쇠 시간(초). tail: 꼬리용으로 늘릴 길이."""
    tail = t60 * 0.6 if tail is None else tail
    xin = np.concatenate([np.zeros(n_of(predelay)), x, np.zeros(n_of(tail))])
    xin = fft_filter(xin, lp=lp, hp=hp, order=1)          # 잔향은 저역 뭉침·고역 치찰 없이
    combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
    out = np.zeros(len(xin))
    for c in combs:
        D = max(8, int(c * size * SR / 44100))
        g = 10 ** (-3 * D / (t60 * SR))
        out += _comb(xin, D, g, damp)
    out /= len(combs)
    for a in (556, 441, 341, 225):
        out = _allpass(out, max(4, int(a * size)))
    dry = np.concatenate([x, np.zeros(len(out) - len(x))])
    return dry * (1 - wet * 0.5) + out * wet * 2.2


def echo_taps(x, taps):
    """다중탭 초기 반사: taps=[(지연초, 게인, 저역컷Hz or None), ...]"""
    L = len(x) + n_of(max(t for t, _, _ in taps)) + 1
    y = np.zeros(L)
    y[: len(x)] += x
    for d, g, lp in taps:
        src = fft_filter(x, lp=lp, order=1) if lp else x
        add(y, src, d, g)
    return y


# ───────────────────────────── 음원(보이스) ─────────────────────────────
class Rng:
    def __init__(self, seed):
        self.r = np.random.default_rng(seed)

    def ph(self):
        return float(self.r.uniform(0, 2 * np.pi))

    def noise(self, sec):
        return self.r.standard_normal(n_of(sec))


def additive(f0, sec, partials, rng, env=None, voices=1, detune=0.0, vib=None, glide=None, bright=0.0):
    """partials: [(배수, 진폭, 추가감쇠(1/s)), ...]
    voices/detune: 코러스(±detune 센트에 고르게 분산), vib=(rate Hz, depth cents, delay s)
    glide=(시작 배수, 시간초): 시작 피치에서 f0로 지수 수렴. bright: 배음이 env^(1+bright*k) 로 따라감."""
    N = n_of(sec)
    t = np.arange(N) / SR
    env = np.ones(N) if env is None else env[:N]
    out = np.zeros(N)
    offs = [0.0] if voices == 1 else list(np.linspace(-detune, detune, voices))
    for vo in offs:
        fr = np.full(N, f0 * cents(vo))
        if glide:
            fr *= 1 + (glide[0] - 1) * np.exp(-t / max(glide[1], 1e-4))
        if vib:
            rate, depth, delay = vib
            ramp = np.clip((t - delay) / 0.15, 0, 1)
            fr *= cents(depth * ramp * np.sin(2 * np.pi * rate * t + rng.ph()))
        phase = 2 * np.pi * np.cumsum(fr) / SR
        for k, (ratio, amp, dec) in enumerate(partials):
            if f0 * ratio > SR * 0.45:
                continue
            e = env ** (1 + bright * k) if bright else env
            p = np.sin(ratio * phase + rng.ph()) * amp * e
            if dec:
                p *= np.exp(-t * dec)
            out += p
    return out / np.sqrt(len(offs))


def saw_partials(n=24, roll=1.0, odd_boost=0.0, dec_step=0.0):
    return [(h, (1 / h ** roll) * (1 + (odd_boost if h % 2 else 0)), dec_step * (h - 1)) for h in range(1, n + 1)]


def bell(f_strike, sec, rng, amp=1.0, dark=0.0, tau=1.6):
    """교회 종형 비정수배 배음(hum 0.5, prime 1, tierce 1.19, quint 1.5, nominal 2, ...).
    f_strike = prime(귀에 들리는 타격음). dark>0이면 고배음을 줄인다."""
    table = [  # ratio, amp, tau 배율
        (0.5, 0.55, 1.6), (1.0, 1.0, 1.0), (1.183, 0.45, 0.8), (1.506, 0.30, 0.55),
        (2.0, 0.55, 0.5), (2.514, 0.22, 0.35), (2.662, 0.18, 0.30), (3.011, 0.16, 0.25),
        (4.166, 0.10, 0.18), (5.433, 0.06, 0.12), (6.79, 0.035, 0.09),
    ]
    N = n_of(sec)
    t = np.arange(N) / SR
    out = np.zeros(N)
    for r, a, tm in table:
        f = f_strike * r
        if f > SR * 0.45:
            continue
        a2 = a * (1 / (1 + dark * max(0, r - 1) ** 1.5))
        # 미세 디튠 쌍 → 종 특유의 맥놀이(beating)
        for dc in (-1.5, 1.5):
            out += np.sin(2 * np.pi * f * cents(dc * r ** 0.5) * t + rng.ph()) * a2 * 0.5 * np.exp(-t / (tau * tm))
    # 타격 순간의 금속성 트랜지언트
    click = fft_filter(rng.noise(0.012) * expdec(0.012, 0.003), hp=f_strike * 2, lp=min(9000, f_strike * 10))
    out[: len(click)] += click * 0.25 * (1 - dark * 0.5)
    atk = n_of(0.0015)
    out[:atk] *= raised_cos(atk)
    return out * amp


def mallet(f0, sec, rng, kind="vibes", tau=0.9, amp=1.0):
    """말렛 타악(비브라폰/마림바/첼레스타). 막대의 비정수배 모드."""
    tables = {
        "vibes": [(1, 1.0, 1.0), (3.98, 0.28, 0.25), (9.3, 0.05, 0.08)],
        "marimba": [(1, 1.0, 1.0), (3.93, 0.35, 0.18), (9.1, 0.10, 0.06), (2.0, 0.06, 0.5)],
        "celesta": [(1, 1.0, 1.0), (2.0, 0.32, 0.45), (3.0, 0.10, 0.25), (5.2, 0.05, 0.1)],
        "glock": [(1, 1.0, 1.0), (2.76, 0.30, 0.35), (5.40, 0.14, 0.15), (8.93, 0.06, 0.07)],
    }
    N = n_of(sec)
    t = np.arange(N) / SR
    out = np.zeros(N)
    for r, a, tm in tables[kind]:
        if f0 * r > SR * 0.45:
            continue
        out += np.sin(2 * np.pi * f0 * r * t + rng.ph()) * a * np.exp(-t / (tau * tm))
    hit = fft_filter(rng.noise(0.006) * expdec(0.006, 0.0015), hp=1500, lp=7000)
    out[: len(hit)] += hit * 0.12
    atk = n_of(0.0012)
    out[:atk] *= raised_cos(atk)
    return out * amp


def pluck(f0, sec, rng, tau=0.8, bright=1.0, amp=1.0, n=14):
    """하프/기타형 발현음: 1/h^1.3 배음, 고배음일수록 빨리 감쇠."""
    parts = [(h, (1 / h ** (1.6 - 0.4 * bright)) * (0.85 if h == 2 else 1), (h - 1) * 3.5 / bright) for h in range(1, n + 1)]
    env = expdec(sec, tau, 0.002)
    return additive(f0, sec, parts, rng, env=env, voices=2, detune=2.5) * amp


def brass(f0, sec, rng, gate, amp=1.0, a=0.03, r=0.18, voices=3, detune=7.0, bright=0.9, n=18, s=0.8, vib=True):
    """금관(트럼펫/호른)풍: 톱니 배음 + 밝기 엔벨로프(세게 불수록 고배음 증가) + 지연 비브라토."""
    env = adsr(sec, a=a, d=0.12, s=s, gate=gate, r=r)
    parts = [(h, 1 / h ** 0.95, 0.0) for h in range(1, n + 1)]
    v = additive(f0, sec, parts, rng, env=env, voices=voices, detune=detune,
                 vib=(5.2, 9, 0.18) if vib else None, bright=bright / n * 6, glide=(0.985, 0.02))
    return v * amp


def pad(freqs, sec, rng, a=0.3, r=0.4, gate=None, lp=2200, voices=4, detune=9.0, amp=1.0, roll=1.1):
    """스트링/오르간 패드: 톱니 배음 × 다중 디튠 + 저역통과."""
    env = adsr(sec, a=a, d=0.4, s=0.85, gate=gate, r=r, curve=2.5)
    out = np.zeros(n_of(sec))
    for f in freqs:
        out += additive(f, sec, saw_partials(int(min(28, SR * 0.45 / f)), roll=roll), rng, env=env,
                        voices=voices, detune=detune, vib=(4.6, 4, 0.2))
    return fft_filter(out, lp=lp, order=2) * amp / max(1, len(freqs)) ** 0.5


def timpani(f0, sec, rng, amp=1.0, tau=0.9):
    modes = [(1.0, 1.0, 1.0), (1.504, 0.55, 0.7), (1.742, 0.25, 0.5), (2.0, 0.35, 0.55), (2.245, 0.18, 0.4), (2.494, 0.12, 0.35)]
    N = n_of(sec)
    t = np.arange(N) / SR
    bend = 1 + 0.04 * np.exp(-t / 0.05)       # 타격 직후 살짝 높은 피치 → 내려앉음
    ph = 2 * np.pi * np.cumsum(f0 * bend) / SR
    out = np.zeros(N)
    for r, a, tm in modes:
        out += np.sin(r * ph + rng.ph()) * a * np.exp(-t / (tau * tm))
    stick = fft_filter(rng.noise(0.03) * expdec(0.03, 0.007), hp=120, lp=2500)
    out[: len(stick)] += stick * 0.35
    atk = n_of(0.002)
    out[:atk] *= raised_cos(atk)
    return out * amp


def wood_knock(f0, sec, rng, amp=1.0, tau=0.05, noise=0.5):
    """나무 타격(판사봉 머리·우드블록): 비정수배 모드 빠른 감쇠 + 짧은 노이즈."""
    modes = [(1.0, 1.0, 1.0), (2.32, 0.55, 0.6), (3.87, 0.3, 0.4), (5.61, 0.15, 0.3)]
    N = n_of(sec)
    t = np.arange(N) / SR
    out = np.zeros(N)
    for r, a, tm in modes:
        if f0 * r < SR * 0.45:
            out += np.sin(2 * np.pi * f0 * r * t + rng.ph()) * a * np.exp(-t / (tau * tm))
    nz = fft_filter(rng.noise(0.01) * expdec(0.01, 0.0018, 0.0003), hp=f0 * 1.2, lp=f0 * 8)
    out[: len(nz)] += nz * noise
    atk = n_of(0.0006)
    out[:atk] *= raised_cos(atk)
    return out * amp


def thump(f_start, f_end, sec, rng, tau=0.12, amp=1.0):
    """피치가 떨어지는 저역 타격(킥/바닥 울림)."""
    N = n_of(sec)
    t = np.arange(N) / SR
    fr = f_end + (f_start - f_end) * np.exp(-t / 0.025)
    ph = 2 * np.pi * np.cumsum(fr) / SR
    out = (np.sin(ph) + 0.35 * np.sin(2 * ph + 0.3) + 0.12 * np.sin(3 * ph)) * np.exp(-t / tau)
    atk = n_of(0.0015)
    out[:atk] *= raised_cos(atk)
    return out * amp


def swell_noise(sec, rng, lo, hi, a_frac=0.8, amp=1.0):
    """역방향 스웰(서서히 커지는 필터 노이즈) — 긴장 고조/등장감."""
    x = fft_filter(rng.noise(sec), hp=lo, lp=hi, order=2)
    N = len(x)
    env = np.linspace(0, 1, N) ** 2.2
    na = int(N * a_frac)
    env[na:] = np.linspace(env[na - 1] if na else 1, 0, N - na) ** 0.6 * (env[na - 1] if na else 1)
    return x * env * amp


# ───────────────────────────── 마스터링 ─────────────────────────────
def active_rms_db(x):
    """정규화 기준 음량 = (활성 RMS + 노트북 스피커 근사 RMS)/2.
    활성 RMS만 맞추면 저역 위주의 소리(밤 드론·망치·마피아 승리)가 작은 스피커에서 3~6dB 작게 들린다.
    두 값을 섞어 헤드폰/스피커와 노트북 스피커 어느 쪽에서도 편차가 작게 한다."""
    m = AZ.analyze_samples(x, SR)
    return 0.5 * (m["active_rms_dbfs"] + m["spk_rms_dbfs"])


def limiter(x, ceil_db=CEIL_DB, look_ms=3.0, rel_ms=80.0):
    ceil = 10 ** (ceil_db / 20)
    a = np.abs(x)
    g = np.minimum(1.0, ceil / np.maximum(a, 1e-12))
    w = max(1, n_of(look_ms / 1000))
    gp = np.concatenate([g, np.ones(w - 1)])
    gmin = np.lib.stride_tricks.sliding_window_view(gp, w).min(axis=1)   # 룩어헤드 최소값
    # 어택은 룩어헤드 구간에서 코사인 램프, 릴리즈는 1극 평활
    rel = np.exp(-1 / (SR * rel_ms / 1000))
    out = np.empty_like(gmin)
    cur = 1.0
    for i, v in enumerate(gmin):
        cur = v if v < cur else v + (cur - v) * rel
        out[i] = cur
    y = x * out
    return np.clip(y, -ceil, ceil)


GAIN_OFFSET_DB = 0.0     # --gain-db N 으로 전체 목표 음량을 N dB 올리거나 내림(피크는 여전히 -1dBFS 제한)
LAST_GR = {"db": 0.0}


def finish(x, target_db, fade_in=0.002, fade_out=0.04, hp=28.0):
    target_db = target_db + GAIN_OFFSET_DB
    x = fft_filter(x, hp=hp, order=2)                  # DC·초저역 제거
    x = x - np.mean(x)
    x = x * 10 ** ((target_db - active_rms_db(x)) / 20)
    LAST_GR["db"] = max(0.0, AZ.db(np.max(np.abs(x))) - CEIL_DB)   # 리미터가 깎아야 하는 양(참고)
    for _ in range(4):                                  # 정규화 ↔ 리미터 수렴
        x = limiter(x)
        x = x * 10 ** ((target_db - active_rms_db(x)) / 20)
    x = limiter(x)
    ni, no = n_of(fade_in), n_of(fade_out)
    x[:ni] *= raised_cos(ni)
    x[-no:] *= raised_cos(no)[::-1]
    x[0] = 0.0
    x[-1] = 0.0
    return x


def trim_to(x, sec):
    N = n_of(sec)
    return x[:N] if len(x) >= N else np.concatenate([x, np.zeros(N - len(x))])


def write_wav(path, x):
    q = np.clip(np.round(x * 32767), -32767, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(q.tobytes())


# ───────────────────────────── 개별 사운드 ─────────────────────────────
# 음이름 → 주파수 (MIDI)
C3, D3, Eb3, E3, F3, G3, Ab3, A3, Bb3, B3 = (midi(m) for m in (48, 50, 51, 52, 53, 55, 56, 57, 58, 59))
C4, D4, Eb4, E4, F4, G4, A4, B4 = (midi(m) for m in (60, 62, 63, 64, 65, 67, 69, 71))
C5, D5, E5, G5, A5, C6, E6 = (midi(m) for m in (72, 74, 76, 79, 81, 84, 88))


def s_night():
    """어둡고 긴장된 저음 드론 + 멀리서 두 번 울리는 작은 종(기존 220→165Hz 두 번 울림 윤곽 유지)."""
    r = Rng(101)
    L = 1.65
    out = buf(L)
    # 드론: E2 + B2 (완전5도) + 아주 작게 F3(단2도 긴장), 느린 스웰
    drone = pad([midi(40), midi(47)], L, r, a=0.35, r=0.45, gate=L - 0.45, lp=520, voices=5, detune=11, roll=1.0)
    trem = 1 + 0.12 * np.sin(2 * np.pi * 3.1 * np.arange(len(drone)) / SR)          # 불안한 미세 트레몰로
    add(out, fft_filter(drone * trem, hp=150, order=1), 0, 0.8)   # 기음은 줄이고 2~4배음 위주 → 노트북 스피커에서도 들림
    add(out, pad([midi(53)], L, r, a=0.6, r=0.4, gate=L - 0.5, lp=900, voices=3, detune=14), 0.15, 0.12)
    # 바람결 같은 필터 노이즈
    wind = fft_filter(r.noise(L), hp=250, lp=900) * adsr(L, a=0.5, d=0.5, s=0.7, gate=L - 0.5, r=0.4)
    add(out, wind, 0, 0.05)
    # 종: 타격음 A4→E4 (hum = 220 / 165Hz → 기존 음높이 그대로)
    b1 = bell(A4, 1.3, r, dark=0.9, tau=1.1)
    b2 = bell(E4, 1.0, r, dark=0.9, tau=1.0)
    bells = buf(L)
    add(bells, b1, 0.02, 2.0)          # 스플래시가 뜨는 순간 첫 종이 또렷하게(드론은 뒤에서 차오름)
    add(bells, b2, 0.55, 1.6)
    bells = fft_filter(bells, lp=3200, order=2)         # '멀리서' 들리게 고역 감쇠
    out += reverb(bells, t60=2.2, size=1.25, wet=0.3, predelay=0.03, tail=0)[: len(out)]
    out = reverb(out, t60=1.6, size=1.1, wet=0.18, tail=0)[: len(out)]
    return finish(out, -18.0, fade_in=0.004, fade_out=0.12)


def s_day():
    """밝은 아침: 첼레스타·글로켄 C5-E5-G5-C6 상승(기존 아르페지오 음 그대로) + 따뜻한 장3화음 패드."""
    r = Rng(202)
    L = 0.95
    out = buf(L)
    padv = pad([C4, E4, G4], L, r, a=0.12, r=0.3, gate=0.5, lp=2800, voices=4, detune=8, roll=1.25)
    add(out, padv, 0, 0.55)
    notes = [(C5, 0.0), (E5, 0.11), (G5, 0.22), (C6, 0.33)]
    for i, (f, at) in enumerate(notes):
        add(out, mallet(f, L - at, r, "celesta", tau=0.5 + 0.1 * i), at, 0.55 + 0.1 * i)
        add(out, mallet(f * 2, L - at, r, "glock", tau=0.35), at + 0.004, 0.12)   # 옥타브 위 반짝임
    shimmer = fft_filter(r.noise(L), hp=6000, lp=11000) * adsr(L, a=0.3, d=0.2, s=0.5, gate=0.5, r=0.25)
    add(out, shimmer, 0.05, 0.02)
    out = reverb(out, t60=1.3, size=0.95, wet=0.28, tail=0)[: len(out)]
    return finish(out, -18.0, fade_out=0.3)


def s_trial():
    """재판 망치: 88Hz 바닥 울림(기존 기음 유지) + 단단한 나무 머리 타격 + 짧은 법정 잔향."""
    r = Rng(303)
    L = 0.5
    out = buf(L)
    add(out, thump(150, 88, L, r, tau=0.11), 0, 0.8)
    add(out, wood_knock(410, L, r, tau=0.06, noise=0.7), 0, 1.0)       # 봉 머리
    add(out, wood_knock(260, L, r, tau=0.09, noise=0.2), 0.001, 0.65)  # 받침(sound block)
    add(out, timpani(G3 / 2, L, r, tau=0.25), 0, 0.25)                 # 묵직한 몸통감
    out = echo_taps(out, [(0.019, 0.22, 3000), (0.031, 0.16, 2400), (0.047, 0.10, 1800)])
    out = reverb(out, t60=0.9, size=0.8, wet=0.2, tail=0)[: n_of(L)]
    return finish(trim_to(out, L), -18.0, fade_in=0.0008, fade_out=0.12)


def s_guilty():
    """불길한 하강 화음: E♭단조(윗성부 E♭4) → B♭ 감화음(윗성부 B♭3) — 기존 311→233Hz 하강 유지.
    낮은 금관 + 팀파니, 두 번째 화음에서 무게를 싣는다."""
    r = Rng(404)
    L = 0.98
    out = buf(L)
    c1 = [Eb3, midi(54), Bb3, Eb4]                   # Eb Gb Bb Eb
    c2 = [midi(46), midi(49), midi(52), Bb3]         # Bb Db Fb(=E) Bb  (감5도 → 불안)
    for f in c1:
        add(out, brass(f, 0.3, r, gate=0.17, a=0.02, r=0.1, detune=8, bright=0.6), 0.0, 0.26)
    for f in c2:
        add(out, brass(f, L - 0.2, r, gate=0.42, a=0.035, r=0.3, detune=10, bright=0.7), 0.2, 0.3)
    add(out, pad([midi(34)], L - 0.2, r, a=0.05, r=0.3, gate=0.45, lp=400, voices=3), 0.2, 0.35)   # Bb1 베이스
    add(out, timpani(midi(34) * 2, L - 0.2, r, tau=0.6), 0.2, 0.55)
    add(out, timpani(Eb3 / 2, 0.3, r, tau=0.3), 0.0, 0.25)
    out = fft_filter(out, lp=3500, order=2)
    out = reverb(out, t60=1.5, size=1.1, wet=0.22, tail=0)[: len(out)]
    return finish(out, -18.0, fade_out=0.15)


def s_innocent():
    """맑은 해소: G sus4 → C장조(윗성부 G5→C6, 기존 784→1047Hz 유지). 하프 발현 + 부드러운 패드."""
    r = Rng(505)
    L = 0.88
    out = buf(L)
    add(out, pad([G3, C4, D4], 0.3, r, a=0.04, r=0.12, gate=0.18, lp=2200, voices=3), 0.0, 0.4)
    add(out, pad([C4, E4, G4, C5], L - 0.17, r, a=0.06, r=0.35, gate=0.25, lp=2600, voices=4), 0.17, 0.5)
    for f, at, g in [(D5, 0.0, 0.3), (G5, 0.012, 0.45)]:
        add(out, pluck(f, 0.4, r, tau=0.35, bright=1.1), at, g)
    for f, at, g in [(E5, 0.17, 0.3), (G5, 0.18, 0.3), (C6, 0.19, 0.55)]:
        add(out, pluck(f, L - at, r, tau=0.6, bright=1.2), at, g)
    add(out, mallet(C6 * 2, L - 0.19, r, "celesta", tau=0.4), 0.195, 0.08)
    out = reverb(out, t60=1.4, size=1.0, wet=0.3, tail=0)[: len(out)]
    return finish(out, -18.0, fade_out=0.3)


def s_tick():
    """초읽기: 시계 '딱' — 1.5kHz 부근 나무 모드(기존 중심 유지), 짧고 또렷하게, 클릭 없는 어택."""
    r = Rng(606)
    L = 0.06
    out = buf(L)
    add(out, wood_knock(1480, L, r, tau=0.012, noise=0.35), 0, 1.0)
    add(out, wood_knock(740, L, r, tau=0.01, noise=0.0), 0, 0.25)   # 살짝 몸통
    out = echo_taps(out, [(0.006, 0.18, 4000), (0.011, 0.1, 3000)])[: n_of(L)]
    return finish(out, -17.0, fade_in=0.001, fade_out=0.012, hp=120)


def s_citizen_win():
    """시민 승리 팡파르(C장조): 금관 C5-E5-G5 → C6 장화음 + 팀파니 롤 착지. 기존 상승 윤곽 유지."""
    r = Rng(707)
    L = 1.2
    out = buf(L)
    lead = [(G4, 0.0, 0.09), (C5, 0.1, 0.09), (E5, 0.2, 0.09), (G5, 0.3, 0.09)]
    for f, at, g in lead:
        add(out, brass(f, 0.3, r, gate=g, a=0.015, r=0.08, detune=6, bright=1.0), at, 0.34)
    # 착지 화음 (0.42s~)
    for f, g in [(C4, 0.22), (E4, 0.2), (G4, 0.22), (C5, 0.26), (E5, 0.2), (C6, 0.3)]:
        add(out, brass(f, L - 0.42, r, gate=0.42, a=0.03, r=0.3, detune=8, bright=1.0), 0.42, g)
    add(out, timpani(C3, L - 0.42, r, tau=0.7), 0.42, 0.55)
    for k in range(4):                                   # 짧은 팀파니 롤 → 착지
        add(out, timpani(G3 / 2 * 2, 0.2, r, tau=0.12), 0.3 + k * 0.03, 0.12 + 0.04 * k)
    cym = fft_filter(r.noise(L - 0.42), hp=5000, lp=12000) * expdec(L - 0.42, 0.35, 0.004)
    add(out, cym, 0.42, 0.06)
    out = reverb(out, t60=1.7, size=1.15, wet=0.26, tail=0)[: len(out)]
    return finish(out, -15.0, fade_out=0.15)


def s_mafia_win():
    """마피아 승리: 저음 금관·오르간 단조 하강 Dm(A3) → B♭(F3) → Fm(C3)(기존 220→175→131Hz 유지)
    + 낮은 종 한 번 + 저역 붐. 어둡고 위압적인 마무리."""
    r = Rng(808)
    L = 1.35
    out = buf(L)
    chords = [
        (0.00, 0.26, [midi(38), midi(45), midi(50), midi(53), A3]),        # Dm: D2 A2 D3 F3 A3
        (0.28, 0.26, [midi(34), midi(41), midi(46), midi(50), F3]),        # Bb: Bb1 F2 Bb2 D3 F3
        (0.56, 0.52, [midi(29), midi(36), midi(41), midi(44), C3]),        # Fm: F1 C2 F2 Ab2 C3
    ]
    for at, gate, fs in chords:
        dur = L - at
        for f in fs:
            add(out, brass(f, dur, r, gate=gate, a=0.03, r=0.25 if at < 0.5 else 0.45, detune=11,
                           bright=0.55, n=22, s=0.85), at, 0.22)
        add(out, pad(fs[:2], dur, r, a=0.02, r=0.3, gate=gate, lp=500, voices=3), at, 0.35)
    add(out, timpani(midi(29) * 2, L - 0.56, r, tau=0.8), 0.56, 0.7)
    add(out, thump(90, 44, L - 0.56, r, tau=0.35), 0.56, 0.6)
    add(out, fft_filter(bell(C4, L - 0.56, r, dark=1.2, tau=1.2), lp=2500), 0.58, 0.22)
    out = fft_filter(out, lp=3000, order=2)
    out = reverb(out, t60=2.0, size=1.25, wet=0.28, tail=0)[: len(out)]
    return finish(out, -15.0, fade_out=0.3)


# ── 새로 제안하는 사운드 ──
def s_notify():
    """새 메시지 '띵동': 기존 1300→780Hz 도어벨 리듬 유지(E6→G5), 순음 대신 비브라폰 음색 + 은은한 잔향."""
    r = Rng(909)
    L = 0.85
    out = buf(L)
    add(out, mallet(E6, 0.5, r, "vibes", tau=0.22), 0.0, 0.6)
    add(out, mallet(G5, L - 0.2, r, "vibes", tau=0.55), 0.2, 0.75)
    add(out, mallet(G5 / 2, L - 0.2, r, "vibes", tau=0.4), 0.2, 0.12)     # 아래 옥타브로 따뜻함
    trem = 1 + 0.08 * np.sin(2 * np.pi * 5.5 * np.arange(len(out)) / SR)  # 비브라폰 모터 트레몰로
    out = reverb(out * trem, t60=1.0, size=0.8, wet=0.2, tail=0)[: len(out)]
    return finish(out, -20.0, fade_out=0.22)


def s_vote_open():
    """투표 개시: 작은 핸드벨 두 번(A4→E5 상행 5도) + 낮은 드럼 한 번 — '지금 결정하라' 신호."""
    r = Rng(1010)
    L = 0.8
    out = buf(L)
    add(out, bell(A4, 0.6, r, dark=0.6, tau=0.6), 0.0, 0.4)
    add(out, bell(E5, 0.55, r, dark=0.6, tau=0.55), 0.14, 0.42)
    add(out, timpani(A3 / 2, 0.5, r, tau=0.25), 0.0, 0.35)
    out = reverb(out, t60=1.1, size=0.9, wet=0.22, tail=0)[: len(out)]
    return finish(out, -19.0, fade_out=0.15)


def s_vote_cast():
    """투표 접수: 종이 투표용지가 함에 '톡' 떨어지는 부드러운 나무 소리 + 짧은 확인 음."""
    r = Rng(1111)
    L = 0.34
    out = buf(L)
    paper = fft_filter(r.noise(0.05), hp=500, lp=4000) * expdec(0.05, 0.012, 0.003)
    add(out, paper, 0, 0.25)
    add(out, wood_knock(330, L, r, tau=0.045, noise=0.25), 0.012, 0.8)
    add(out, thump(160, 110, 0.15, r, tau=0.04), 0.012, 0.35)
    add(out, mallet(G5, L - 0.05, r, "marimba", tau=0.25), 0.05, 0.22)
    out = reverb(out, t60=0.6, size=0.7, wet=0.15, tail=0)[: len(out)]
    return finish(out, -21.0, fade_in=0.001, fade_out=0.12)


def s_death():
    """사망 통보: 낮은 조종(弔鐘) 한 번 + 어두운 드론 스웰. 재판 망치음 재사용 대신 전용 음."""
    r = Rng(1212)
    L = 1.5
    out = buf(L)
    add(out, bell(A3, L, r, dark=0.7, tau=1.4), 0.0, 0.8)          # hum 110Hz
    add(out, thump(80, 55, 0.6, r, tau=0.25), 0.0, 0.35)
    add(out, pad([midi(45), midi(52), midi(48)], L, r, a=0.25, r=0.5, gate=L - 0.5, lp=600, voices=4, detune=12),
        0.0, 0.45)                                               # A2 E3 C3 (단3화음 저역)
    out = reverb(out, t60=2.4, size=1.3, wet=0.3, tail=0)[: len(out)]
    return finish(out, -18.0, fade_out=0.2)


def s_role_reveal():
    """직업 공개: 카드가 뒤집히듯 짧은 역방향 스웰 → 신비로운 D단조add9 발현 화음 + 저역 붐."""
    r = Rng(1313)
    L = 1.05
    out = buf(L)
    add(out, swell_noise(0.28, r, 1200, 7000, a_frac=0.95), 0.0, 0.18)
    add(out, pad([D4, A4], 0.3, r, a=0.25, r=0.05, gate=0.25, lp=1800, voices=3), 0.0, 0.18)
    hit = 0.27
    for i, f in enumerate([D3, A3, D4, F4, A4, E5]):
        add(out, pluck(f, L - hit, r, tau=0.55, bright=1.0), hit + i * 0.018, 0.28)
    add(out, mallet(D5 * 2, L - hit, r, "celesta", tau=0.5), hit + 0.1, 0.07)
    add(out, thump(110, 60, 0.5, r, tau=0.18), hit, 0.35)
    add(out, pad([D3, A3, F4], L - hit, r, a=0.05, r=0.35, gate=0.35, lp=1500, voices=4), hit, 0.3)
    out = reverb(out, t60=1.8, size=1.15, wet=0.3, tail=0)[: len(out)]
    return finish(out, -18.0, fade_out=0.14)


def s_night_action():
    """밤 행동 확정: 낮게 눌린 발현음(뮤트) + 짧은 어두운 바람 — 은밀하게 '봉인'되는 느낌."""
    r = Rng(1414)
    L = 0.45
    out = buf(L)
    add(out, pluck(A3 / 2, L, r, tau=0.12, bright=0.6), 0, 0.6)
    add(out, pluck(E3, L, r, tau=0.1, bright=0.6), 0.03, 0.35)
    add(out, swell_noise(0.2, r, 300, 1500, a_frac=0.3), 0.0, 0.12)
    add(out, thump(90, 60, 0.3, r, tau=0.08), 0.0, 0.25)
    out = fft_filter(out, lp=2200)
    out = reverb(out, t60=1.2, size=1.0, wet=0.25, tail=0)[: len(out)]
    return finish(out, -21.0, fade_out=0.08)


def s_recruit():
    """참가자 모집 시작: 부드러운 호른 콜 G4-C5-E5(모이라는 신호), 은은한 잔향."""
    r = Rng(1515)
    L = 0.8
    out = buf(L)
    for f, at, gate in [(G4, 0.0, 0.1), (C5, 0.12, 0.1), (E5, 0.24, 0.3)]:
        add(out, brass(f, L - at, r, gate=gate, a=0.035, r=0.18, detune=6, bright=0.4, n=12), at, 0.4)
    add(out, pad([C4, G4], L - 0.24, r, a=0.1, r=0.3, gate=0.25, lp=1500, voices=3), 0.24, 0.22)
    out = fft_filter(out, lp=4000)
    out = reverb(out, t60=1.3, size=1.0, wet=0.24, tail=0)[: len(out)]
    return finish(out, -19.0, fade_out=0.1)


def s_join():
    """참가 신청 접수: 상행 2음 말렛(E5→A5) — 가볍고 긍정적."""
    r = Rng(1616)
    L = 0.52
    out = buf(L)
    add(out, mallet(E5, 0.4, r, "marimba", tau=0.3), 0.0, 0.6)
    add(out, mallet(A5, L - 0.08, r, "marimba", tau=0.35), 0.08, 0.7)
    out = reverb(out, t60=0.8, size=0.8, wet=0.18, tail=0)[: len(out)]
    return finish(out, -21.0, fade_out=0.2)


def s_leave():
    """참가 취소/방 나가기: 하행 2음(A4→E4) 어두운 말렛 — 조용한 퇴장."""
    r = Rng(1717)
    L = 0.58
    out = buf(L)
    add(out, mallet(A4, 0.4, r, "marimba", tau=0.3), 0.0, 0.6)
    add(out, mallet(E4, L - 0.1, r, "marimba", tau=0.4), 0.1, 0.7)
    out = fft_filter(out, lp=2500)
    out = reverb(out, t60=0.9, size=0.85, wet=0.2, tail=0)[: len(out)]
    return finish(out, -21.0, fade_out=0.22)


SOUNDS = {
    # 이름: (함수, 설계 의도 한 줄, 기존 비교 대상(../sounds 또는 reference/ 상대경로) 또는 None)
    "night": (s_night, "E2·B2 저음 드론(미세 트레몰로·단2도 긴장) 위로 멀리서 A4→E4 작은 종 두 번(기존 220→165Hz 윤곽).", "../sounds/night.wav"),
    "day": (s_day, "첼레스타 C5-E5-G5-C6 상승(기존 음 그대로) + 따뜻한 C장조 패드와 옥타브 위 반짝임.", "../sounds/day.wav"),
    "trial": (s_trial, "88Hz 바닥 울림 + 단단한 판사봉 나무 머리 + 받침 울림, 짧은 법정 초기반사.", "../sounds/trial.wav"),
    "guilty": (s_guilty, "E♭단조 → B♭감화음 하강(윗성부 311→233Hz), 저음 금관 + 팀파니로 무게.", "../sounds/guilty.wav"),
    "innocent": (s_innocent, "G sus4 → C장조 해소(윗성부 G5→C6), 하프 발현 + 부드러운 패드.", "../sounds/innocent.wav"),
    "tick": (s_tick, "1.5kHz 나무 시계 '딱' — 0.06초, 클릭 없는 어택, 또렷하지만 날카롭지 않게.", "../sounds/tick.wav"),
    "citizen_win": (s_citizen_win, "금관 G4-C5-E5-G5 → C장조 착지 화음 + 팀파니 롤·심벌 여운(승리 팡파르).", "../sounds/citizen_win.wav"),
    "mafia_win": (s_mafia_win, "저음 금관 Dm→B♭→Fm 하강(윗성부 220→175→131Hz) + 저역 붐·낮은 종.", "../sounds/mafia_win.wav"),
    "notify": (s_notify, "'띵동' 리듬(E6→G5, 기존 1300→780Hz) 유지, 순음 → 비브라폰 음색 + 잔향.", "reference/notify_old.wav"),
    "vote_open": (s_vote_open, "핸드벨 A4→E5 두 번 + 낮은 북 — 투표창이 열렸다는 신호(현재 무음).", None),
    "vote_cast": (s_vote_cast, "투표용지가 함에 '톡' — 종이·나무 소리 + 작은 확인음(현재 무음).", None),
    "death": (s_death, "낮은 조종(hum 110Hz) 한 번 + 단조 드론(현재는 trial.wav 재사용).", "../sounds/trial.wav"),
    "role_reveal": (s_role_reveal, "카드 뒤집는 역스웰 → D단조add9 발현 화음 + 저역 붐(현재 무음).", None),
    "night_action": (s_night_action, "뮤트 발현 저음 + 짧은 어두운 바람 — 은밀히 '봉인'(현재 무음).", None),
    "recruit": (s_recruit, "부드러운 호른 콜 G4-C5-E5 — '모이세요'(현재 무음).", None),
    "join": (s_join, "상행 2음 마림바 E5→A5 — 참가 신청 접수(현재 무음).", None),
    "leave": (s_leave, "하행 2음 마림바 A4→E4 — 참가 취소·방 나가기(현재 무음).", None),
}


# ───────────────────────────── 비교 페이지 ─────────────────────────────
def build_compare_html(rows):
    data_json = json.dumps(rows, ensure_ascii=False)
    html = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAFIA 사운드 비교</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e3e1dc;--acc:#4f46e5;--good:#0f766e;--warn:#b45309;--bad:#b91c1c;--chip:#eef0ff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121216;--card:#1b1b21;--ink:#ececf1;--mute:#9a9aa6;--line:#2b2b33;--acc:#a5b4fc;--good:#5eead4;--warn:#fbbf24;--bad:#fca5a5;--chip:#262640}}
:root[data-theme="dark"]{--bg:#121216;--card:#1b1b21;--ink:#ececf1;--mute:#9a9aa6;--line:#2b2b33;--acc:#a5b4fc;--good:#5eead4;--warn:#fbbf24;--bad:#fca5a5;--chip:#262640}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mute);margin:0 0 16px}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:16px;position:sticky;top:0;z-index:2}
.bar label{display:flex;gap:6px;align-items:center;cursor:pointer}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;justify-content:space-between}
.head h2{font-size:17px;margin:0}.tag{font-size:12px;background:var(--chip);color:var(--acc);border-radius:999px;padding:2px 8px}
.intent{color:var(--mute);margin:4px 0 10px;font-size:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:720px){.grid{grid-template-columns:1fr}}
.side{border:1px solid var(--line);border-radius:10px;padding:10px}
.side h3{font-size:13px;margin:0 0 6px;color:var(--mute);font-weight:600}
audio{width:100%;height:36px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;font-variant-numeric:tabular-nums}
td{padding:2px 4px;border-bottom:1px dashed var(--line)}td:first-child{color:var(--mute)}td:last-child{text-align:right}
.none{color:var(--mute);font-size:13px;padding:10px 0}
.pick{display:flex;gap:14px;margin-top:10px;font-size:14px}
.warn{color:var(--warn)}.bad{color:var(--bad)}.good{color:var(--good)}
#summary{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;font:13px/1.5 Consolas,monospace}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:4px 10px;cursor:pointer}
.calls{font-size:12px;color:var(--mute);margin-top:6px}
</style></head><body><main>
<h1>MAFIA 효과음 비교 — 기존 vs 새로 합성</h1>
<p class="sub">각 행에서 두 소리를 번갈아 들어 보고 마음에 드는 쪽을 고르세요. 선택은 이 브라우저에 저장되고, 맨 아래에 요약이 나옵니다.
기존 소리는 대부분 새 소리보다 2~7dB 크게 녹음되어 있어서(night·day는 약 7dB), 그대로 비교하면 큰 쪽이 더 좋게 들리기 쉽습니다. 공정하게 비교하려면 <b>음량 맞춤</b>을 켜 두세요.</p>
<div class="bar">
 <label><input type="checkbox" id="match" checked> 음량 맞춤(활성 RMS 기준으로 큰 쪽을 줄여 재생)</label>
 <label><input type="checkbox" id="stopOthers" checked> 재생 시 다른 소리 정지</label>
 <button id="theme">테마 전환</button>
</div>
<div id="list"></div>
<h2 style="font-size:17px">선택 요약</h2>
<div id="summary"></div>
<p class="sub" style="margin-top:8px">수치 설명: 피크/RMS = dBFS(0이 최대). 활성 RMS = 소리가 나는 구간만의 평균 음량(무음 꼬리 제외). 시작/끝 클릭 = 첫·마지막 2ms 안의 최대 샘플 점프(0.02 이상이면 '틱' 잡음 가능). 끝 50ms = 파일 마지막 50ms 레벨(-40 이상이면 소리가 뚝 끊김). 노트북 스피커 근사 RMS = 200Hz 이하를 걸러낸 뒤의 활성 RMS(작은 스피커로 들을 때의 체감 음량 근사).</p>
</main>
<script>
const ROWS = __DATA__;
const KEY = "mafia_sound_pick_v1";
let picks = {};
try { picks = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { picks = {}; }
function save(){ try { localStorage.setItem(KEY, JSON.stringify(picks)); } catch (e) {} }
const fmt = (v, d=1) => (v === null || v === undefined) ? "-" : Number(v).toFixed(d);
function cls(k, v){
  if (v == null) return "";
  if (k === "start_click" || k === "end_click") return v >= 0.02 ? "bad" : "good";
  if (k === "tail50ms_dbfs") return v > -40 ? "bad" : "good";
  if (k === "peak_dbfs") return v > -1.0 ? "warn" : "";
  return "";
}
function metrics(m){
  if (!m) return "";
  const rows = [["길이", fmt(m.duration_s,3)+" s"],["샘플레이트", m.sr+" Hz"],["피크", fmt(m.peak_dbfs)+" dBFS","peak_dbfs"],
    ["RMS(전체)", fmt(m.rms_dbfs)+" dBFS"],["활성 RMS", fmt(m.active_rms_dbfs)+" dBFS"],["노트북 스피커 근사 RMS", fmt(m.spk_rms_dbfs)+" dBFS"],["크레스트", fmt(m.crest_db)+" dB"],
    ["스펙트럼 중심", fmt(m.centroid_hz,0)+" Hz"],["배음 피크 수(-30dB)", m.n_peaks_30db],
    ["시작 클릭", fmt(m.start_click,3),"start_click"],["끝 클릭", fmt(m.end_click,3),"end_click"],["끝 50ms", fmt(m.tail50ms_dbfs)+" dBFS","tail50ms_dbfs"]];
  return "<table>"+rows.map(r=>`<tr><td>${r[0]}</td><td class="${cls(r[2], r[2]? m[r[2]]:null)}">${r[1]}</td></tr>`).join("")+"</table>";
}
const list = document.getElementById("list");
ROWS.forEach(r => {
  const c = document.createElement("section"); c.className = "card";
  const oldSide = r.old_src ? `<audio controls preload="auto" src="${r.old_src}" data-rms="${r.old ? r.old.active_rms_dbfs : ''}"></audio>${metrics(r.old)}`
                            : `<div class="none">현재 이 상황에는 소리가 없습니다(무음).</div>`;
  c.innerHTML = `<div class="head"><h2>${r.name}.wav</h2><span class="tag">${r.kind}</span></div>
   <div class="intent">${r.intent}</div>
   <div class="grid"><div class="side"><h3>기존 — ${r.old_label}</h3>${oldSide}</div>
   <div class="side"><h3>새 것 — ${r.name}.wav</h3><audio controls preload="auto" src="${r.name}.wav" data-rms="${r.new.active_rms_dbfs}"></audio>${metrics(r.new)}</div></div>
   ${r.calls ? `<div class="calls">붙일 곳: ${r.calls}</div>` : ""}
   <div class="pick"><label><input type="radio" name="p_${r.name}" value="old"> ${r.old_src ? "기존 유지" : "넣지 않음"}</label>
   <label><input type="radio" name="p_${r.name}" value="new"> 새 것 채택</label>
   <label><input type="radio" name="p_${r.name}" value="undecided"> 보류</label></div>`;
  list.appendChild(c);
  c.querySelectorAll('input[type=radio]').forEach(inp => {
    if (picks[r.name] === inp.value) inp.checked = true;
    inp.addEventListener("change", () => { picks[r.name] = inp.value; save(); summary(); });
  });
  const auds = [...c.querySelectorAll("audio")];
  auds.forEach(a => a.addEventListener("play", () => { applyVol(auds); if (document.getElementById("stopOthers").checked) document.querySelectorAll("audio").forEach(o => { if (o !== a) { o.pause(); } }); }));
});
function applyVol(auds){
  const on = document.getElementById("match").checked;
  if (auds.length < 2) { auds.forEach(a => a.volume = 1); return; }
  const [o, n] = auds; const ro = parseFloat(o.dataset.rms), rn = parseFloat(n.dataset.rms);
  if (!on || isNaN(ro) || isNaN(rn)) { o.volume = 1; n.volume = 1; return; }
  const lo = Math.min(ro, rn);
  o.volume = Math.min(1, Math.pow(10, (lo - ro) / 20));
  n.volume = Math.min(1, Math.pow(10, (lo - rn) / 20));
}
document.getElementById("match").addEventListener("change", () => document.querySelectorAll(".card").forEach(c => applyVol([...c.querySelectorAll("audio")])));
function summary(){
  const take = ROWS.filter(r => picks[r.name] === "new").map(r => r.name + ".wav");
  const keep = ROWS.filter(r => picks[r.name] === "old").map(r => r.name);
  const und = ROWS.filter(r => !picks[r.name] || picks[r.name] === "undecided").map(r => r.name);
  document.getElementById("summary").textContent =
    "새 것 채택: " + (take.join(", ") || "(없음)") + "\n기존 유지/넣지 않음: " + (keep.join(", ") || "(없음)") + "\n보류: " + (und.join(", ") || "(없음)");
}
summary();
document.getElementById("theme").addEventListener("click", () => {
  const r = document.documentElement; const dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  r.dataset.theme = dark ? "light" : "dark";
});
</script></body></html>"""
    return html.replace("__DATA__", data_json)


CALLS = {
    "night": "mafia_ui_night.py:265, mafia_ui_net.py:951 (이미 연결됨)",
    "day": "mafia_ui_night.py:720, mafia_ui_net.py:998 (이미 연결됨)",
    "trial": "mafia_ui_view.py:447 재판 개시 (이미 연결됨)",
    "guilty": "mafia_ui_view.py:542 (이미 연결됨)",
    "innocent": "mafia_ui_view.py:553 (이미 연결됨)",
    "tick": "mafia_ui_view.py:492 변론 마지막 10초 (이미 연결됨)",
    "citizen_win": "mafia_ui.py:619, mafia_ui_net.py:145 (이미 연결됨)",
    "mafia_win": "mafia_ui.py:619, mafia_ui_net.py:145 (이미 연결됨)",
    "notify": "app.py:2592 _play_notify_sound — _soft_whistle_wav() 대신 sounds/notify.wav",
    "vote_open": "mafia_ui_vote.py:170 _show_vote_popup(팝업 연 직후), :679 _open_revote_popup",
    "vote_cast": "mafia_ui_vote.py:536·561 _popup_vote(ok일 때), :819 _cast_revote, :1189 _cast_defense",
    "death": "mafia_ui_night.py:704, mafia_ui_net.py:990 — sound_type=\"trial\" → \"death\"",
    "role_reveal": "mafia_ui_view.py:150 _show_role_popup (게임 시작 시만; :123 재열람 버튼은 제외 권장)",
    "night_action": "mafia_ui_night.py:427 _apply_night_pick(호스트 ok), :446 _close_night_panel_on_ack(원격 접수)",
    "recruit": "mafia_ui_net.py:1196 recruit_start 수신(방장 아닌 사람), mafia_ui.py:265 _start_recruitment",
    "join": "mafia_ui_net.py:1216 방장이 참가 신청 받음, mafia_ui.py:414 내 참가 신청",
    "leave": "mafia_ui_net.py:1228 recruit_leave, :1150 leave_game(방장), mafia_ui.py:422 내 참가 취소, mafia_ui_net.py:202 방 나가기",
}


def main():
    global GAIN_OFFSET_DB
    sys.stdout.reconfigure(encoding="utf-8")
    if "--gain-db" in sys.argv:
        GAIN_OFFSET_DB = float(sys.argv[sys.argv.index("--gain-db") + 1])
        print(f"전체 목표 음량 오프셋: {GAIN_OFFSET_DB:+.1f} dB")
    os.makedirs(os.path.join(HERE, "reference"), exist_ok=True)
    # 기존 메모리 합성 알림음을 비교용 파일로 저장(22.05kHz 원본 그대로)
    with open(os.path.join(HERE, "reference", "notify_old.wav"), "wb") as fp:
        fp.write(AZ.legacy_notify_wav())
    rows = []
    for name, (fn, intent, old_rel) in SOUNDS.items():
        x = fn()
        path = os.path.join(HERE, name + ".wav")
        write_wav(path, x)
        new_m = AZ.analyze_file(path)
        old_m = AZ.analyze_file(os.path.normpath(os.path.join(HERE, old_rel))) if old_rel else None
        is_legacy = name in AZ.NAMES
        label = ("sounds/" + name + ".wav") if is_legacy else (
            "기존 알림음(_soft_whistle_wav)" if name == "notify" else ("현재 재생되는 trial.wav" if name == "death" else "없음"))
        rows.append({"name": name, "intent": intent, "old_src": old_rel, "old_label": label,
                     "kind": "기존 교체" if is_legacy or name == "notify" else "신규 제안",
                     "old": old_m, "new": new_m, "calls": CALLS.get(name, "")})
        print(f"{name:13s} len={new_m['duration_s']:.3f}s peak={new_m['peak_dbfs']:6.2f} GR={LAST_GR['db']:4.1f}dB "
              f"rms={new_m['rms_dbfs']:6.2f} act={new_m['active_rms_dbfs']:6.2f} spk={new_m['spk_rms_dbfs']:6.2f} "
              f"clk={new_m['start_click']:.3f}/{new_m['end_click']:.3f} tail={new_m['tail50ms_dbfs']:6.1f} "
              f"cent={new_m['centroid_hz']:7.1f} peaks={new_m['n_peaks_30db']} bands={new_m['band_energy_pct']}")
    with open(os.path.join(HERE, "compare.html"), "w", encoding="utf-8") as fp:
        fp.write(build_compare_html(rows))
    AZ.main()          # 기존/신규/시스템 비프 전체 분석 → analysis.json + 표 출력


if __name__ == "__main__":
    main()
