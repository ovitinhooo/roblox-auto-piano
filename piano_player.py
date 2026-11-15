"""
Auto Piano Player para Roblox
Toca músicas no piano do Roblox simulando o teclado (layout "Virtual Piano", 61 teclas).

Aceita:
  - Partituras em texto no formato Virtual Piano (.txt)
  - Arquivos MIDI (.mid / .midi)

Atalhos globais (funcionam com o Roblox em foco):
  F5 = tocar / pausar
  F6 = parar

O botão "Prévia" toca a música no som do PC (sintetizador MIDI do Windows).
As configurações ficam salvas em config.json, ao lado do app.
"""

import ctypes
import json
import os
import shutil
import struct
import sys
import threading
import time
import tkinter as tk
import unicodedata
import urllib.parse
import webbrowser
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Mapeamento do piano (C2 .. C7 = MIDI 36 .. 96)
# ---------------------------------------------------------------------------

KEYS = "1!2@34$5%6^78*9(0qQwWeErtTyYuiIoOpPasSdDfgGhHjJklLzZxcCvVbBnm"
LOW_NOTE = 36
HIGH_NOTE = LOW_NOTE + len(KEYS) - 1  # 96
NOTE_TO_KEY = {LOW_NOTE + i: k for i, k in enumerate(KEYS)}
KEY_TO_NOTE = {k: n for n, k in NOTE_TO_KEY.items()}

# Modo 88 teclas (A0 .. C8): notas extras com Ctrl + tecla
CTRL_PREFIX = "c:"
LOW_88 = "1234567890qwert"   # A0 .. B1  (MIDI 21 .. 35)
HIGH_88 = "yuiopasdfghj"     # C#7 .. C8 (MIDI 97 .. 108)
NOTE_TO_KEY_88 = dict(NOTE_TO_KEY)
NOTE_TO_KEY_88.update({21 + i: CTRL_PREFIX + k for i, k in enumerate(LOW_88)})
NOTE_TO_KEY_88.update({97 + i: CTRL_PREFIX + k for i, k in enumerate(HIGH_88)})

SHIFT_SYMBOLS = {"!": "1", "@": "2", "$": "4", "%": "5", "^": "6", "*": "8", "(": "9"}

SCANCODES = {
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07,
    "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
}
SC_LSHIFT = 0x2A
SC_LCTRL = 0x1D
SC_SPACE = 0x39


def split_key(ch):
    """Retorna (tecla_base, precisa_shift, precisa_ctrl)."""
    if ch.startswith(CTRL_PREFIX):
        return ch[len(CTRL_PREFIX):], False, True
    if ch in SHIFT_SYMBOLS:
        return SHIFT_SYMBOLS[ch], True, False
    if ch.isalpha() and ch.isupper():
        return ch.lower(), True, False
    return ch, False, False


# ---------------------------------------------------------------------------
# Envio de teclas (SendInput com scancode — é o que o Roblox reconhece)
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def _send_scancodes(codes, up):
    if not codes:
        return
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    arr = (INPUT * len(codes))()
    for i, sc in enumerate(codes):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(0, sc, flags, 0, 0)
    user32.SendInput(len(codes), arr, ctypes.sizeof(INPUT))


SUSTAIN_OFF, SUSTAIN_TOGGLE, SUSTAIN_HOLD = 0, 1, 2


def tap(sc):
    _send_scancodes([sc], up=False)
    time.sleep(0.02)
    _send_scancodes([sc], up=True)


class Keyboard:
    """Saída para o Roblox: aperta as teclas de verdade.
    Cada nota tem um id para que o "soltar" de uma nota antiga não solte
    a mesma tecla tocada de novo depois."""

    def __init__(self):
        self.owner = {}  # scancode -> (tecla, id)

    def down(self, chars, ident):
        groups = {(False, False): [], (True, False): [], (False, True): []}
        used = set()
        for ch in chars:
            base, shift, ctrl = split_key(ch)
            sc = SCANCODES.get(base)
            if sc is None or sc in used:
                continue
            used.add(sc)
            if sc in self.owner:  # mesma tecla ainda segurada: solta e toca de novo
                _send_scancodes([sc], up=True)
            self.owner[sc] = (ch, ident)
            groups[(shift, ctrl)].append(sc)

        if groups[(False, False)]:
            _send_scancodes(groups[(False, False)], up=False)
        for (shift, ctrl), codes in groups.items():
            if not codes or (shift, ctrl) == (False, False):
                continue
            mod = SC_LSHIFT if shift else SC_LCTRL
            _send_scancodes([mod], up=False)
            time.sleep(0.004)
            _send_scancodes(codes, up=False)
            time.sleep(0.004)
            _send_scancodes([mod], up=True)

    def up(self, ch, ident):
        base, _, _ = split_key(ch)
        sc = SCANCODES.get(base)
        if sc is not None and self.owner.get(sc) == (ch, ident):
            _send_scancodes([sc], up=True)
            del self.owner[sc]

    def release_all(self):
        if self.owner:
            _send_scancodes(list(self.owner), up=True)
            self.owner.clear()

    def sustain_on(self, mode):
        if mode == SUSTAIN_TOGGLE:
            tap(SC_SPACE)
        elif mode == SUSTAIN_HOLD:
            _send_scancodes([SC_SPACE], up=False)

    def sustain_off(self, mode):
        if mode == SUSTAIN_TOGGLE:
            tap(SC_SPACE)
        elif mode == SUSTAIN_HOLD:
            _send_scancodes([SC_SPACE], up=True)

    def suspend(self, mode):
        """Pausa ou Roblox fora de foco: solta tudo para não 'digitar' em outra janela."""
        self.release_all()
        if mode == SUSTAIN_HOLD:
            _send_scancodes([SC_SPACE], up=True)

    def resume(self, mode):
        if mode == SUSTAIN_HOLD:
            _send_scancodes([SC_SPACE], up=False)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Prévia no PC (sintetizador MIDI do Windows, sem dependências)
# ---------------------------------------------------------------------------

winmm = ctypes.WinDLL("winmm")
winmm.midiOutOpen.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.UINT,
                              ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD)
