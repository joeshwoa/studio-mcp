"""Procedural (offline, royalty-free) music: lo-fi, cinematic, corporate, oriental/maqam.

A tiny composer writes note events (with sections, chord progressions, rhythm patterns and phrase-
based melodies), then the same events are (1) rendered with the numpy synth in _synth.py and
(2) written as a multi-track MIDI file (the editable master: opens in GarageBand, Logic, MuseScore,
LMMS, Reaper…). Quarter-tone maqams are exact in the audio and use pitch-bend in the MIDI."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ...core.registry import tool
from ...core.result import Result, ToolError
from . import _common as C
from . import _synth as S

NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
        "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
MAQAM = {  # one octave, semitones from the tonic (quarter tones as .5)
    "hijaz": [0, 1, 4, 5, 7, 8, 10], "hijazkar": [0, 1, 4, 5, 7, 8, 11], "kurd": [0, 1, 3, 5, 7, 8, 10],
    "nahawand": [0, 2, 3, 5, 7, 8, 11], "bayati": [0, 1.5, 3, 5, 7, 8, 10], "rast": [0, 2, 3.5, 5, 7, 9, 10.5],
    "saba": [0, 1.5, 3, 4, 7, 8, 10], "nikriz": [0, 2, 3, 6, 7, 9, 10],
}
RHYTHMS = {  # darbuka patterns over one 4/4 bar: (beat, stroke)
    "maqsum": [(0, "dum"), (0.5, "tek"), (1.5, "tek"), (2, "dum"), (3, "tek")],
    "baladi": [(0, "dum"), (0.5, "dum"), (1.5, "tek"), (2, "dum"), (3, "tek")],
    "saidi": [(0, "dum"), (0.5, "tek"), (1.5, "dum"), (2, "dum"), (3, "tek")],
    "malfuf": [(0, "dum"), (0.75, "tek"), (1.5, "tek"), (2, "dum"), (2.75, "tek"), (3.5, "tek")],
}
GENRES = ("lofi", "cinematic", "corporate", "oriental")
GM_DRUM = {"kick": 36, "snare": 38, "clap": 39, "hat": 42, "ohat": 46, "rim": 37, "boom": 35, "dum": 64, "tek": 62,
           "ka": 63, "riq": 54}
GM_PROG = {"keys": 4, "bass": 33, "pad": 89, "strings": 48, "pluck": 25, "bell": 10, "oud": 105, "qanun": 107,
           "ney": 73, "piano": 0, "lead": 4}


@dataclass
class Ev:
    track: str
    beat: float
    dur: float
    note: float | str
    vel: float = 0.8
    extra: dict = field(default_factory=dict)


class Song:
    def __init__(self, bpm: float, bars: int, seed: int):
        self.bpm, self.bars = bpm, bars
        self.rng = np.random.default_rng(seed)
        self.ev: list[Ev] = []
        self.sections: list[tuple[int, int, str]] = []

    def add(self, *a, **k):
        self.ev.append(Ev(*a, **k))

    def section(self, bar: int) -> str:
        for s, e, name in self.sections:
            if s <= bar < e:
                return name
        return "main"


def _layout(bars: int, names: list[tuple[str, float]]) -> list[tuple[int, int, str]]:
    """Split `bars` into sections by relative weight, each a multiple of 2 bars (≥2)."""
    tot = sum(w for _, w in names)
    out, b = [], 0
    for i, (n, w) in enumerate(names):
        k = bars - b if i == len(names) - 1 else max(2, int(round(bars * w / tot / 2)) * 2)
        k = max(0, min(k, bars - b - 2 * (len(names) - 1 - i)))
        if k:
            out.append((b, b + k, n))
        b += k
    return out


def _chord(root: float, scale: list[float], degree: int, n: int = 4, spread: int = 2) -> list[float]:
    """Stacked thirds on a scale degree (degree 0-based), n notes."""
    out = []
    for i in range(n):
        d = degree + spread * i
        out.append(root + scale[d % 7] + 12 * (d // 7))
    return out


def _voice(notes: list[float], lo: float, hi: float) -> list[float]:
    """Move chord tones into [lo, hi) for smooth voicing."""
    out = []
    for n in notes:
        while n < lo:
            n += 12
        while n >= hi:
            n -= 12
        out.append(n)
    return sorted(out)


# ───────────────────────────── genres ─────────────────────────────

def compose_lofi(s: Song, key: int):
    scale = MAJOR
    root = 48 + key
    progs = [[1, 4, 0, 5], [0, 5, 1, 4], [3, 2, 1, 0], [0, 2, 3, 4]]
    prog = progs[s.rng.integers(len(progs))]
    s.sections = _layout(s.bars, [("intro", 1), ("main", 4), ("break", 1), ("main2", 3), ("outro", 1)])
    pent = [0, 2, 4, 7, 9]
    for bar in range(s.bars):
        sec = s.section(bar)
        deg = prog[bar % 4]
        ch = _voice(_chord(root, scale, deg, 5)[1:], 55, 72)      # rootless 3-5-7-9
        b0 = bar * 4
        # keys: soft chord, sometimes re-struck on the 'and' of 3
        s.add("keys", b0, 3.4, 0, 0.55, {"chord": ch})
        if sec in ("main", "main2") and s.rng.random() < 0.5:
            s.add("keys", b0 + 2.5, 1.3, 0, 0.35, {"chord": ch[1:]})
        if sec != "intro":
            bn = root - 12 + scale[deg % 7]
            s.add("bass", b0, 1.4, bn, 0.8)
            s.add("bass", b0 + 2.5, 1.0, bn + (7 if s.rng.random() < 0.4 else 0), 0.65)
        if sec in ("main", "main2"):
            for st in (0, 2.5):
                s.add("drums", b0 + st, 0.2, "kick", 0.95 if st == 0 else 0.75)
            if s.rng.random() < 0.3:
                s.add("drums", b0 + 1.75, 0.2, "kick", 0.55)
            for st in (1, 3):
                s.add("drums", b0 + st, 0.2, "snare", 0.7)
            for i in range(8):
                sw = 0.08 if i % 2 else 0.0                          # swing the off-beats
                s.add("drums", b0 + i * 0.5 + sw, 0.1, "hat", 0.35 + 0.2 * (i % 2 == 0) * s.rng.random())
        # melody: short pentatonic phrases every other bar in main sections
        if sec in ("main2", "main") and bar % 2 == 1:
            t = 0.5 + s.rng.integers(0, 2) * 0.5
            d = int(s.rng.integers(2, 5))
            while t < 3.5:
                d = int(np.clip(d + s.rng.choice([-1, 0, 1, 1, -2]), 0, 9))
                n = root + 12 + pent[d % 5] + 12 * (d // 5)
                ln = float(s.rng.choice([0.5, 0.5, 1.0, 0.75]))
                s.add("lead", b0 + t, ln, n, 0.5)
                t += ln + float(s.rng.choice([0, 0, 0.5]))
    s.add("keys", s.bars * 4, 4, 0, 0.5, {"chord": _voice(_chord(root, scale, 0, 5)[1:], 55, 72)})
    return {"key": f"{_name(key)} major", "progression": [_roman(d, False) + "7" for d in prog]}


def compose_cinematic(s: Song, key: int):
    scale = MINOR
    root = 45 + key
    prog = [[0, 5, 2, 6], [0, 5, 3, 4], [0, 3, 5, 4]][s.rng.integers(3)]
    s.sections = _layout(s.bars, [("intro", 1.2), ("build", 2), ("climax", 2), ("resolve", 1)])
    for bar in range(s.bars):
        sec = s.section(bar)
        deg = prog[bar % 4]
        b0 = bar * 4
        ch = _chord(root, scale, deg, 3)
        vel = {"intro": 0.45, "build": 0.6, "climax": 0.85, "resolve": 0.5}[sec]
        s.add("strings", b0, 4.0, 0, vel, {"chord": _voice(ch, 50, 69)})
        s.add("bass", b0, 3.9, root - 12 + scale[deg % 7], 0.5 if sec == "intro" else 0.75, {"sustain": True})
        if sec in ("build", "climax"):
            arp = _voice(ch + [ch[0] + 12], 57, 81)
            pat = [0, 1, 2, 3, 2, 1, 2, 3] if sec == "build" else [0, 2, 1, 3, 0, 2, 1, 3]
            for i, p in enumerate(pat):
                s.add("piano", b0 + i * 0.5, 0.45, arp[p % len(arp)], 0.45 + 0.1 * (i % 4 == 0))
        if sec == "climax":
            s.add("strings", b0, 4.0, 0, 0.55, {"chord": _voice([ch[0] + 12, ch[2] + 12], 69, 86), "high": True})
            s.add("drums", b0, 1, "boom", 0.9)
            s.add("drums", b0 + 2.5, 1, "boom", 0.5)
            for i in range(4):
                s.add("drums", b0 + i, 0.2, "kick", 0.55)
        if sec == "build":
            s.add("drums", b0, 1, "boom", 0.35 + 0.4 * (bar - s.sections[1][0]) / max(1, s.sections[1][1] - s.sections[1][0]))
            if bar == s.sections[1][1] - 1:           # snare-roll riser into the climax
                for i in range(16):
                    s.add("drums", b0 + i * 0.25, 0.1, "snare", 0.15 + 0.6 * i / 16)
    s.add("strings", s.bars * 4, 6, 0, 0.5, {"chord": _voice(_chord(root, scale, 0, 3), 50, 69)})
    s.add("bass", s.bars * 4, 6, root - 12, 0.6, {"sustain": True})
    s.add("drums", s.bars * 4, 1, "boom", 0.8)
    return {"key": f"{_name(key)} minor", "progression": [_roman(d, True) for d in prog]}


def compose_corporate(s: Song, key: int):
    scale = MAJOR
    root = 48 + key
    prog = [[0, 4, 5, 3], [5, 3, 0, 4], [0, 5, 3, 4]][s.rng.integers(3)]
    s.sections = _layout(s.bars, [("intro", 1), ("verse", 2), ("hook", 2), ("verse2", 1), ("hook2", 2), ("outro", 1)])
    hook = [int(x) for x in s.rng.choice([0, 1, 2, 4, 2, 1, 4, 5], 6)]
    hook_r = [0.5, 0.5, 1, 0.5, 0.5, 1]
    for bar in range(s.bars):
        sec = s.section(bar)
        deg = prog[bar % 4]
        b0 = bar * 4
        ch = _chord(root, scale, deg, 3)
        arp = _voice(ch + [ch[0] + 12], 60, 79)
        for i in range(8):                                       # plucky 8th-note arpeggio
            s.add("pluck", b0 + i * 0.5, 0.4, arp[[0, 2, 1, 3, 0, 2, 1, 2][i]], 0.55 + 0.15 * (i % 2 == 0))
        if sec != "intro":
            for i in range(4):
                s.add("keys", b0 + i + 0.5, 0.4, 0, 0.4, {"chord": _voice(ch, 52, 67)})
            for i in range(8):
                s.add("bass", b0 + i * 0.5, 0.42, root - 12 + scale[deg % 7] + (12 if i % 4 == 3 else 0), 0.7)
        if sec not in ("intro", "outro"):
            for i in range(4):
                s.add("drums", b0 + i, 0.2, "kick", 0.8)
            s.add("drums", b0 + 1, 0.2, "clap", 0.6)
            s.add("drums", b0 + 3, 0.2, "clap", 0.6)
            for i in range(16):
                s.add("drums", b0 + i * 0.25, 0.1, "hat", 0.18 + 0.14 * (i % 2 == 0))
        if sec.startswith("hook"):
            t = 0.0
            for d, r in zip(hook, hook_r):
                s.add("bell", b0 + t, r, root + 24 + scale[(d + (2 if bar % 2 else 0)) % 7], 0.5)
                t += r
    s.add("pluck", s.bars * 4, 2, root + 12, 0.6)
    s.add("keys", s.bars * 4, 3, 0, 0.5, {"chord": _voice(_chord(root, scale, 0, 3), 52, 67)})
    s.add("bass", s.bars * 4, 2, root - 12, 0.7)
    return {"key": f"{_name(key)} major", "progression": [_roman(d, False) for d in prog]}


def _phrase(s: Song, scale: list[float], start_deg: int, end_deg: int, beats: float, lo: int, hi: int) -> list[tuple[float, float, int]]:
    """Maqam melody: stepwise walk (mostly seconds, some thirds, rare fourths), ends on end_deg."""
    cells = [[1, 0.5, 0.5, 1, 1], [0.5, 0.5, 0.5, 0.5, 2], [0.75, 0.25, 0.5, 0.5, 1, 1], [1.5, 0.5, 1, 1],
             [0.5, 0.5, 1, 0.5, 0.5, 1], [0.25, 0.25, 0.5, 1, 2]]
    rhythm = []
    while sum(rhythm) < beats:
        rhythm += cells[s.rng.integers(len(cells))]
    while sum(rhythm) > beats:
        rhythm[-1] -= sum(rhythm) - beats
        if rhythm[-1] <= 0:
            rhythm.pop()
    rhythm[-1] = max(rhythm[-1], 1.0) if sum(rhythm[:-1]) + 1 <= beats else rhythm[-1]
    out, d, t = [], start_deg, 0.0
    n = len(rhythm)
    for i, r in enumerate(rhythm):
        if i == n - 1:
            d = end_deg
        elif i >= n - 3:                                     # approach the cadence by step
            d += int(np.sign(end_deg - d)) if d != end_deg else int(s.rng.choice([-1, 1]))
        else:
            d += int(s.rng.choice([-1, 1, -1, 1, 2, -2, 0, 3, -1]))
        d = int(np.clip(d, lo, hi))
        out.append((t, r, d))
        t += r
    return out


def compose_oriental(s: Song, key: int, maqam: str, rhythm: str):
    scale = MAQAM[maqam]
    root = 50 + key                                          # D3 by default (key=0 → D)
    pat = RHYTHMS[rhythm]
    s.sections = _layout(s.bars, [("taqsim", 1), ("A", 2), ("B", 2), ("A2", 2), ("khatma", 1)])

    def deg2note(d: int, base: float) -> float:
        return base + scale[d % 7] + 12 * (d // 7)

    motif_a = None
    for bar in range(0, s.bars, 2):
        sec = s.section(bar)
        b0 = bar * 4
        # drone: tonic + fifth (strings) and oud bass on the dums
        s.add("strings", b0, 8.0, 0, 0.35 if sec != "taqsim" else 0.45, {"chord": [root, root + 7]})
        if sec != "taqsim":
            for bb in range(2):
                for bt, st in pat:
                    s.add("drums", b0 + bb * 4 + bt, 0.2, st, 0.85 if st == "dum" else 0.6)
                    if st == "dum":
                        s.add("bass", b0 + bb * 4 + bt, 0.9, root - 12 + (7 if (bb and bt == 2) else 0), 0.7)
                for i in range(8):                            # riq on 8ths, ka ghost notes on free 16ths
                    s.add("drums", b0 + bb * 4 + i * 0.5, 0.1, "riq", 0.25 + 0.2 * (i % 2 == 0))
                taken = {bt for bt, _ in pat}
                for i in range(16):
                    q = i * 0.25
                    if q not in taken and s.rng.random() < 0.28:
                        s.add("drums", b0 + bb * 4 + q, 0.1, "ka", 0.25)
        # melody
        if sec == "taqsim":                                   # free-ish ney intro over the drone
            ph = _phrase(s, scale, 4, 0, 8, 0, 8)
            for t, r, d in ph:
                s.add("ney", b0 + t, r * 1.02, deg2note(d, root + 12), 0.5)
        elif sec in ("A", "A2"):
            if motif_a is None or sec == "A2" and s.rng.random() < 0.3:
                motif_a = _phrase(s, scale, 2, 0 if (bar // 2) % 2 else 4, 8, -1, 8)
            ph = motif_a if (bar // 2) % 2 == 0 else motif_a[:-2] + [(motif_a[-2][0], motif_a[-2][1], 1),
                                                                    (motif_a[-1][0], motif_a[-1][1], 0)]
            for t, r, d in ph:
                s.add("oud", b0 + t, r, deg2note(d, root + 12), 0.8)
                if r >= 1.0 and s.rng.random() < 0.6:        # qanun tremolo on long notes
                    k = int(r / 0.125)
                    for j in range(k):
                        s.add("qanun", b0 + t + j * 0.125, 0.12, deg2note(d, root + 24), 0.25 + 0.1 * (j % 2))
        elif sec == "B":                                      # answer in the upper jins, ney doubles
            ph = _phrase(s, scale, 5, 4 if (bar // 2) % 2 == 0 else 0, 8, 2, 10)
            for t, r, d in ph:
                s.add("oud", b0 + t, r, deg2note(d, root + 12), 0.75)
                s.add("ney", b0 + t, r, deg2note(d, root + 24), 0.3)
        else:                                                 # khatma: descend to the tonic
            ph = _phrase(s, scale, 4, 0, 7, 0, 7)
            for t, r, d in ph:
                s.add("oud", b0 + t, r, deg2note(d, root + 12), 0.8)
    end = s.bars * 4
    s.add("oud", end, 2.5, root + 12, 0.9)
    s.add("qanun", end, 0.2, root + 24, 0.4)
    s.add("strings", end, 5, 0, 0.4, {"chord": [root, root + 7]})
    s.add("drums", end, 0.3, "dum", 0.9)
    s.add("bass", end, 2, root - 12, 0.7)
    return {"key": f"{_name(key + 2)} (tonic)", "maqam": maqam, "rhythm": rhythm,
            "scale_semitones": scale}


def _name(k: int) -> str:
    return ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"][k % 12]


def _roman(d: int, minor: bool) -> str:
    r = ["I", "II", "III", "IV", "V", "VI", "VII"][d % 7]
    small = ([0, 3, 4] if minor else [1, 2, 5, 6])
    return r.lower() if d % 7 in small else r


# ───────────────────────────── render + midi ─────────────────────────────

TRACK_MIX = {  # gain, pan, reverb send bus
    "keys": (1.0, -0.15, "room"), "lead": (0.55, 0.2, "hall"), "bass": (0.9, 0.0, None), "drums": (0.9, 0.0, "room"),
    "strings": (0.9, 0.0, "hall"), "piano": (0.6, 0.15, "hall"), "pluck": (0.55, -0.2, "room"),
    "bell": (0.5, 0.25, "hall"), "oud": (0.85, -0.1, "room"), "qanun": (0.45, 0.35, "room"), "ney": (0.6, 0.2, "hall"),
}
DRUM_PAN = {"hat": 0.25, "ohat": 0.25, "riq": 0.35, "ka": -0.2, "tek": -0.1, "clap": 0.05}


def render(song: Song, genre: str) -> np.ndarray:
    import pedalboard as pb
    spb = 60.0 / song.bpm
    end = max(e.beat + e.dur for e in song.ev) * spb + 4.0
    n = int(end * S.SR)
    buses = {k: np.zeros((n, 2), np.float32) for k in ("dry", "room", "hall")}
    drum_bus = np.zeros((n, 2), np.float32)
    kick_env = np.zeros(n, np.float32)
    rng = np.random.default_rng(11)
    cache: dict = {}
    for e in song.ev:
        at = int((e.beat * spb + rng.normal(0, 0.004 if e.track != "drums" else 0.002)) * S.SR)
        at = max(0, at)
        dur = e.dur * spb
        g, pan, bus = TRACK_MIX[e.track]
        if e.track == "drums":
            key = (e.note, round(e.vel, 2))
            if key not in cache:
                fn = {"kick": lambda v: S.kick(v), "snare": S.snare, "hat": S.hat, "ohat": lambda v: S.hat(v, True),
                      "clap": S.clap, "rim": S.rim, "boom": S.boom, "riq": S.riq,
                      "dum": lambda v: S.darbuka("dum", v), "tek": lambda v: S.darbuka("tek", v),
                      "ka": lambda v: S.darbuka("ka", v)}[e.note]
                cache[key] = fn(e.vel)
            x = cache[key]
            S.place(drum_bus, x, at, g, DRUM_PAN.get(e.note, 0.0))
            if e.note in ("kick", "dum", "boom"):
                kick_env[at:at + len(x)] = np.maximum(kick_env[at:at + len(x)], np.abs(x[: n - at]) if at < n else 0)
            continue
        tr = e.track
        if tr == "keys":
            x = sum(S.epiano(nt, dur, e.vel * (0.9 + 0.2 * rng.random())) for nt in e.extra["chord"])
        elif tr == "piano":
            x = S.epiano(e.note, dur, e.vel, bright=0.6)
        elif tr == "lead":
            x = S.epiano(e.note, dur, e.vel, bright=1.3) if genre == "lofi" else S.bell(e.note, dur, e.vel)
        elif tr == "bell":
            x = S.add(S.bell(e.note, dur, e.vel), 0.5 * S.pluck_ks(e.note, dur, e.vel * 0.5, bright=0.9))
        elif tr == "bass":
            x = S.bass(e.note, dur, e.vel, drive=1.2 if e.extra.get("sustain") else 2.0) if genre != "oriental" \
                else S.pluck_ks(e.note, dur, e.vel, bright=0.3, damp=0.997, body_hz=110)
        elif tr == "strings":
            ch = e.extra["chord"]
            x = S.pad(ch, dur, e.vel, cutoff=2600 if e.extra.get("high") else 1700, attack=1.2 if genre == "cinematic" else 0.6,
                      release=2.0, vibrato=1.0)
        elif tr == "pluck":
            x = S.pluck_ks(e.note, min(dur, 0.35), e.vel, bright=0.75, damp=0.994)
        elif tr == "oud":
            x = S.pluck_ks(e.note, dur, e.vel, bright=0.42, damp=0.9965, body_hz=190)
        elif tr == "qanun":
            x = S.pluck_ks(e.note, dur, e.vel, bright=0.85, damp=0.995)
        elif tr == "ney":
            x = S.ney(e.note, dur, e.vel)
        else:
            continue
        S.place(buses[bus or "dry"], x.astype(np.float32), at, g, pan + rng.uniform(-0.05, 0.05))
    # sidechain-style pump on the music (not drums) from the kick/dum: subtle, genre-dependent
    duck = {"lofi": 0.2, "corporate": 0.25, "oriental": 0.12, "cinematic": 0.0}[genre]
    if duck:
        from scipy.ndimage import uniform_filter1d
        ke = uniform_filter1d(kick_env, int(0.08 * S.SR))
        ke = ke / (ke.max() + 1e-9)
        gmul = (1 - duck * ke)[:, None]
        for k in buses:
            buses[k] *= gmul
    room = pb.Pedalboard([pb.Reverb(room_size=0.35, damping=0.6, wet_level=1.0, dry_level=0.0, width=0.9)])
    hall = pb.Pedalboard([pb.Reverb(room_size=0.85 if genre == "cinematic" else 0.7, damping=0.45, wet_level=1.0,
                                    dry_level=0.0, width=1.0)])
    drums_wet = room(drum_bus.T, S.SR).T * (0.12 if genre != "cinematic" else 0.35)
    mix = (buses["dry"] + buses["room"] + buses["hall"] + drum_bus
           + room(buses["room"].T, S.SR).T * 0.28 + hall(buses["hall"].T, S.SR).T * (0.5 if genre == "cinematic" else 0.35)
           + drums_wet)
    if genre == "lofi":
        t = np.arange(n) / S.SR
        wow = 0.0018 * np.sin(2 * np.pi * 0.55 * t) + 0.0006 * np.sin(2 * np.pi * 3.1 * t)
        idx = np.clip(np.arange(n) + wow * S.SR, 0, n - 1)
        mix = np.stack([np.interp(idx, np.arange(n), mix[:, c]) for c in range(2)], 1).astype(np.float32)
        mix = pb.Pedalboard([pb.LowpassFilter(7500), pb.HighpassFilter(40)])(mix.T, S.SR).T
        cr = S.crackle(n)
        mix += np.stack([cr, cr * 0.9], 1) * 0.6
    master = pb.Pedalboard([pb.HighpassFilter(28), pb.Compressor(threshold_db=-16, ratio=2.0, attack_ms=20, release_ms=180)])
    mix = master(mix.T.astype(np.float32), S.SR).T
    return mix.astype(np.float32)


def write_midi(song: Song, path) -> None:
    import mido
    tpb = 480
    mf = mido.MidiFile(ticks_per_beat=tpb)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.bpm)))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    mf.tracks.append(meta)
    tracks = sorted({e.track for e in song.ev})
    chan = 0
    for tr in tracks:
        ch = 9 if tr == "drums" else chan
        if tr != "drums":
            chan += 1 + (chan == 8)
        msgs = []
        for e in song.ev:
            if e.track != tr:
                continue
            notes = [GM_DRUM[e.note]] if tr == "drums" else (e.extra.get("chord") or [e.note])
            for nt in notes:
                base = int(math.floor(nt + 1e-6))
                frac = float(nt) - base
                on = int(e.beat * tpb)
                off = on + max(1, int(e.dur * tpb))
                v = int(np.clip(e.vel * 110, 1, 127))
                if tr != "drums":
                    msgs.append((on, 0, mido.Message("pitchwheel", channel=ch, pitch=int(frac * 4096))))
                msgs.append((on, 1, mido.Message("note_on", channel=ch, note=base, velocity=v)))
                msgs.append((off, -1, mido.Message("note_off", channel=ch, note=base, velocity=0)))
        msgs.sort(key=lambda m: (m[0], m[1]))
        t = mido.MidiTrack()
        t.append(mido.MetaMessage("track_name", name=tr))
        if tr != "drums":
            t.append(mido.Message("program_change", channel=ch, program=GM_PROG.get(tr, 0)))
            # pitch-bend range ±2 semitones (RPN 0) so quarter tones = pitch 2048
            for c, val in ((101, 0), (100, 0), (6, 2), (38, 0)):
                t.append(mido.Message("control_change", channel=ch, control=c, value=val))
        last = 0
        for tick, _, m in msgs:
            t.append(m.copy(time=tick - last))
            last = tick
        mf.tracks.append(t)
    mf.save(str(path))


def _key(k: str, genre: str) -> int | None:
    if not k.strip():
        return None
    tok = k.strip().split()[0]
    for suf in ("min", "maj", "m"):
        if len(tok) > 1 and tok.lower().endswith(suf) and tok[:-len(suf)].capitalize() in NOTE:
            tok = tok[:-len(suf)]
            break
    tok = tok[0].upper() + tok[1:]
    if tok not in NOTE:
        raise ToolError(f"unknown key {k!r}", "C, D, Eb, F#, A …")
    return NOTE[tok]


@tool("audio")
def audio_music(genre: str = "lofi", duration: float = 60.0, bpm: float = 0.0, key: str = "", maqam: str = "hijaz",
                rhythm: str = "maqsum", seed: int = 0, loudness: float = -14.0, fit: bool = False,
                project: str = "", out: str = "") -> Result:
    """Generate an original, royalty-free music bed offline (no AI model, no internet): WAV + editable
    multi-track MIDI. genre: lofi (jazzy 7th chords, swung boom-bap, vinyl) | cinematic (strings, piano
    ostinato, build → climax → resolve, trailer booms) | corporate (bright plucks, claps, bell hook,
    I–V–vi–IV) | oriental (Arabic maqam melody on a plucked oud, qanun tremolos, ney, tonic drone and
    darbuka/riq). maqam: hijaz | hijazkar | kurd | nahawand | bayati | rast | saba | nikriz (bayati/rast/
    saba use exact quarter tones). rhythm (oriental): maqsum | baladi | saidi | malfuf. key e.g. 'D'
    (oriental: tonic, default D), bpm 0 = genre default, seed = variation (same seed → same piece).
    Ends musically (± a bar); fit=true fades out at exactly `duration`. Honest level: tasteful synthetic
    demo/library quality — good for reels/explainers under a voice, not a substitute for a composer."""
    g = genre.lower().strip()
    if g in ("arabic", "maqam", "oriental-arabic", "eastern"):
        g = "oriental"
    if g not in GENRES:
        raise ToolError(f"unknown genre {genre!r}", f"one of {GENRES}")
    if g == "oriental" and maqam not in MAQAM:
        raise ToolError(f"unknown maqam {maqam!r}", f"one of {sorted(MAQAM)}")
    if g == "oriental" and rhythm not in RHYTHMS:
        raise ToolError(f"unknown rhythm {rhythm!r}", f"one of {sorted(RHYTHMS)}")
    import pedalboard  # noqa: F401  (clear error if missing)
    rng = np.random.default_rng(seed or None)
    seed = seed or int(rng.integers(1, 10**6))
    default_bpm = {"lofi": 78, "cinematic": 80, "corporate": 114, "oriental": 100}[g]
    tempo = bpm or default_bpm
    bars = max(8, int(round(duration / (4 * 60 / tempo) / 2)) * 2)
    bars = max(8, bars - 2)          # leave room for the ending/tail (keeps an even bar count)
    song = Song(tempo, bars, seed)
    k = _key(key, g)
    if k is None:
        k = int(np.random.default_rng(seed).choice([0, 2, 3, 5, 7, 9])) if g != "oriental" else 0
    if g == "lofi":
        info = compose_lofi(song, k)
    elif g == "cinematic":
        info = compose_cinematic(song, k)
    elif g == "corporate":
        info = compose_corporate(song, k)
    else:
        info = compose_oriental(song, (k - 2) % 12 if key else 0, maqam, rhythm)
    mix = render(song, g)
    # trim trailing silence of the reverb tail, keep ≤ 3 s
    env = C.rms_env(mix, S.SR, 0.05)
    last = np.where(env > C.undb(-60))[0]
    if len(last):
        mix = mix[: min(len(mix), last[-1] + int(0.2 * S.SR))]
    if fit:
        n = int(duration * S.SR)
        mix = mix[:n] if len(mix) >= n else np.pad(mix, ((0, n - len(mix)), (0, 0)))
        mix = C.fade(mix, S.SR, 0.0, min(3.0, duration / 6))
    else:
        mix = C.fade(mix, S.SR, 0.01, 0.8)
    mix, norm = C.normalize_loudness(mix, S.SR, loudness, -1.0)
    stem = f"music-{g}" + (f"-{info.get('maqam')}" if g == "oriental" else "") + f"-{seed}"
    dest = C.out_path(project, out, stem, ".wav", kind="audio/music")
    C.save(dest, mix, S.SR)
    mid = C.sibling(dest, ".mid")
    write_midi(song, mid)
    m, w = C.measure(dest, loudness)
    spb = 60 / tempo
    secs = [{"name": n, "start": round(a * 4 * spb, 2), "end": round(b * 4 * spb, 2)} for a, b, n in song.sections]
    pv = C.preview(dest, C.sibling(dest, ".png"), metrics=m, title=f"{g} {info.get('maqam', '')} {tempo:g} bpm seed {seed}",
                   marks=[(s["start"], s["start"] + 0.3, s["name"]) for s in secs])
    res = Result(f"Generated a {m['duration']:.0f}s {g} piece ({info.get('maqam', info.get('key'))}, {tempo:g} bpm, "
                 f"{bars} bars, {len(song.ev)} notes, seed {seed}) → {m.get('lufs')} LUFS. Procedurally synthesised — "
                 f"original and royalty-free, library/demo quality.",
                 files=[str(dest), str(mid)], previews=[str(pv)], warnings=w,
                 data={"audio": str(dest), "midi": str(mid), "genre": g, "bpm": tempo, "seed": seed, "sections": secs,
                       "metrics": m, **info})
    res.next_steps += ["Listen first; if it doesn't fit, try another seed (cheap) before changing genre.",
                       "Open the .mid in GarageBand/Logic/MuseScore/LMMS to re-orchestrate with real instruments.",
                       "Put it under a voice with audio_mix (auto-ducking).",
                       "For AI-generated music (MusicGen / Stable Audio Open) use the ai department (ComfyUI)."]
    return res
