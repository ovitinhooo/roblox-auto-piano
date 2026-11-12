"""
Auto Piano Player para Roblox
Toca músicas no piano do Roblox simulando o teclado (layout "Virtual Piano", 61 teclas).

Aceita:
  - Partituras em texto no formato Virtual Piano (.txt)
  - Arquivos MIDI (.mid / .midi)

Atalhos globais (funcionam com o Roblox em foco):
  F5 = tocar / pausar
  F6 = parar
"""

import ctypes
import os
import shutil
import struct
import sys
import threading
import time
import tkinter as tk
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


def split_key(ch):
    """Retorna (tecla_base, precisa_shift)."""
    if ch in SHIFT_SYMBOLS:
        return SHIFT_SYMBOLS[ch], True
    if ch.isalpha() and ch.isupper():
        return ch.lower(), True
    return ch, False


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


def press_keys(chars, hold=0.012):
    """Pressiona um conjunto de teclas ao mesmo tempo (acorde)."""
    normal, shifted = [], []
    for ch in chars:
        base, needs_shift = split_key(ch)
        sc = SCANCODES.get(base)
        if sc is None:
            continue
        (shifted if needs_shift else normal).append(sc)

    if normal:
        _send_scancodes(normal, up=False)
        time.sleep(hold)
        _send_scancodes(normal, up=True)
    if shifted:
        _send_scancodes([SC_LSHIFT], up=False)
        time.sleep(0.004)
        _send_scancodes(shifted, up=False)
        time.sleep(hold)
        _send_scancodes(shifted, up=True)
        _send_scancodes([SC_LSHIFT], up=True)


# ---------------------------------------------------------------------------
# Partitura em texto (formato Virtual Piano)
#   letra/número   = nota (1 passo)
#   [tuo]          = acorde (notas juntas)
#   [t u o]        = arpejo rápido
#   espaço/quebra  = pausa de 1 passo
#   |              = pausa de 2 passos
#   -              = pausa de 1 passo
#   linhas iniciadas com # são comentários;  "# bpm: 240" define o tempo
# ---------------------------------------------------------------------------

def read_sheet_bpm(text, default=200):
    for line in text.splitlines():
        s = line.strip().lower()
        if s.startswith("#") and "bpm" in s:
            digits = "".join(c if c.isdigit() else " " for c in s.split("bpm", 1)[1]).split()
            if digits:
                return max(20, min(1200, int(digits[0])))
    return default


def parse_sheet(text, bpm):
    """Converte o texto em lista de eventos (tempo_segundos, [teclas])."""
    step = 60.0 / bpm
    lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
    body = "\n".join(lines)
    events, t, i = [], 0.0, 0
    while i < len(body):
        ch = body[i]
        if ch == "[":
            j = body.find("]", i)
            if j == -1:
                j = len(body)
            inner = body[i + 1:j]
            notes = [c for c in inner if c in KEY_TO_NOTE]
            if notes:
                if " " in inner.strip():  # arpejo
                    sub = min(step / max(len(notes), 1), 0.06)
                    for k, n in enumerate(notes):
                        events.append((t + k * sub, [n]))
                else:
                    events.append((t, notes))
                t += step
            i = j + 1
            continue
        if ch in KEY_TO_NOTE:
            events.append((t, [ch]))
            t += step
        elif ch in " \n\t-":
            t += step
        elif ch == "|":
            t += 2 * step
        i += 1
    return events


# ---------------------------------------------------------------------------
# Leitor de MIDI (sem dependências)
# ---------------------------------------------------------------------------

def _read_varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def parse_midi(path):
    """Retorna (lista de (tempo_seg, nota, canal), duração_total)."""
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

    raw_notes = []   # (tick, nota, canal)
    tempos = []      # (tick, microssegundos por semínima)
    for _ in range(ntracks):
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
                    p += 1
                else:
                    d1, d2 = data[p], data[p + 1]
                    p += 2
                    if kind == 0x90 and d2 > 0:
                        raw_notes.append((tick, d1, ch))

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

    notes = sorted((tick_to_sec(t), n, c) for t, n, c in raw_notes)
    return notes


def midi_to_events(notes, transpose=0, skip_drums=True, chord_window=0.025):
    """Agrupa notas próximas em acordes e converte para teclas."""
    events = []
    out_of_range = 0
    for t, n, ch in notes:
        if skip_drums and ch == 9:
            continue
        n += transpose
        if n < LOW_NOTE or n > HIGH_NOTE:
            out_of_range += 1
            while n < LOW_NOTE:
                n += 12
            while n > HIGH_NOTE:
                n -= 12
        key = NOTE_TO_KEY[n]
        if events and t - events[-1][0] <= chord_window:
            if key not in events[-1][1]:
                events[-1][1].append(key)
        else:
            events.append((t, [key]))
    if events:
        t0 = events[0][0]
        events = [(t - t0, k) for t, k in events]
    return events, out_of_range