winmm.midiOutShortMsg.argtypes = (wintypes.HANDLE, wintypes.DWORD)
winmm.midiOutReset.argtypes = (wintypes.HANDLE,)
winmm.midiOutClose.argtypes = (wintypes.HANDLE,)
MIDI_MAPPER = 0xFFFFFFFF

NOTE_OF_KEY = {k: n for n, k in NOTE_TO_KEY_88.items()}


class MidiOutput:
    """Saída para os alto-falantes do PC, com a mesma interface do Keyboard."""

    def __init__(self):
        self.handle = wintypes.HANDLE()
        if winmm.midiOutOpen(ctypes.byref(self.handle), MIDI_MAPPER, 0, 0, 0) != 0:
            raise OSError("Não consegui abrir o som MIDI do Windows.")
        self._msg(0xC0, 0)  # instrumento: piano
        self.owner = {}  # nota -> id

    def _msg(self, status, d1, d2=0):
        winmm.midiOutShortMsg(self.handle, status | (d1 << 8) | (d2 << 16))

    def down(self, chars, ident):
        for ch in chars:
            n = NOTE_OF_KEY.get(ch)
            if n is None:
                continue
            if n in self.owner:
                self._msg(0x80, n)
            self._msg(0x90, n, 96)
            self.owner[n] = ident

    def up(self, ch, ident):
        n = NOTE_OF_KEY.get(ch)
        if n is not None and self.owner.get(n) == ident:
            self._msg(0x80, n)
            del self.owner[n]

    def release_all(self):
        for n in self.owner:
            self._msg(0x80, n)
        self.owner.clear()

    def sustain_on(self, mode):
        if mode != SUSTAIN_OFF:
            self._msg(0xB0, 64, 127)

    def sustain_off(self, mode):
        self._msg(0xB0, 64, 0)

    def suspend(self, mode):
        self.release_all()
        self._msg(0xB0, 64, 0)

    def resume(self, mode):
        self.sustain_on(mode)

    def close(self):
        self.release_all()
        winmm.midiOutReset(self.handle)
        winmm.midiOutClose(self.handle)


# ---------------------------------------------------------------------------
# Detecção do Roblox em foco
# ---------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_focus_cache = {}


def roblox_focused():
    """True se a janela em primeiro plano é o Roblox (versão do site ou da Microsoft Store)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cached = _focus_cache.get(hwnd)
    if cached is not None:
        return cached
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        size = wintypes.DWORD(1024)
        pbuf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
            exe = os.path.basename(pbuf.value).lower()
        kernel32.CloseHandle(h)
    ok = "roblox" in exe or buf.value.strip() == "Roblox"
    if len(_focus_cache) > 200:
        _focus_cache.clear()
    _focus_cache[hwnd] = ok
    return ok


# ---------------------------------------------------------------------------
# Partitura em texto (formato Virtual Piano)
#   letra/número   = nota (1 passo)
#   [tuo]          = acorde (notas juntas)
#   [t u o]        = arpejo rápido
#   espaço/quebra  = pausa de 1 passo
#   |              = pausa de 2 passos
#   -              = pausa de 1 passo
#   linhas iniciadas com # são comentários;  "# bpm: 240" define o tempo
#
# Eventos: (tempo_segundos, [teclas], [durações_segundos])
# ---------------------------------------------------------------------------

MAX_HOLD = 4.0


def read_sheet_bpm(text, default=200):
    for line in text.splitlines():
        s = line.strip().lower()
        if s.startswith("#") and "bpm" in s:
            digits = "".join(c if c.isdigit() else " " for c in s.split("bpm", 1)[1]).split()
            if digits:
                return max(20, min(1200, int(digits[0])))
    return default


def parse_sheet(text, bpm):
    """Converte o texto em eventos. Cada nota fica segurada até a próxima começar."""
    step = 60.0 / bpm
    lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
    body = "\n".join(lines)
    raw, group_start = [], []  # raw: (t, teclas, grupo)
    t, i = 0.0, 0
    while i < len(body):
        ch = body[i]
        if ch == "[":
            j = body.find("]", i)
            if j == -1:
                j = len(body)
            inner = body[i + 1:j]
            notes = [c for c in inner if c in KEY_TO_NOTE]
            if notes:
                g = len(group_start)
                group_start.append(t)
                if " " in inner.strip():  # arpejo
                    sub = min(step / max(len(notes), 1), 0.06)
                    for k, n in enumerate(notes):
                        raw.append((t + k * sub, [n], g))
                else:
                    raw.append((t, notes, g))
                t += step
            i = j + 1
            continue
        if ch in KEY_TO_NOTE:
            raw.append((t, [ch], len(group_start)))
            group_start.append(t)
            t += step
        elif ch in " \n\t-":
            t += step
        elif ch == "|":
            t += 2 * step
        i += 1

    events = []
    for et, keys, g in raw:
        nxt = group_start[g + 1] if g + 1 < len(group_start) else et + 2 * step
        dur = max(0.03, min((nxt - et) * 0.95, MAX_HOLD))
        events.append((et, keys, [dur] * len(keys)))
    return events


# ---------------------------------------------------------------------------
# Leitor de MIDI (sem dependências)
# ---------------------------------------------------------------------------

GM_FAMILIES = ["Piano", "Percussão cromática", "Órgão", "Violão/Guitarra", "Baixo", "Cordas",
               "Conjunto/Coral", "Metais", "Palhetas", "Flautas", "Synth (solo)", "Synth (fundo)",
               "Synth (efeitos)", "Étnico", "Percussão", "Efeitos"]


def _read_varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def parse_midi(path):
    """Retorna (notas, partes).
    notas:  lista de (início_seg, nota, canal, duração_seg, parte)
    partes: {parte: {"track", "ch", "name", "family", "count", "avg"}}, parte = "faixa:canal"
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("Não é um arquivo MIDI válido.")
    hlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("MIDI com timing SMPTE não é suportado.")
    tpq = division
    pos = 8 + hlen

    raw_notes = []    # (tick_inicio, tick_fim, nota, canal, faixa)
    tempos = []       # (tick, microssegundos por semínima)
    track_names = {}  # faixa -> nome
    prog_tc = {}      # (faixa, canal) -> programa
    prog_ch = {}      # canal -> programa
    for ti in range(ntracks):
        while pos + 8 <= len(data) and data[pos:pos + 4] != b"MTrk":
            # pula chunks desconhecidos
            clen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
            pos += 8 + clen
        if pos + 8 > len(data):
            break
        tlen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        p, end = pos + 8, min(pos + 8 + tlen, len(data))
        pos = end
        tick, status = 0, 0
        active = {}  # (canal, nota) -> [ticks de início]
        while p < end:
            delta, p = _read_varlen(data, p)
            tick += delta
            b = data[p]
            if b & 0x80:
                status = b
                p += 1
            if status == 0xFF:
                mtype = data[p]
                mlen, p = _read_varlen(data, p + 1)
                if mtype == 0x51 and mlen == 3:
                    tempos.append((tick, int.from_bytes(data[p:p + 3], "big")))
                elif mtype == 0x03 and ti not in track_names:
                    track_names[ti] = data[p:p + mlen].decode("latin-1", "replace").strip()
                elif mtype == 0x2F:
                    p += mlen
                    break
                p += mlen
            elif status in (0xF0, 0xF7):
                slen, p = _read_varlen(data, p)
                p += slen
            else:
                kind = status & 0xF0
                ch = status & 0x0F
                if kind in (0xC0, 0xD0):
                    if kind == 0xC0:
                        prog_tc.setdefault((ti, ch), data[p])
                        prog_ch.setdefault(ch, data[p])
                    p += 1
                else:
                    d1, d2 = data[p], data[p + 1]
                    p += 2
                    if kind == 0x90 and d2 > 0:
                        active.setdefault((ch, d1), []).append(tick)
                    elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                        starts = active.get((ch, d1))
                        if starts:
                            raw_notes.append((starts.pop(0), tick, d1, ch, ti))
        for (ch, n), starts in active.items():  # notas sem "note off"
            for s in starts:
                raw_notes.append((s, s + tpq, n, ch, ti))

    # converte ticks para segundos usando o mapa de tempo
    tempos.sort()
    if not tempos or tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))
    seg_start = []  # (tick, segundos, uspq)
    sec = 0.0
    for i, (tk_, us) in enumerate(tempos):
        if i > 0:
            ptk, psec, pus = seg_start[-1]
            sec = psec + (tk_ - ptk) * pus / 1e6 / tpq
        seg_start.append((tk_, sec, us))

    def tick_to_sec(t):
        lo, hi = 0, len(seg_start) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if seg_start[mid][0] <= t:
                lo = mid
            else:
                hi = mid - 1
        stk, ssec, sus = seg_start[lo]
        return ssec + (t - stk) * sus / 1e6 / tpq

    notes, parts = [], {}
    for s, e, n, c, ti in raw_notes:
        ts = tick_to_sec(s)
        part = f"{ti}:{c}"
        notes.append((ts, n, c, max(0.0, tick_to_sec(e) - ts), part))
        info = parts.get(part)
        if info is None:
            prog = prog_tc.get((ti, c), prog_ch.get(c, 0))
            info = parts[part] = {"track": ti, "ch": c, "name": track_names.get(ti, ""),
                                  "family": "Bateria" if c == 9 else GM_FAMILIES[prog // 8],
                                  "count": 0, "sum": 0}
        info["count"] += 1
        info["sum"] += n
    for info in parts.values():
        info["avg"] = info.pop("sum") / info["count"]
    notes.sort()
    return notes, parts


def default_parts(parts):
    """Todas as partes, menos a bateria."""
    return {p for p, info in parts.items() if info["ch"] != 9}


def melody_part(parts):
    """Chuta qual parte é a melodia: a mais aguda entre as que têm bastante nota."""
    cands = [(p, i) for p, i in parts.items() if i["ch"] != 9]
    if not cands:
        return set()
    biggest = max(i["count"] for _, i in cands)
    cands = [(p, i) for p, i in cands if i["count"] >= biggest * 0.15]
    return {max(cands, key=lambda c: c[1]["avg"])[0]}


def midi_to_events(notes, transpose=0, enabled=None, keys88=False, chord_window=0.025):
    """Agrupa notas próximas em acordes e converte para teclas.
    enabled: conjunto de partes a tocar (None = todas menos bateria)."""
    note_to_key = NOTE_TO_KEY_88 if keys88 else NOTE_TO_KEY
    low, high = min(note_to_key), max(note_to_key)
    events = []
    out_of_range = 0
    for t, n, ch, dur, part in notes:
        if (ch == 9) if enabled is None else (part not in enabled):
            continue
        n += transpose
        if n < low or n > high:
            out_of_range += 1
            while n < low:
                n += 12
            while n > high:
                n -= 12
        key = note_to_key[n]
        dur = max(0.03, min(dur, MAX_HOLD))
        if events and t - events[-1][0] <= chord_window:
            keys, durs = events[-1][1], events[-1][2]
            if key in keys:
                k = keys.index(key)
                durs[k] = max(durs[k], dur)
            else:
                keys.append(key)
                durs.append(dur)
        else:
            events.append((t, [key], [dur]))
    if events:
        t0 = events[0][0]
        events = [(t - t0, k, d) for t, k, d in events]
    return events, out_of_range


def best_transpose(notes, enabled=None, keys88=False):
    """Escolhe a oitava (-24..24) que deixa mais notas dentro do teclado, sem mudar o tom."""
    note_to_key = NOTE_TO_KEY_88 if keys88 else NOTE_TO_KEY
    low, high = min(note_to_key), max(note_to_key)
    pitches = [n for _, n, ch, _, part in notes
               if not ((ch == 9) if enabled is None else (part not in enabled))]
    if not pitches:
        return 0
    best, best_score = 0, None
    for tr in (-24, -12, 0, 12, 24):
        inside = sum(low <= p + tr <= high for p in pitches)
        score = (inside, -abs(tr))
        if best_score is None or score > best_score:
            best, best_score = tr, score
    return best


def events_to_sheet(events, step):
    """Converte eventos em texto Virtual Piano (aproximado), para visualização.
    Notas extras do modo 88 teclas aparecem como {tecla} (= Ctrl + tecla)."""
    def show(k):
        return "{" + k[2:] + "}" if k.startswith(CTRL_PREFIX) else k

    out, last_t = [], 0.0
    for idx, (t, keys, _) in enumerate(events):
        if idx > 0:
            gap = round((t - last_t) / step) - 1
            if gap > 0:
                out.append(" " * min(gap, 8))
        out.append(show(keys[0]) if len(keys) == 1 else "[" + "".join(map(show, keys)) + "]")
        last_t = t
    return "".join(out)


# ---------------------------------------------------------------------------
# Motor de reprodução
# ---------------------------------------------------------------------------

class Player:
    """Toca eventos numa saída (Keyboard = Roblox, MidiOutput = prévia no PC).

    on_progress(i, total, valor, fim):
      i = -1 -> contagem regressiva (valor = segundos restantes)
      i = -2 -> esperando o Roblox ficar em foco
      i >= 0 -> tocando (valor = segundos decorridos)
    """

    def __init__(self, on_progress, on_finish):
        self.on_progress = on_progress
        self.on_finish = on_finish
        self.thread = None
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()

    @property
    def playing(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def paused(self):
        return self.pause_flag.is_set()

    def play(self, events, speed=1.0, delay=0.0, sustain=SUSTAIN_OFF,
             output_factory=Keyboard, focus_check=None):
        self.stop()
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.thread = threading.Thread(
            target=self._run, args=(events, speed, delay, sustain, output_factory, focus_check),
            daemon=True)
        self.thread.start()

    def toggle_pause(self):
        if self.pause_flag.is_set():
            self.pause_flag.clear()
        else:
            self.pause_flag.set()

    def stop(self):
        self.stop_flag.set()
        self.pause_flag.clear()
        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=1)

    def _blocked(self, focus_check):
        return self.pause_flag.is_set() or (focus_check is not None and not focus_check())

    def _wait_unblocked(self, focus_check, total, end_time):
        """Espera sair da pausa e o Roblox voltar ao foco. False se mandaram parar."""
        reported = False
        while not self.stop_flag.is_set():
            if self.pause_flag.is_set():
                time.sleep(0.03)
                continue
            if focus_check is not None and not focus_check():
                if not reported:
                    self.on_progress(-2, total, 0, end_time)
                    reported = True
                time.sleep(0.05)
                continue
            return True
        return False

    def _run(self, events, speed, delay, sustain, output_factory, focus_check):
        total = len(events)
        end_time = events[-1][0] / speed if events else 0
        deadline = time.perf_counter() + delay
        while time.perf_counter() < deadline:
            if self.stop_flag.is_set():
                self.on_finish(False)
                return
            self.on_progress(-1, total, deadline - time.perf_counter(), end_time)
            time.sleep(0.05)
        if not self._wait_unblocked(focus_check, total, end_time):
            self.on_finish(False)
            return

        # linha do tempo: apertar (1) e soltar (0); no mesmo instante, solta antes de apertar
        actions = []
        for idx, (t, keys, durs) in enumerate(events):
            actions.append((t / speed, 1, idx, keys))
            for k, d in zip(keys, durs):
                actions.append(((t + d) / speed, 0, idx, k))
        actions.sort(key=lambda a: (a[0], a[1]))

        try:
            out = output_factory()
        except OSError as e:
            self.on_progress(-3, total, str(e), end_time)
            self.on_finish(False)
            return
        out.sustain_on(sustain)
        completed = False
        try:
            start = time.perf_counter()
            last_report = 0
            for when, kind, idx, keys in actions:
                while True:
                    if self.stop_flag.is_set():
                        return
                    if self._blocked(focus_check):
                        out.suspend(sustain)
                        stopped_at = time.perf_counter()
                        if not self._wait_unblocked(focus_check, total, end_time):
                            return
                        start += time.perf_counter() - stopped_at
                        out.resume(sustain)
                        last_report = 0
                        continue
                    remaining = when - (time.perf_counter() - start)
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.02) if remaining > 0.003 else 0)
                if kind == 1:
                    out.down(keys, idx)
                    now = time.perf_counter()
                    if now - last_report > 0.1 or idx == total - 1:
                        last_report = now
                        self.on_progress(idx + 1, total, now - start, end_time)
                else:
                    out.up(keys, idx)
            completed = True
        finally:
            out.release_all()
            out.sustain_off(sustain)
            out.close()
            self.on_finish(completed)