def best_transpose(notes, skip_drums=True):
    """Escolhe a oitava (-24..24) que deixa mais notas dentro do teclado, sem mudar o tom."""
    pitches = [n for _, n, ch in notes if not (skip_drums and ch == 9)]
    if not pitches:
        return 0
    best, best_score = 0, None
    for tr in (-24, -12, 0, 12, 24):
        inside = sum(LOW_NOTE <= p + tr <= HIGH_NOTE for p in pitches)
        score = (inside, -abs(tr))
        if best_score is None or score > best_score:
            best, best_score = tr, score
    return best


def events_to_sheet(events, step):
    """Converte eventos em texto Virtual Piano (aproximado), para edição."""
    out, last_t = [], 0.0
    for idx, (t, keys) in enumerate(events):
        if idx > 0:
            gap = round((t - last_t) / step) - 1
            if gap > 0:
                out.append(" " * min(gap, 8))
        out.append(keys[0] if len(keys) == 1 else "[" + "".join(keys) + "]")
        last_t = t
    return "".join(out)


# ---------------------------------------------------------------------------
# Motor de reprodução
# ---------------------------------------------------------------------------

class Player:
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

    def play(self, events, speed=1.0, delay=0.0):
        self.stop()
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.thread = threading.Thread(target=self._run, args=(events, speed, delay), daemon=True)
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

    def _run(self, events, speed, delay):
        total = len(events)
        end_time = events[-1][0] / speed if events else 0
        t_deadline = time.perf_counter() + delay
        while time.perf_counter() < t_deadline:
            if self.stop_flag.is_set():
                self.on_finish(False)
                return
            self.on_progress(-1, total, t_deadline - time.perf_counter(), end_time)
            time.sleep(0.05)

        start = time.perf_counter()
        last_report = 0
        for i, (t, keys) in enumerate(events):
            target = t / speed
            while True:
                if self.stop_flag.is_set():
                    self.on_finish(False)
                    return
                if self.pause_flag.is_set():
                    paused_at = time.perf_counter()
                    while self.pause_flag.is_set() and not self.stop_flag.is_set():
                        time.sleep(0.03)
                    start += time.perf_counter() - paused_at
                    continue
                remaining = target - (time.perf_counter() - start)
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.02) if remaining > 0.003 else 0)
            press_keys(keys)
            now = time.perf_counter()
            if now - last_report > 0.1 or i == total - 1:
                last_report = now
                self.on_progress(i + 1, total, now - start, end_time)
        self.on_finish(True)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