# ---------------------------------------------------------------------------
# Configurações salvas (config.json ao lado do app)
# ---------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SONGS_DIR = os.path.join(APP_DIR, "musicas")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            cfg.setdefault("songs", {})
            return cfg
    except (OSError, ValueError):
        pass
    return {"songs": {}}


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

SAMPLE_SONGS = {
    "Ode à Alegria (Beethoven).txt":
        "# bpm: 150\n"
        "u u i o o i u y t t y u u  y y |\n"
        "u u i o o i u y t t y u y  t t |\n"
        "y y u t y [ui] u t y [ui] u y t y w |\n"
        "u u i o o i u y t t y u y  t t\n",
    "Brilha Brilha Estrelinha.txt":
        "# bpm: 220\n"
        "[t8] t [o0] o [pq] p [o0]  |\n"
        "[iq] i [u8] u [yw] y [t8]  |\n"
        "o o i i u u y  |\n"
        "o o i i u u y  |\n"
        "[t8] t [o0] o [pq] p [o0]  |\n"
        "[iq] i [u8] u [yw] y [t8]\n",
    "Für Elise (Beethoven).txt":
        "# bpm: 260\n"
        "f D f D f a d s p  | t u p a  | u O a s  |\n"
        "u f D f D f a d s p  | t u p a  | u s a p  |\n"
        "f D f D f a d s p  | t u p a  | u O a s  |\n"
        "u f D f D f a d s p  | t u p a  | u s a p\n",
    "Parabéns pra Você.txt":
        "# bpm: 200\n"
        "t t y  t  i  u   |\n"
        "t t y  t  o  i   |\n"
        "t t s  p  i  u  y   |\n"
        "P P p  i  o  i\n",
}


def ensure_songs_dir():
    if not os.path.isdir(SONGS_DIR):
        os.makedirs(SONGS_DIR)
        for name, text in SAMPLE_SONGS.items():
            with open(os.path.join(SONGS_DIR, name), "w", encoding="utf-8") as f:
                f.write(text)


def _fold(s):
    """Minúsculas e sem acentos, para a busca: 'Já Que' -> 'ja que'."""
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("_", " ").replace("-", " ")


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class App:
    VK_F5, VK_F6 = 0x74, 0x75
    HOLD_OPTIONS = ["Segurar (duração real)", "Curtas (só tocar)"]
    SUSTAIN_OPTIONS = ["Desligado", "Alternar com Espaço", "Segurar Espaço"]

    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        root.title("Auto Piano — Roblox")
        root.geometry(self.cfg.get("geometry", "900x680"))
        root.minsize(760, 560)

        self.player = Player(self._progress_cb, self._finish_cb)
        self.current_path = None
        self.current_fname = None
        self.midi_notes = None
        self.midi_parts = {}
        self.part_ids = []
        self.mode = "game"
        self._hotkey_state = {self.VK_F5: False, self.VK_F6: False}
        self._ui_queue = []
        self._ui_lock = threading.Lock()

        ensure_songs_dir()
        self._build_ui()
        self.refresh_library(select=self.cfg.get("last_song"))
        self._poll()

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        cfg = self.cfg

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # Biblioteca
        left = ttk.LabelFrame(main, text="Músicas", padding=6)
        left.pack(side="left", fill="y")

        search = ttk.Frame(left)
        search.pack(fill="x", pady=(0, 6))
        ttk.Label(search, text="Buscar música na internet:").pack(anchor="w")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.pack(fill="x", pady=2)
        entry.bind("<Return>", lambda e: self.web_search("virtual piano sheet"))
        sbtns = ttk.Frame(search)
        sbtns.pack(fill="x")
        ttk.Button(sbtns, text="🔎 Partitura", command=lambda: self.web_search("virtual piano sheet")
                   ).pack(side="left", expand=True, fill="x")
        ttk.Button(sbtns, text="🔎 MIDI", command=lambda: self.web_search("midi piano")
                   ).pack(side="left", expand=True, fill="x")
        ttk.Button(search, text="📋 Colar partitura copiada", command=self.paste_sheet).pack(fill="x", pady=(2, 0))

        ttk.Separator(left).pack(fill="x", pady=(2, 6))
        fhead = ttk.Frame(left)
        fhead.pack(fill="x")
        ttk.Label(fhead, text="🔍 Procurar nas minhas músicas:").pack(side="left")
        self.count_lbl = ttk.Label(fhead, text="", foreground="#666")
        self.count_lbl.pack(side="right")
        frow = ttk.Frame(left)
        frow.pack(fill="x", pady=(2, 4))
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(frow, textvariable=self.filter_var)
        self.filter_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(frow, text="✕", width=3, command=self.clear_filter).pack(side="left", padx=(2, 0))
        self.filter_var.trace_add("write", lambda *a: self._apply_filter())
        self.filter_entry.bind("<Return>", lambda e: self._pick_filtered(0))
        self.filter_entry.bind("<Down>", lambda e: self._pick_filtered(0, focus=True))
        self.filter_entry.bind("<Escape>", lambda e: self.clear_filter())
        self.root.bind("<Control-f>", lambda e: (self.filter_entry.focus_set(),
                                                 self.filter_entry.select_range(0, "end")))

        self.listbox = tk.Listbox(left, width=32, activestyle="none", exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Return>", lambda e: self.play(countdown=True))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.load_selected())
        self.listbox.bind("<Double-Button-1>", lambda e: self.play(countdown=True))
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Nova", command=self.new_song).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Importar…", command=self.import_files).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Pasta", command=lambda: os.startfile(SONGS_DIR)).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Excluir", command=self.delete_song).pack(side="left", expand=True, fill="x")

        right = ttk.Frame(main, padding=(8, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)

        # Nome + editor
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Nome:").pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.name_var).pack(side="left", fill="x", expand=True, padx=4)
        self.save_btn = ttk.Button(top, text="Salvar", command=self.save_song)
        self.save_btn.pack(side="left")

        # Instrumentos do MIDI (só aparece para MIDI)
        self.parts_frame = ttk.LabelFrame(right, text="Instrumentos do MIDI — clique para ligar/desligar",
                                          padding=6)
        self.parts_list = tk.Listbox(self.parts_frame, selectmode="multiple", height=5,
                                     activestyle="none", exportselection=False)
        self.parts_list.pack(side="left", fill="both", expand=True)
        self.parts_list.bind("<<ListboxSelect>>", lambda e: self._parts_changed())
        pbtns = ttk.Frame(self.parts_frame)
        pbtns.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(pbtns, text="Todos", command=lambda: self._set_parts(set(self.part_ids))).pack(fill="x")
        ttk.Button(pbtns, text="Sem bateria",
                   command=lambda: self._set_parts(default_parts(self.midi_parts))).pack(fill="x", pady=2)
        ttk.Button(pbtns, text="Só melodia",
                   command=lambda: self._set_parts(melody_part(self.midi_parts))).pack(fill="x")

        self.text = tk.Text(right, wrap="word", font=("Consolas", 12), undo=True, height=10)
        self.text.pack(fill="both", expand=True, pady=6)

        # Opções
        opts = ttk.LabelFrame(right, text="Opções", padding=6)
        opts.pack(fill="x")

        ttk.Label(opts, text="BPM (texto):").grid(row=0, column=0, sticky="w")
        self.bpm_var = tk.IntVar(value=200)
        self.bpm_spin = ttk.Spinbox(opts, from_=20, to=1200, increment=10, width=6, textvariable=self.bpm_var)
        self.bpm_spin.grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(opts, text="Transpor (MIDI):").grid(row=0, column=2, sticky="w")
        self.transpose_var = tk.IntVar(value=0)
        self.transpose_spin = ttk.Spinbox(opts, from_=-24, to=24, width=5, textvariable=self.transpose_var,
                                          command=self._update_midi_info)
        self.transpose_spin.grid(row=0, column=3, sticky="w", padx=(4, 16))

        ttk.Label(opts, text="Contagem (s):").grid(row=0, column=4, sticky="w")
        self.delay_var = tk.IntVar(value=cfg.get("delay", 3))
        ttk.Spinbox(opts, from_=0, to=10, width=4, textvariable=self.delay_var).grid(
            row=0, column=5, sticky="w", padx=(4, 0))

        ttk.Label(opts, text="Velocidade:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.speed_var = tk.IntVar(value=100)
        speed_frame = ttk.Frame(opts)
        speed_frame.grid(row=1, column=1, columnspan=5, sticky="we", padx=(4, 0), pady=(6, 0))
        ttk.Scale(speed_frame, from_=25, to=200, variable=self.speed_var,
                  command=lambda v: self.speed_lbl.config(text=f"{int(float(v))}%")).pack(side="left", fill="x", expand=True)
        self.speed_lbl = ttk.Label(speed_frame, text="100%", width=5)
        self.speed_lbl.pack(side="left")

        ttk.Label(opts, text="Notas:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.hold_var = tk.StringVar(value=cfg.get("hold", self.HOLD_OPTIONS[0]))
        if self.hold_var.get() not in self.HOLD_OPTIONS:
            self.hold_var.set(self.HOLD_OPTIONS[0])
        ttk.Combobox(opts, textvariable=self.hold_var, values=self.HOLD_OPTIONS, state="readonly",
                     width=22).grid(row=2, column=1, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(opts, text="Sustain:").grid(row=2, column=2, sticky="w", pady=(6, 0))
        self.sustain_var = tk.StringVar(value=cfg.get("sustain", self.SUSTAIN_OPTIONS[0]))
        if self.sustain_var.get() not in self.SUSTAIN_OPTIONS:
            self.sustain_var.set(self.SUSTAIN_OPTIONS[0])
        ttk.Combobox(opts, textvariable=self.sustain_var, values=self.SUSTAIN_OPTIONS, state="readonly",
                     width=22).grid(row=2, column=3, columnspan=3, sticky="w", padx=(4, 0), pady=(6, 0))

        checks = ttk.Frame(opts)
        checks.grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.focus_var = tk.BooleanVar(value=cfg.get("focus_only", True))
        ttk.Checkbutton(checks, text="Só tocar com o Roblox em foco",
                        variable=self.focus_var).pack(side="left")
        self.keys88_var = tk.BooleanVar(value=cfg.get("keys88", False))
        ttk.Checkbutton(checks, text="88 teclas (Ctrl)", variable=self.keys88_var,
                        command=self._keys_mode_changed).pack(side="left", padx=(16, 0))
        self.topmost_var = tk.BooleanVar(value=cfg.get("topmost", False))
        ttk.Checkbutton(checks, text="Sempre no topo", variable=self.topmost_var,
                        command=lambda: self.root.attributes("-topmost", self.topmost_var.get())
                        ).pack(side="left", padx=(16, 0))
        self.root.attributes("-topmost", self.topmost_var.get())
        opts.columnconfigure(6, weight=1)

        # Controles
        ctrl = ttk.Frame(right)
        ctrl.pack(fill="x", pady=(8, 0))
        ttk.Button(ctrl, text="▶ Tocar", command=lambda: self.play(countdown=True)).pack(side="left")
        ttk.Button(ctrl, text="🔊 Prévia", command=self.preview).pack(side="left", padx=(4, 0))
        self.pause_btn = ttk.Button(ctrl, text="⏸ Pausar (F5)", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=4)
        ttk.Button(ctrl, text="■ Parar (F6)", command=self.stop).pack(side="left")
        self.progress = ttk.Progressbar(ctrl, maximum=1000)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.time_lbl = ttk.Label(ctrl, text="0:00 / 0:00", width=12)
        self.time_lbl.pack(side="left")

        self.status = ttk.Label(right, text="Escolha uma música. Clique em Tocar e vá para o Roblox "
                                            "(ou, com o Roblox aberto, aperte F5).",
                                foreground="#555")
        self.status.pack(fill="x", pady=(6, 0))

    # ---------- Configurações ----------
    def _int(self, var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    def _store_song_settings(self):
        """Guarda velocidade, BPM, transposição e instrumentos da música atual."""
        if not self.current_fname:
            return
        s = self.cfg["songs"].setdefault(self.current_fname, {})
        s["speed"] = self._int(self.speed_var, 100)
        if self.midi_notes is not None:
            s["transpose"] = self._int(self.transpose_var, 0)
            s["keys88"] = self.keys88_var.get()
            s["parts"] = sorted(self._enabled_parts())
        else:
            s["bpm"] = self._int(self.bpm_var, 200)

    def save_settings(self):
        self._store_song_settings()
        self.cfg.update({
            "focus_only": self.focus_var.get(),
            "topmost": self.topmost_var.get(),
            "delay": self._int(self.delay_var, 3),
            "hold": self.hold_var.get(),
            "sustain": self.sustain_var.get(),
            "keys88": self.keys88_var.get(),
            "last_song": self.current_fname,
        })
        if self.root.state() == "normal":
            self.cfg["geometry"] = self.root.geometry()
        save_config(self.cfg)

    def on_close(self):
        self.player.stop()
        self.save_settings()
        self.root.after_cancel(self._after_id)
        self.root.destroy()

    # ---------- Biblioteca ----------
    def refresh_library(self, select=None):
        self.all_files = sorted(
            (f for f in os.listdir(SONGS_DIR) if f.lower().endswith((".txt", ".mid", ".midi"))),
            key=lambda f: _fold(f))
        if select and select in self.all_files and not self._matches(select):
            self.filter_var.set("")  # mostra a música recém-salva/importada
        self._apply_filter()
        if select and select in self.files:
            idx = self.files.index(select)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
            self.load_selected()

    def _matches(self, fname):
        words = _fold(self.filter_var.get()).split()
        name = _fold(os.path.splitext(fname)[0])
        return all(w in name for w in words)

    def _apply_filter(self):
        if not hasattr(self, "all_files"):
            return
        self.files = [f for f in self.all_files if self._matches(f)]
        self.listbox.delete(0, "end")
        for f in self.files:
            icon = "🎹 " if f.lower().endswith((".mid", ".midi")) else "📄 "
            self.listbox.insert("end", icon + os.path.splitext(f)[0])
        if not self.files and self.all_files:
            self.listbox.insert("end", "  Nenhuma música encontrada")
            self.listbox.itemconfig(0, foreground="#888", selectbackground="white",
                                    selectforeground="#888")
        if self.current_fname in self.files:  # mantém a música atual marcada
            idx = self.files.index(self.current_fname)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
        total = len(self.all_files)
        self.count_lbl.config(text=f"{len(self.files)} de {total}" if self.filter_var.get().strip()
                              else f"{total} músicas")

    def _pick_filtered(self, idx, focus=False):
        if not self.files:
            return "break"
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)
        self.listbox.see(idx)
        self.load_selected()
        if focus:
            self.listbox.focus_set()
        return "break"

    def clear_filter(self):
        self.filter_var.set("")
        self.filter_entry.focus_set()

    def load_selected(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.files):
            return
        fname = self.files[sel[0]]
        if fname == self.current_fname and self.current_path:
            return
        self._store_song_settings()
        path = os.path.join(SONGS_DIR, fname)
        s = self.cfg["songs"].get(fname, {})
        self.current_path = path
        self.current_fname = fname
        self.name_var.set(os.path.splitext(fname)[0])
        self.speed_var.set(s.get("speed", 100))
        self.speed_lbl.config(text=f"{self.speed_var.get()}%")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if fname.lower().endswith((".mid", ".midi")):
            try:
                self.midi_notes, self.midi_parts = parse_midi(path)
            except Exception as e:
                self.midi_notes, self.midi_parts = None, {}
                self.parts_frame.pack_forget()
                messagebox.showerror("Erro no MIDI", f"Não consegui ler este MIDI:\n{e}")
                return
            if "keys88" in s:
                self.keys88_var.set(s["keys88"])
            self._fill_parts(set(s["parts"]) & set(self.midi_parts) if "parts" in s
                             else default_parts(self.midi_parts))
            if "transpose" in s:
                self.transpose_var.set(s["transpose"])
            else:
                self.transpose_var.set(best_transpose(self.midi_notes, self._enabled_parts(),
                                                      self.keys88_var.get()))
            self._update_midi_info()
        else:
            self.midi_notes, self.midi_parts = None, {}
            self.parts_frame.pack_forget()
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.text.insert("1.0", content)
            self.bpm_var.set(s.get("bpm", read_sheet_bpm(content, self.bpm_var.get())))
            self.status.config(text=f"Partitura carregada: {self.name_var.get()}")

    # ---------- Instrumentos do MIDI ----------
    def _fill_parts(self, enabled):
        self.part_ids = sorted(self.midi_parts, key=lambda p: (self.midi_parts[p]["track"],
                                                               self.midi_parts[p]["ch"]))
        self.parts_list.delete(0, "end")
        for p in self.part_ids:
            info = self.midi_parts[p]
            name = info["name"] or f"Faixa {info['track'] + 1}"
            self.parts_list.insert("end", f"{info['family']} — {name}  ({info['count']} notas)")
        self._select_parts(enabled)
        self.parts_frame.pack(fill="x", pady=(6, 0), before=self.text)

    def _select_parts(self, enabled):
        self.parts_list.selection_clear(0, "end")
        for i, p in enumerate(self.part_ids):
            if p in enabled:
                self.parts_list.selection_set(i)

    def _enabled_parts(self):
        return {self.part_ids[i] for i in self.parts_list.curselection()}

    def _set_parts(self, enabled):
        if self.midi_notes is None:
            return
        self._select_parts(enabled)
        self._parts_changed()

    def _parts_changed(self):
        self.transpose_var.set(best_transpose(self.midi_notes, self._enabled_parts(), self.keys88_var.get()))
        self._update_midi_info()

    def _update_midi_info(self):
        if self.midi_notes is None:
            return
        events, oor = self._midi_events()
        dur = events[-1][0] if events else 0
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0",
                         f"Arquivo MIDI — {len(self._enabled_parts())} de {len(self.part_ids)} instrumentos ligados\n\n"
                         f"Notas/acordes: {len(events)}\n"
                         f"Duração: {fmt_time(dur)}\n"
                         f"Notas fora do teclado (ajustadas de oitava): {oor}\n\n"
                         f"Dica: use \"Transpor\" para mudar o tom e \"🔊 Prévia\" para ouvir no PC.\n\n"
                         f"Prévia em formato Virtual Piano:\n\n"
                         + events_to_sheet(events[:400], 0.15))
        self.text.config(state="disabled")
        self.status.config(text=f"MIDI carregado: {self.name_var.get()}")

    def _midi_events(self):
        return midi_to_events(self.midi_notes, self._int(self.transpose_var, 0),
                              self._enabled_parts(), self.keys88_var.get())

    def _keys_mode_changed(self):
        if self.midi_notes is not None:
            self.transpose_var.set(best_transpose(self.midi_notes, self._enabled_parts(), self.keys88_var.get()))
            self._update_midi_info()

    # ---------- Edição ----------
    def new_song(self):
        self._store_song_settings()
        self.current_path = None
        self.current_fname = None
        self.midi_notes, self.midi_parts = None, {}
        self.parts_frame.pack_forget()
        self.listbox.selection_clear(0, "end")
        self.name_var.set("Nova música")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "# bpm: 200\n# Cole aqui a partitura no formato Virtual Piano\n")
        self.text.focus_set()

    def web_search(self, suffix):
        name = self.search_var.get().strip()
        if not name:
            self.status.config(text="Digite o nome da música no campo de busca.")
            return
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(f"{name} {suffix}"))
        self.status.config(text="Copie a partitura no navegador (Ctrl+C) e clique em \"Colar partitura copiada\".")

    def paste_sheet(self):
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            clip = ""
        notes = sum(c in KEY_TO_NOTE for c in clip)
        if notes < 4:
            messagebox.showinfo("Colar", "A área de transferência não parece ter uma partitura.\n"
                                         "Selecione a partitura no site, copie com Ctrl+C e tente de novo.")
            return
        self.new_song()
        self.name_var.set(self.search_var.get().strip() or "Nova música")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", clip.strip() + "\n")
        self.bpm_var.set(read_sheet_bpm(clip, 200))
        self.save_song()
        self.status.config(text=f"Partitura colada e salva: {self.name_var.get()} ({notes} notas). "
                                f"Ajuste o BPM se precisar e clique em Salvar.")

    def save_song(self):
        if self.midi_notes is not None:
            messagebox.showinfo("MIDI", "Arquivos MIDI não precisam ser salvos — já estão na biblioteca.")
            return
        name = self.name_var.get().strip()
        name = "".join(c for c in name if c not in '\\/:*?"<>|').strip()
        if not name:
            messagebox.showwarning("Nome", "Dê um nome para a música.")
            return
        content = self.text.get("1.0", "end-1c")
        bpm = self._int(self.bpm_var, 200)
        lines = [l for l in content.splitlines() if not (l.strip().lower().startswith("#") and "bpm" in l.lower())]
        content = f"# bpm: {bpm}\n" + "\n".join(lines)
        fname = name + ".txt"
        path = os.path.join(SONGS_DIR, fname)
        old = self.current_fname
        if self.current_path and os.path.abspath(self.current_path) != os.path.abspath(path) \
                and self.current_path.lower().endswith(".txt") and os.path.exists(self.current_path):
            os.remove(self.current_path)  # renomeado
            if old in self.cfg["songs"]:
                self.cfg["songs"][fname] = self.cfg["songs"].pop(old)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.current_path = path
        self.current_fname = fname
        self._store_song_settings()
        self.refresh_library(select=fname)
        self.status.config(text=f"Salvo: {name}")

    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="Importar músicas",
            filetypes=[("Músicas", "*.mid *.midi *.txt"), ("MIDI", "*.mid *.midi"), ("Texto", "*.txt")])
        last = None
        for p in paths:
            dest = os.path.join(SONGS_DIR, os.path.basename(p))
            if os.path.abspath(p) != os.path.abspath(dest):
                shutil.copy2(p, dest)
            last = os.path.basename(p)
        if last:
            self.refresh_library(select=last)

    def delete_song(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.files):
            return
        fname = self.files[sel[0]]
        if messagebox.askyesno("Excluir", f"Excluir \"{fname}\"?"):
            os.remove(os.path.join(SONGS_DIR, fname))
            self.cfg["songs"].pop(fname, None)
            self.current_path = None
            self.current_fname = None
            self.midi_notes, self.midi_parts = None, {}
            self.parts_frame.pack_forget()
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.refresh_library()

    # ---------- Reprodução ----------
    def _build_events(self):
        if self.midi_notes is not None:
            events, _ = self._midi_events()
            return events
        return parse_sheet(self.text.get("1.0", "end-1c"), self._int(self.bpm_var, 200))

    def _start(self, mode, delay):
        events = self._build_events()
        if not events:
            self.status.config(text="Nada para tocar — escolha uma música (ou ligue algum instrumento do MIDI).")
            return
        speed = max(0.25, self.speed_var.get() / 100.0)
        if self.hold_var.get() == self.HOLD_OPTIONS[1]:  # notas curtas
            events = [(t, keys, [0.015 * speed] * len(keys)) for t, keys, _ in events]
        sustain = self.SUSTAIN_OPTIONS.index(self.sustain_var.get())
        self.mode = mode
        if mode == "preview":
            self.player.play(events, speed, 0, sustain, output_factory=MidiOutput)
        else:
            focus = roblox_focused if self.focus_var.get() else None
            self.player.play(events, speed, delay, sustain, output_factory=Keyboard, focus_check=focus)
        self.pause_btn.config(text="⏸ Pausar (F5)")
        self.save_settings()

    def play(self, countdown=True):
        delay = float(self._int(self.delay_var, 3)) if countdown else 0.3
        self._start("game", delay)

    def preview(self):
        self._start("preview", 0)
        if self.player.playing:
            self.status.config(text="🔊 Prévia tocando no PC…   F6 = parar")

    def toggle_pause(self):
        if not self.player.playing:
            return
        self.player.toggle_pause()
        paused = self.player.paused
        self.pause_btn.config(text="▶ Continuar (F5)" if paused else "⏸ Pausar (F5)")
        self.status.config(text="Pausado." if paused else "Tocando…")

    def stop(self):
        self.player.stop()
        self.progress["value"] = 0
        self.pause_btn.config(text="⏸ Pausar (F5)")

    # callbacks do thread do player -> fila para a UI
    def _progress_cb(self, i, total, value, end):
        with self._ui_lock:
            self._ui_queue.append(("progress", i, total, value, end))

    def _finish_cb(self, completed):
        with self._ui_lock:
            self._ui_queue.append(("finish", completed))

    def _poll(self):
        # hotkeys globais
        for vk in (self.VK_F5, self.VK_F6):
            down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            if down and not self._hotkey_state[vk]:
                if vk == self.VK_F5:
                    if self.player.playing:
                        self.toggle_pause()
                    else:
                        self.play(countdown=False)
                else:
                    self.stop()
                    self.status.config(text="Parado.")
            self._hotkey_state[vk] = down

        with self._ui_lock:
            items, self._ui_queue = self._ui_queue, []
        for item in items:
            if item[0] == "progress":
                _, i, total, value, end = item
                if i == -1:
                    self.status.config(text=f"Começando em {value:.1f}s… clique na janela do Roblox!")
                    self.progress["value"] = 0
                elif i == -2:
                    self.status.config(text="⏳ Esperando o Roblox… clique na janela do jogo para tocar "
                                            "(o app pausa quando o Roblox sai do foco).")
                elif i == -3:
                    self.status.config(text=f"Erro: {value}")
                else:
                    self.progress["value"] = 1000 * i / max(total, 1)
                    self.time_lbl.config(text=f"{fmt_time(value)} / {fmt_time(end)}")
                    if not self.player.paused:
                        where = "🔊 Prévia no PC" if self.mode == "preview" else "Tocando no Roblox"
                        self.status.config(text=f"{where}… ({i}/{total})   F5 = pausar, F6 = parar")
            elif item[0] == "finish":
                if item[1]:
                    self.progress["value"] = 1000
                    self.status.config(text="Música terminada! 🎵")
                self.pause_btn.config(text="⏸ Pausar (F5)")
        self._after_id = self.root.after(30, self._poll)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