SONGS_DIR = os.path.join(APP_DIR, "musicas")

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


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class App:
    VK_F5, VK_F6 = 0x74, 0x75

    def __init__(self, root):
        self.root = root
        root.title("Auto Piano — Roblox")
        root.geometry("860x600")
        root.minsize(720, 480)

        self.player = Player(self._progress_cb, self._finish_cb)
        self.current_path = None
        self.midi_notes = None
        self._hotkey_state = {self.VK_F5: False, self.VK_F6: False}
        self._ui_queue = []
        self._ui_lock = threading.Lock()

        ensure_songs_dir()
        self._build_ui()
        self.refresh_library()
        self._poll()

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # Biblioteca
        left = ttk.LabelFrame(main, text="Músicas", padding=6)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=32, activestyle="none", exportselection=False)
        self.listbox.pack(fill="both", expand=True)
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

        self.text = tk.Text(right, wrap="word", font=("Consolas", 12), undo=True, height=12)
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

        self.drums_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Ignorar bateria", variable=self.drums_var,
                        command=self._update_midi_info).grid(row=0, column=4, sticky="w")

        ttk.Label(opts, text="Velocidade:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.speed_var = tk.IntVar(value=100)
        speed_frame = ttk.Frame(opts)
        speed_frame.grid(row=1, column=1, columnspan=3, sticky="we", pady=(6, 0))
        ttk.Scale(speed_frame, from_=25, to=200, variable=self.speed_var,
                  command=lambda v: self.speed_lbl.config(text=f"{int(float(v))}%")).pack(side="left", fill="x", expand=True)
        self.speed_lbl = ttk.Label(speed_frame, text="100%", width=5)
        self.speed_lbl.pack(side="left")

        ttk.Label(opts, text="Contagem (s):").grid(row=1, column=4, sticky="w", pady=(6, 0))
        self.delay_var = tk.IntVar(value=3)
        ttk.Spinbox(opts, from_=0, to=10, width=4, textvariable=self.delay_var).grid(row=1, column=5, sticky="w", pady=(6, 0))

        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Sempre no topo", variable=self.topmost_var,
                        command=lambda: self.root.attributes("-topmost", self.topmost_var.get())
                        ).grid(row=0, column=5, sticky="w")
        opts.columnconfigure(1, weight=1)

        # Controles
        ctrl = ttk.Frame(right)
        ctrl.pack(fill="x", pady=(8, 0))
        ttk.Button(ctrl, text="▶ Tocar", command=lambda: self.play(countdown=True)).pack(side="left")
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

    # ---------- Biblioteca ----------
    def refresh_library(self, select=None):
        self.files = sorted(
            f for f in os.listdir(SONGS_DIR)
            if f.lower().endswith((".txt", ".mid", ".midi"))
        )
        self.listbox.delete(0, "end")
        for f in self.files:
            icon = "🎹 " if f.lower().endswith((".mid", ".midi")) else "📄 "
            self.listbox.insert("end", icon + os.path.splitext(f)[0])
        if select and select in self.files:
            idx = self.files.index(select)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
            self.load_selected()

    def load_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        fname = self.files[sel[0]]
        path = os.path.join(SONGS_DIR, fname)
        self.current_path = path
        self.name_var.set(os.path.splitext(fname)[0])
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if fname.lower().endswith((".mid", ".midi")):
            try:
                self.midi_notes = parse_midi(path)
            except Exception as e:
                self.midi_notes = None
                messagebox.showerror("Erro no MIDI", f"Não consegui ler este MIDI:\n{e}")
                return
            self.transpose_var.set(best_transpose(self.midi_notes, self.drums_var.get()))
            self._update_midi_info()
        else:
            self.midi_notes = None
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.text.insert("1.0", content)
            self.bpm_var.set(read_sheet_bpm(content, self.bpm_var.get()))
            self.status.config(text=f"Partitura carregada: {self.name_var.get()}")

    def _update_midi_info(self):
        if self.midi_notes is None:
            return
        events, oor = self._midi_events()
        dur = events[-1][0] if events else 0
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0",
                         f"Arquivo MIDI\n\n"
                         f"Notas/acordes: {len(events)}\n"
                         f"Duração: {fmt_time(dur)}\n"
                         f"Notas fora do teclado (ajustadas de oitava): {oor}\n\n"
                         f"Dica: use \"Transpor\" para mudar o tom. O valor inicial foi escolhido\n"
                         f"automaticamente para caber o máximo de notas no piano.\n\n"
                         f"Prévia em formato Virtual Piano:\n\n"
                         + events_to_sheet(events[:400], 0.15))
        self.text.config(state="disabled")
        self.status.config(text=f"MIDI carregado: {self.name_var.get()}")

    def _midi_events(self):
        try:
            tr = int(self.transpose_var.get())
        except (tk.TclError, ValueError):
            tr = 0
        return midi_to_events(self.midi_notes, tr, self.drums_var.get())

    def new_song(self):
        self.current_path = None
        self.midi_notes = None
        self.listbox.selection_clear(0, "end")
        self.name_var.set("Nova música")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "# bpm: 200\n# Cole aqui a partitura no formato Virtual Piano\n")
        self.text.focus_set()

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
        try:
            bpm = int(self.bpm_var.get())
        except (tk.TclError, ValueError):
            bpm = 200
        lines = [l for l in content.splitlines() if not (l.strip().lower().startswith("#") and "bpm" in l.lower())]
        content = f"# bpm: {bpm}\n" + "\n".join(lines)
        fname = name + ".txt"
        path = os.path.join(SONGS_DIR, fname)
        if self.current_path and os.path.abspath(self.current_path) != os.path.abspath(path) \
                and self.current_path.lower().endswith(".txt") and os.path.exists(self.current_path):
            os.remove(self.current_path)  # renomeado
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.current_path = path
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
        if not sel:
            return
        fname = self.files[sel[0]]
        if messagebox.askyesno("Excluir", f"Excluir \"{fname}\"?"):
            os.remove(os.path.join(SONGS_DIR, fname))
            self.current_path = None
            self.midi_notes = None
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.refresh_library()

    # ---------- Reprodução ----------
    def _build_events(self):
        if self.midi_notes is not None:
            events, _ = self._midi_events()
            return events
        try:
            bpm = int(self.bpm_var.get())
        except (tk.TclError, ValueError):
            bpm = 200
        return parse_sheet(self.text.get("1.0", "end-1c"), bpm)

    def play(self, countdown=True):
        events = self._build_events()
        if not events:
            self.status.config(text="Nada para tocar — escolha ou escreva uma música.")
            return
        speed = max(0.25, self.speed_var.get() / 100.0)
        try:
            delay = float(self.delay_var.get()) if countdown else 0.3
        except (tk.TclError, ValueError):
            delay = 3.0
        self.player.play(events, speed, delay)
        self.pause_btn.config(text="⏸ Pausar (F5)")

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
    def _progress_cb(self, i, total, elapsed, end):
        with self._ui_lock:
            self._ui_queue.append(("progress", i, total, elapsed, end))

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
                _, i, total, elapsed, end = item
                if i < 0:
                    self.status.config(text=f"Começando em {elapsed:.1f}s… clique na janela do Roblox!")
                    self.progress["value"] = 0
                else:
                    self.progress["value"] = 1000 * i / max(total, 1)
                    self.time_lbl.config(text=f"{fmt_time(elapsed)} / {fmt_time(end)}")
                    if not self.player.paused:
                        self.status.config(text=f"Tocando… ({i}/{total})   F5 = pausar, F6 = parar")
            elif item[0] == "finish":
                if item[1]:
                    self.progress["value"] = 1000
                    self.status.config(text="Música terminada! 🎵")
                self.pause_btn.config(text="⏸ Pausar (F5)")
        self.root.after(30, self._poll)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
