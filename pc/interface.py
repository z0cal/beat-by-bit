"""
Beat by Bit - Semestre 2
Renderer puro: recebe pacotes da FPGA via UART e renderiza o jogo.
Nenhuma logica de jogo aqui — tudo roda na FPGA (Verilog).

Protocolo UART (115200 baud, 8N1):
  Pacote de 22 bytes a ~60fps:
    [0]  0x55              marcador de inicio
    [1]  {eq_lo, state}    state (bits 3:0): 0=IDLE 1=COUNTDOWN 2=PLAY 3=PAUSE
                           4=WIN 5=LOSE 6=SELECT 7=SETTINGS
                           bits 5:4 = GRAVES, bits 7:6 = MEDIOS
    [2]  {eq_hi, countdown} countdown (bits 3:0): 3,2,1,0
                           bits 5:4 = AGUDOS, bits 7:6 = BRILHO
    [3-5]  trilha 0 — ages das 3 notas (0xFF = vazio)
    [6-8]  trilha 1
    [9-11] trilha 2
    [12-14]trilha 3
    [15] score high byte
    [16] score low byte
    [17] misses (0-10)
    [18] combo
    [19] {song_id, frame_counter[13:8]}  (2 + 6 bits)
    [20] frame_counter[7:0]   (8 bits baixos)
    [21] 0xAA              marcador de fim

Conversao age -> Y: y = age * NOTE_SPEED  (NOTE_SPEED=4)

Niveis do atenuador (eq): 0=0 dB, 1=-6 dB, 2=-12 dB, 3=-18 dB por faixa.
"""

import pygame

# Tamanho do buffer de audio.
# Maior buffer = menos crackling em maquinas lentas, mas mais latencia.
# 16384 @ 44100Hz ≈ 371ms ≈ 22 frames a 60fps — compensado no chart (AUDIO_LATENCY_FRAMES).
_AUDIO_BUFFER = 4096
# Passos que a simulacao recupera de uma vez (10 s). Recupera tudo para nao
# perder a sincronia com o audio; acima disso (ex.: maquina suspensa) reancora.
_SIM_MAX_CATCHUP = 600

# pre_init DEVE ser chamado antes de pygame.init() para garantir as configuracoes
# corretas do mixer desde o inicio (evita double-init e distorcao em algumas maquinas).
pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=_AUDIO_BUFFER)
pygame.init()

import io
import threading
import time
from collections import Counter
import queue
import wave
import argparse
import math
import random
import os
from sys import exit

import numpy as np

try:
    import serial

    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# ─── Constantes de tela e layout ─────────────────────────────────────────────

SCREEN_W, SCREEN_H = 1800, 1080
FPS = 60
# Latencia do buffer de audio em frames: compensa o atraso entre pygame.music.play()
# e o som efetivamente sair pelos alto-falantes.
AUDIO_LATENCY_FRAMES = round(_AUDIO_BUFFER / 44100 * FPS)  # ≈ 22 frames

# ─── Fatores de escala (base = 800×600) ──────────────────────────────────────
_BASE_W, _BASE_H = 800, 600
SX = SCREEN_W / _BASE_W  # escala horizontal
SY = SCREEN_H / _BASE_H  # escala vertical
SS = min(SX, SY)  # escala uniforme (elementos quadrados)


def _px(v):
    return int(v * SX)  # escala um valor em X


def _py(v):
    return int(v * SY)  # escala um valor em Y


def _ps(v):
    return int(v * SS)  # escala uniforme


NOTE_SPEED = 4 * SY  # escala com SY para manter HIT_AGE constante
HIT_Y = _py(520)
HIT_AGE = HIT_Y // NOTE_SPEED  # = 130 independente da resolucao
NOTE_RADIUS = _ps(22)
GLOW_SIZE = _ps(120)
GLOW_CENTER = GLOW_SIZE // 2
TRACK_W = _px(80)

TRACK_X = [_px(x) for x in [175, 300, 500, 625]]
TRACK_LABELS = ["D", "F", "J", "K"]
TRACK_COLORS = [
    (255, 220, 0),    # amarelo
    (0, 180, 255),    # azul
    (0, 255, 100),    # verde
    (255, 255, 255),  # branco
]

BG_COLOR = (8, 12, 25)
TRACK_BG = (12, 20, 48)
LINE_COLOR = (30, 55, 110)
HIT_LINE_COL = (55, 100, 180)

MAX_MISSES = 999

# Estados da FPGA (devem ser iguais aos estados da UC em Verilog)
STATE_IDLE = 0
STATE_COUNTDOWN = 1
STATE_PLAY = 2
STATE_PAUSE = 3
STATE_WIN = 4
STATE_LOSE = 5
STATE_SONG_SELECT = 6
STATE_SETTINGS = 7

# Caminhos resolvidos a partir deste arquivo (independe do diretorio atual)
_PC_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_PC_DIR)
_RTL_DIR = os.path.join(_ROOT_DIR, "rtl")  # .hex ficam junto do Quartus ($readmemh)
_SONGS_DIR = os.path.join(_ROOT_DIR, "songs")
_ASSETS_DIR = os.path.join(_PC_DIR, "assets")

SONG_DURATION_MS = 63_860  # duracao total do chart de Concerning Hobbits (versao curta)
ROM_DEPTH = 3831  # int(63.86 * 60) — deve ser igual ao localparam no Verilog

SONGS = [
    {
        "title": "Concerning Hobbits",
        "subtitle": "The Lord of the Rings",
        "author": "",
        "midi": os.path.join(_SONGS_DIR, "condado", "condado_curta.mid"),
        "audio": os.path.join(_SONGS_DIR, "condado", "Condado_curta.wav"),
        "duration": 63.86,
        "hex": os.path.join(_RTL_DIR, "condado_curta.hex"),
        "available": True,
        # Melodia: flauta doce (canal 1) ate ~25 s, depois a linha aguda do canal 2
        "melody": [1, 2],
        "ref_notes": 139,  # notas do chart antigo (todos os canais); base do CHART_NOTE_FACTOR
    },
    {
        "title": "Song of Storms",
        "subtitle": "The Legend of Zelda",
        "author": "",
        "midi": os.path.join(_SONGS_DIR, "ocarina", "Ocarina_curta.mid"),
        "audio": os.path.join(_SONGS_DIR, "ocarina", "Ocarina_curta.wav"),
        "duration": 70.33,
        "hex": os.path.join(_RTL_DIR, "ocarina_curta.hex"),
        "available": True,
        # Melodia: clarinete (canal 1); o violino (canal 0) so dobra a mesma linha
        "melody": [1],
        "ref_notes": 88,  # notas do chart antigo (todos os canais); base do CHART_NOTE_FACTOR
    },
    {
        "title": "Power Rangers",
        "subtitle": "Super Patrulha Delta",
        "author": "",
        "midi": os.path.join(_SONGS_DIR, "power_rangers", "power_rangers.mid"),
        "audio": os.path.join(_SONGS_DIR, "power_rangers", "Power_Rangers.wav"),
        "duration": 64.67,
        "hex": os.path.join(_RTL_DIR, "power_rangers.hex"),
        "available": True,
        # Melodia: riff da guitarra (canal 2); o canal 0 e o baixo
        "melody": [2],
        "ref_notes": 132,  # notas do chart antigo (todos os canais); base do CHART_NOTE_FACTOR
    },
    {
        "title": "Pokemon",
        "subtitle": "Opening Theme",
        "author": "",
        "midi": os.path.join(_SONGS_DIR, "pokemon", "opening_pokemon.mid"),
        "audio": os.path.join(_SONGS_DIR, "pokemon", "Pokemon.wav"),
        "duration": 61.98,
        "hex": os.path.join(_RTL_DIR, "pokemon.hex"),
        "available": True,
        # Melodia: trompete 1 na introducao (canal 0), depois trompete 2 (canal 3)
        "melody": [0, 3],
        "ref_notes": 168,  # notas do chart antigo (todos os canais); base do CHART_NOTE_FACTOR
    },
]

PACKET_SIZE = 22
PKT_HEADER = 0x55
PKT_FOOTER = 0xAA
EMPTY_SLOT = 0xFF

# ─── Atenuador de frequencias (tela de configuracoes) ────────────────────────
# Uma faixa por botao de trilha. Na tela de configuracoes, cada aperto avanca o
# nivel da faixa (0 -> 1 -> 2 -> 3 -> 0). A FPGA guarda os niveis (eq_config.v)
# e o PC aplica a atenuacao no audio antes de tocar.
EQ_BANDS = [
    {"name": "GRAVES", "range": "ate 250 Hz"},
    {"name": "MEDIOS", "range": "250 Hz a 2 kHz"},
    {"name": "AGUDOS", "range": "2 a 6 kHz"},
    {"name": "BRILHO", "range": "acima de 6 kHz"},
]
EQ_CROSSOVERS = (250.0, 2000.0, 6000.0)  # Hz, fronteiras entre as faixas
EQ_LEVELS = 4
EQ_STEP_DB = 6
EQ_PREVIEW_S = 10.0  # trecho tocado em loop na tela de configuracoes
EQ_FADE_S = 0.05  # fade nas pontas da previa (evita clique na emenda do loop)


def _eq_gain_curve(freqs, eq):
    """Ganho linear por frequencia para os niveis eq[0..3] (ver EQ_BANDS)."""
    gains_db = [-EQ_STEP_DB * lvl for lvl in eq]
    x = np.log2(np.maximum(freqs, 1.0))
    curve_db = np.full(x.shape, float(gains_db[0]))
    for k, fc in enumerate(EQ_CROSSOVERS):
        # Transicao em cosseno de 1 oitava centrada na fronteira: sem degrau
        # no espectro, logo sem ringing audivel
        t = np.clip(x - math.log2(fc) + 0.5, 0.0, 1.0)
        curve_db += (gains_db[k + 1] - gains_db[k]) * (0.5 - 0.5 * np.cos(np.pi * t))
    return 10.0 ** (curve_db / 20.0)


def _apply_eq(samples, rate, eq):
    """Aplica o atenuador no dominio da frequencia. samples: (n, canais) float."""
    n = samples.shape[0]
    spec = np.fft.rfft(samples, axis=0)
    spec *= _eq_gain_curve(np.fft.rfftfreq(n, 1.0 / rate), eq)[:, None]
    return np.fft.irfft(spec, n=n, axis=0)


def _eq_wav_bytes(path, eq, preview_s=None):
    """
    Le o WAV (16 bits, mono ou estereo), aplica o atenuador e devolve um WAV em
    memoria. Com preview_s, usa so um trecho dessa duracao do meio da musica,
    com fade nas pontas para tocar em loop.
    """
    with wave.open(path, "rb") as w:
        ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        n = w.getnframes()
        if width != 2:
            raise ValueError(f"WAV de {8 * width} bits; so 16 bits e suportado")
        first, count = 0, n
        if preview_s is not None:
            count = min(n, int(preview_s * rate))
            first = (n - count) // 2
        w.setpos(first)
        pcm = np.frombuffer(w.readframes(count), dtype="<i2").reshape(-1, ch)

    out = pcm.astype(np.float64)
    if any(eq):
        out = _apply_eq(out, rate, eq)
    if preview_s is not None:
        fade = min(len(out) // 2, int(EQ_FADE_S * rate))
        if fade:
            ramp = np.linspace(0.0, 1.0, fade)[:, None]
            out[:fade] *= ramp
            out[-fade:] *= ramp[::-1]
    pcm = np.clip(np.rint(out), -32768, 32767).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ─── Thread Serial ────────────────────────────────────────────────────────────


class SerialReader(threading.Thread):
    """Le bytes da UART em background e coloca em buffer thread-safe."""

    def __init__(self, port="/dev/ttyUSB0", baudrate=115200):
        super().__init__(daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.buf_lock = threading.Lock()
        self._buf = bytearray()
        self._stop = threading.Event()
        self.connected = False

    def run(self):
        if not SERIAL_AVAILABLE:
            return
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
            self.connected = True
            print(f"[Serial] Conectado em {self.port} @ {self.baudrate} baud")
        except Exception as e:
            print(f"[Serial] Nao foi possivel abrir {self.port}: {e}")
            return
        while not self._stop.is_set():
            try:
                waiting = ser.in_waiting
                if waiting > 0:
                    data = ser.read(waiting)
                    with self.buf_lock:
                        self._buf.extend(data)
            except Exception as e:
                print(f"[Serial] Erro: {e}")
                self.connected = False
                break
        if ser.is_open:
            ser.close()

    def read_buf(self):
        """Retorna e limpa o buffer acumulado (thread-safe)."""
        with self.buf_lock:
            data = bytes(self._buf)
            self._buf.clear()
        return data

    def stop(self):
        self._stop.set()


# ─── Parser de pacote ─────────────────────────────────────────────────────────


def parse_packet(data, offset):
    """
    Tenta interpretar um pacote de 22 bytes a partir de 'offset' em 'data'.
    Retorna um dict com o estado do jogo, ou None se invalido.
    """
    if offset + PACKET_SIZE > len(data):
        return None
    if data[offset] != PKT_HEADER or data[offset + 21] != PKT_FOOTER:
        return None

    state = data[offset + 1] & 0x0F
    countdown = data[offset + 2] & 0x0F
    # Niveis do atenuador nos nibbles altos dos bytes 1 e 2 (eq_config.v)
    eq = [
        (data[offset + 1] >> 4) & 0x03,
        (data[offset + 1] >> 6) & 0x03,
        (data[offset + 2] >> 4) & 0x03,
        (data[offset + 2] >> 6) & 0x03,
    ]
    notes = []
    for t in range(4):
        track_notes = []
        for s in range(3):
            age = data[offset + 3 + t * 3 + s]
            if age != EMPTY_SLOT:
                track_notes.append(age * NOTE_SPEED)
        notes.append(track_notes)
    score = (data[offset + 15] << 8) | data[offset + 16]
    misses = data[offset + 17]
    combo = data[offset + 18]
    frame_counter = ((data[offset + 19] & 0x3F) << 8) | data[offset + 20]
    song_id = (data[offset + 19] >> 6) & 0x03

    return {
        "state": state,
        "countdown": countdown,
        "notes": notes,
        "score": score,
        "misses": misses,
        "combo": combo,
        "frame_counter": frame_counter,
        "song_id": song_id,
        "eq": eq,
    }


def find_packet(buf):
    """
    Procura o primeiro pacote valido em buf.
    Retorna (packet_dict, bytes_consumed) ou (None, 0).
    """
    for i in range(len(buf) - PACKET_SIZE + 1):
        pkt = parse_packet(buf, i)
        if pkt is not None:
            return pkt, i + PACKET_SIZE
    # Nao encontrou pacote completo; descarta tudo exceto os ultimos 19 bytes
    keep = min(len(buf), PACKET_SIZE - 1)
    return None, max(0, len(buf) - keep)


# ─── Chart: gerado do MIDI real ───────────────────────────────────────────────

_WAV_DURATION = 63.86  # segundos

# ─── Dificuldade ──────────────────────────────────────────────────────────────
# Mude apenas esta linha: 'facil', 'medio' ou 'dificil'
DIFFICULTY = "facil"

_DIFF_PRESETS = {
    #              min_lane  global  max_sim
    "facil": (60, 30, 1),
    "medio": (30, 10, 2),
    "dificil": (15, 5, 4),
}
_MIN_NOTE_SPACING, _GLOBAL_SPACING, _MAX_SIMULTANEOUS = _DIFF_PRESETS[DIFFICULTY]


# ─── Chart: melodia ───────────────────────────────────────────────────────────
# O chart segue a melodia (canais "melody" de cada musica), nao a harmonia.
# Onde a melodia fica em silencio por mais de _MELODY_GAP_S, a voz mais aguda do
# acompanhamento preenche. A bateria (canal 9) nunca entra.
CHART_NOTE_FACTOR = 0.65  # notas mantidas = ref_notes * fator (0.65 = 35% menos)
_MELODY_GAP_S = 2.5  # silencio da melodia a partir do qual o acompanhamento preenche
_FILL_MARGIN_S = 0.5  # distancia minima entre preenchimento e nota da melodia
_CHORD_S = 0.04  # onsets mais proximos que isso contam como um acorde
_DRUM_CHANNEL = 9
_LANE_SLOT_SPAN = 152  # 4 notas seguidas na trilha precisam caber em 3 slots (MISS_AGE + 1)


def _midi_notes(midi_path):
    """
    Le todas as notas do MIDI. Cada nota: t (s), dur (s), pitch, vel, ch e
    metric (peso no compasso: 3 = cabeca, 2 = tempo, 1 = colcheia, 0 = resto).
    """
    import mido

    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat
    tempo, tick, t = 500_000, 0, 0.0
    num, den, bar_origin = 4, 4, 0
    opened, notes = {}, []

    def metric(tk):
        beat = tpb * 4 // den
        pos = (tk - bar_origin) % (beat * num)
        tol = max(1, beat // 16)  # MIDIs exportados nem sempre estao quantizados

        def on_grid(step):
            r = pos % step
            return min(r, step - r) <= tol

        if min(pos, beat * num - pos) <= tol:
            return 3
        if on_grid(beat):
            return 2
        if on_grid(beat // 2):
            return 1
        return 0

    # merge_tracks mantem msg.time em ticks: precisamos deles para o compasso
    for msg in mido.merge_tracks(mid.tracks):
        if msg.time:
            t += mido.tick2second(msg.time, tpb, tempo)
            tick += msg.time
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "time_signature":
            num, den, bar_origin = msg.numerator, msg.denominator, tick
        elif msg.type == "note_on" and msg.velocity > 0:
            n = {"t": t, "dur": 0.0, "pitch": msg.note, "vel": msg.velocity,
                 "ch": msg.channel, "metric": metric(tick)}
            opened[(msg.channel, msg.note)] = n
            notes.append(n)
        elif msg.type in ("note_off", "note_on"):
            n = opened.pop((msg.channel, msg.note), None)
            if n:
                n["dur"] = t - n["t"]
    return notes


def _skyline(notes):
    """Uma nota por onset: a mais aguda de cada acorde (a melodia fica no topo)."""
    out = []
    for n in sorted(notes, key=lambda n: n["t"]):
        if out and n["t"] - out[-1]["t"] < _CHORD_S:
            if n["pitch"] > out[-1]["pitch"]:
                out[-1] = n
        else:
            out.append(n)
    return out


def _melody_candidates(notes, melody_channels, duration):
    """Melodia (skyline) + preenchimento das lacunas, dentro do audio."""
    inside = [n for n in notes if 0.0 <= n["t"] <= duration]
    if melody_channels:
        melody = _skyline([n for n in inside if n["ch"] in melody_channels])
    else:
        melody = []
    for n in melody:
        n["fill"] = False

    # Lacunas da melodia (inclui inicio e fim da musica)
    marks = [-_MELODY_GAP_S] + [n["t"] for n in melody] + [duration + _MELODY_GAP_S]
    gaps = [
        (a + _FILL_MARGIN_S, b - _FILL_MARGIN_S)
        for a, b in zip(marks, marks[1:])
        if b - a > _MELODY_GAP_S
    ]
    accomp = _skyline(
        [
            n
            for n in inside
            if n["ch"] != _DRUM_CHANNEL and n["ch"] not in melody_channels
        ]
    )
    fill = [n for n in accomp if any(a <= n["t"] <= b for a, b in gaps)]
    for n in fill:
        n["fill"] = True
    return melody + fill


def _midi_to_chart(song_config=None, save_hex=False):
    """
    Le o MIDI e retorna lista de bitmasks por frame (60 fps).
    Se save_hex=True, tambem grava o arquivo .hex da musica.
    song_config: entrada do array SONGS. Se None, usa SONGS[0].

    1. Candidatas: a melodia da musica (song_config["melody"]) e, nas lacunas
       dela, a voz mais aguda do acompanhamento.
    2. Trilha pela altura: quartis das alturas das candidatas, entao a melodia
       "sobe" para a direita. Se a trilha preferida estiver ocupada (espacamento
       ou slots), a nota vai para a trilha livre mais proxima dela.
    3. Selecao por prioridade, ate ref_notes * CHART_NOTE_FACTOR notas: primeiro
       cabeca de compasso, depois tempos, colcheias; melodia antes do
       preenchimento; notas longas antes das curtas. Cada nota so entra se
       respeitar o espacamento global e por trilha do preset de dificuldade e
       os 3 slots por trilha do hardware.
    """
    song_config = song_config or SONGS[0]
    duration = song_config["duration"]
    total_frames = int(duration * FPS)  # igual ao ROM_DEPTH correspondente no Verilog

    cands = _melody_candidates(
        _midi_notes(song_config["midi"]), song_config.get("melody", []), duration
    )

    # AUDIO_LATENCY_FRAMES compensa o atraso do buffer de audio: sem ele, as notas
    # chegam na hit zone antes do som correspondente sair pelos alto-falantes.
    for n in cands:
        n["frame"] = int(n["t"] * FPS) - int(HIT_AGE) + AUDIO_LATENCY_FRAMES
    cands = [n for n in cands if 0 <= n["frame"] < total_frames]

    # Quartis com empates: cada altura vai para a trilha do ponto medio da sua
    # fatia na distribuicao acumulada. Altura repetida nao lota uma trilha so.
    counts = Counter(n["pitch"] for n in cands)
    lane_of, below = {}, 0
    for p in sorted(counts):
        mid = (below + counts[p] / 2) / len(cands)
        lane_of[p] = min(3, int(mid * 4))
        below += counts[p]
    for n in cands:
        n["lane"] = lane_of[n["pitch"]]

    ref = song_config.get("ref_notes", len(cands))
    target = round(ref * CHART_NOTE_FACTOR)

    def fits(frames, f, spacing):
        return all(abs(f - g) >= spacing for g in frames)

    def slots_ok(frames, f):
        seq = sorted(frames + [f])
        i = seq.index(f)
        return all(
            seq[j + 3] - seq[j] >= _LANE_SLOT_SPAN
            for j in range(max(0, i - 3), min(i, len(seq) - 4) + 1)
        )

    # Notas monofonicas: _MAX_SIMULTANEOUS nao se aplica (o skyline ja tirou acordes)
    chosen, by_lane = [], [[] for _ in range(4)]
    for n in sorted(cands, key=lambda n: (n["metric"], not n["fill"], n["dur"]), reverse=True):
        if len(chosen) >= target:
            break
        f = n["frame"]
        if not fits(chosen, f, _GLOBAL_SPACING):
            continue
        # Trilha preferida primeiro, depois as vizinhas por distancia
        for lane in sorted(range(4), key=lambda l: (abs(l - n["lane"]), l)):
            if fits(by_lane[lane], f, _MIN_NOTE_SPACING) and slots_ok(by_lane[lane], f):
                chosen.append(f)
                by_lane[lane].append(f)
                break

    rom = [0] * total_frames
    for lane, frames in enumerate(by_lane):
        for f in frames:
            rom[f] |= 1 << lane

    if save_hex:
        with open(song_config["hex"], "w") as fh:
            for v in rom:
                fh.write(f"{v:02X}\n")
        print(
            f"[chart] {song_config['hex']} gerado: {len(rom)} frames, {len(chosen)} notas "
            f"(alvo {target}, {sum(not n['fill'] for n in cands)} da melodia + "
            f"{sum(n['fill'] for n in cands)} de preenchimento candidatas)"
        )

    return rom


# ─── Simulacao (modo sem FPGA) ────────────────────────────────────────────────


class SimulationEngine:
    """Replica a FSM e a logica de notas do Verilog para teste sem FPGA."""

    HIT_AGE = HIT_AGE  # age quando nota esta em HIT_Y (definido em constantes globais)
    HIT_WIN = 11  # janela de acerto: ±11 frames
    MISS_AGE = 151  # nota escapa apos este age
    CD_FRAMES = 180  # frames de countdown (3s a 60fps)
    SLOTS = 3  # slots de nota por trilha
    EMPTY = 0xFF

    def __init__(self):
        self.state = STATE_IDLE
        self._cd_frame = 0
        self._chart_frame = 0
        self._ages = [[self.EMPTY] * self.SLOTS for _ in range(4)]
        self.score = 0
        self.misses = 0
        self.combo = 0
        self._selected_song = 0
        # Niveis do atenuador: como o eq_config da FPGA, so zeram no reset
        self._eq = [0] * 4
        # Pre-gera charts de todas as musicas disponíveis e grava os .hex no disco
        self._charts = [
            _midi_to_chart(s, save_hex=True) for s in SONGS if s["available"]
        ]
        self._chart = self._charts[self._selected_song]
        self._rom_depth = len(self._chart)

    # ── API publica ──────────────────────────────────────────────────────────

    def step(self, btn):
        """
        Avanca um frame. btn = [track0, track1, track2, track3, start] (bool).
        Retorna dict compativel com parse_packet().
        """
        self._fsm(btn)

        if self.state == STATE_PLAY:
            self._advance_ages()
            self._check_escapes()
            self._spawn_notes()
            self._process_buttons(btn)
            if self._chart_frame >= self._rom_depth:
                self.state = STATE_WIN

        countdown_sec = (
            max(0, 3 - self._cd_frame // 60) if self.state == STATE_COUNTDOWN else 0
        )
        notes = []
        for track in self._ages:
            notes.append([age * NOTE_SPEED for age in track if age != self.EMPTY])

        return {
            "state": self.state,
            "countdown": countdown_sec,
            "notes": notes,
            "score": self.score,
            "misses": self.misses,
            "combo": self.combo,
            "frame_counter": self._chart_frame,
            "song_id": self._selected_song,
            "eq": list(self._eq),
        }

    # ── FSM ──────────────────────────────────────────────────────────────────

    def _fsm(self, btn):
        start = btn[4]
        if self.state == STATE_IDLE:
            if start:
                self.state = STATE_SONG_SELECT
            elif any(btn[:4]):
                self.state = STATE_SETTINGS
        elif self.state == STATE_SETTINGS:
            if start:
                self.state = STATE_IDLE
            else:
                for i in range(4):
                    if btn[i]:
                        self._eq[i] = (self._eq[i] + 1) % EQ_LEVELS
        elif self.state == STATE_SONG_SELECT:
            if start:
                self.state = STATE_IDLE
            elif btn[0]:
                self._selected_song = 0
                self._start_countdown()
            elif btn[1]:
                self._selected_song = 1
                self._start_countdown()
            elif btn[2]:
                self._selected_song = 2
                self._start_countdown()
            elif btn[3]:
                self._selected_song = 3
                self._start_countdown()
        elif self.state == STATE_COUNTDOWN:
            self._cd_frame += 1
            if self._cd_frame >= self.CD_FRAMES:
                self.state = STATE_PLAY
        elif self.state == STATE_PLAY:
            if start:
                self.state = STATE_PAUSE
        elif self.state == STATE_PAUSE:
            if btn[0]:
                self.state = STATE_IDLE
                self._reset()
            elif start:
                self.state = STATE_PLAY
        elif self.state in (STATE_WIN, STATE_LOSE):
            if start:
                self.state = STATE_IDLE
                self._reset()

    def _start_countdown(self):
        self._reset()
        self._chart = self._charts[self._selected_song]
        self._rom_depth = len(self._chart)
        self.state = STATE_COUNTDOWN

    def _reset(self):
        self._cd_frame = 0
        self._chart_frame = 0
        self._ages = [[self.EMPTY] * self.SLOTS for _ in range(4)]
        self.score = 0
        self.misses = 0
        self.combo = 0

    # ── Logica de notas ──────────────────────────────────────────────────────

    def _advance_ages(self):
        for track in self._ages:
            for i in range(self.SLOTS):
                if track[i] != self.EMPTY:
                    track[i] += 1

    def _check_escapes(self):
        for track in self._ages:
            for i in range(self.SLOTS):
                if track[i] != self.EMPTY and track[i] > self.MISS_AGE:
                    track[i] = self.EMPTY
                    self.misses += 1
                    self.combo = 0

    def _spawn_notes(self):
        if self._chart_frame < len(self._chart):
            mask = self._chart[self._chart_frame]
            for t in range(4):
                if mask & (1 << t):
                    for i in range(self.SLOTS):
                        if self._ages[t][i] == self.EMPTY:
                            self._ages[t][i] = 0
                            break
        self._chart_frame += 1

    def _process_buttons(self, btn):
        for t in range(4):
            if not btn[t]:
                continue
            track = self._ages[t]
            hit = False
            for i in range(self.SLOTS):
                if (
                    track[i] != self.EMPTY
                    and abs(track[i] - self.HIT_AGE) <= self.HIT_WIN
                ):
                    track[i] = self.EMPTY
                    self.score += 100 + self.combo
                    self.combo += 1
                    hit = True
                    break
            if not hit:
                self.combo = 0

    def _all_empty(self):
        return all(age == self.EMPTY for track in self._ages for age in track)


# ─── Renderer ─────────────────────────────────────────────────────────────────


class Renderer:
    def __init__(
        self,
        serial_port="/dev/ttyUSB0",
        baudrate=115200,
        sim_mode=False,
        scale=None,
        audio_offset_ms=0,
    ):
        self._sim_mode = sim_mode
        # Latencia de saida alem do buffer do mixer (fone Bluetooth, placa de
        # som externa). Positivo: a musica comeca adiantada esse tanto para
        # compensar; negativo: a musica comeca atrasada.
        self._audio_offset = audio_offset_ms / 1000.0
        self._music_start_at = None  # perf_counter em que play() deve rodar
        # Jogo sempre desenha em SCREEN_W x SCREEN_H (HIT_AGE depende disso);
        # _present() escala esse frame para a janela, que e redimensionavel.
        if scale is None:
            desk_w, desk_h = pygame.display.get_desktop_sizes()[0]
            scale = min(1.0, 0.9 * desk_w / SCREEN_W, 0.9 * desk_h / SCREEN_H)
        self.window = pygame.display.set_mode(
            (int(SCREEN_W * scale), int(SCREEN_H * scale)), pygame.RESIZABLE
        )
        self.screen = pygame.Surface((SCREEN_W, SCREEN_H)).convert()
        pygame.display.set_caption("Beat by Bit" + (" [SIM]" if sim_mode else ""))
        self.clock = pygame.time.Clock()
        self._font_cache = {}

        # Estado recebido da FPGA (ou simulado)
        self.fpga_state = STATE_IDLE
        self.fpga_countdown = 3
        self.fpga_notes = [[], [], [], []]
        self.fpga_score = 0
        self.fpga_misses = 0
        self.fpga_combo = 0
        self.fpga_frame_counter = 0
        self.fpga_song_id = 0
        self._selected_song = 0
        self._eq = [0, 0, 0, 0]  # niveis do atenuador (FPGA ou simulacao)

        # Audio carregado no mixer: (musica, eq, previa). WAVs filtrados ficam
        # em memoria (nada e gravado em disco) num cache pequeno.
        self._loaded_audio = None
        self._audio_cache = {}
        self._music_buf = None

        # Efeitos visuais tipados
        self._hit_effects = []  # burst de acerto
        self._prev_notes = [[], [], [], []]
        self._prev_score = 0
        self._prev_combo = 0

        # Surface scratch reutilizavel (evita alocacao por frame)
        self.scratch = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

        self._serial_buf = bytearray()
        if sim_mode:
            self.sim = SimulationEngine()
            self.serial = None
            self._sim_t0 = None  # perf_counter do 1o passo da simulacao
            self._sim_steps = 0
            self._sim_btn = [False] * 5
            self._sim_song_ms = int(_WAV_DURATION * 1000)
        else:
            self.serial = SerialReader(port=serial_port, baudrate=baudrate)
            self.serial.start()

        self._build_glow_surfaces()
        self._build_btn_surfaces()
        self._build_scanline_surf()
        self._build_bg_grid_surf()
        self._build_vignette_surf()
        self._build_progress_gradient()
        self._build_score_backdrop()
        self._build_ghost_note_surfs()
        self._init_idle_anim()
        self._init_music()
        self._load_logo()

    def _load_logo(self):
        logo_path = os.path.join(_ASSETS_DIR, "images", "beat_by_bit_logo.png")
        raw = pygame.image.load(logo_path).convert_alpha()
        logo_w = int(SCREEN_W * 0.38)
        logo_h = int(raw.get_height() * logo_w / raw.get_width())
        self._logo = pygame.transform.smoothscale(raw, (logo_w, logo_h))
        title_size = _ps(90)
        self._logo_title = pygame.transform.smoothscale(raw, (title_size, title_size))
        pause_size = int(SCREEN_W * 0.17)
        self._logo_pause = pygame.transform.smoothscale(raw, (pause_size, pause_size))

    # ── Musica ────────────────────────────────────────────────────────────────

    _AUDIO_CACHE_MAX = 8

    def _init_music(self):
        """Carrega a musica selecionada, ja com o atenuador aplicado."""
        self._load_audio(preview=False)

    def _load_audio(self, preview):
        """
        Carrega no mixer a musica selecionada filtrada por self._eq. Com
        preview=True, carrega so o trecho em loop da tela de configuracoes.
        """
        self._music_ok = False
        self._music_paused = False
        key = (self._selected_song, tuple(self._eq), preview)
        self._loaded_audio = key
        path = SONGS[self._selected_song]["audio"]

        source = path
        if preview or any(self._eq):
            data = self._audio_cache.get(key)
            if data is None:
                try:
                    data = _eq_wav_bytes(path, self._eq, EQ_PREVIEW_S if preview else None)
                except Exception as e:
                    print(f"[music] Atenuador indisponivel, tocando original: {e}")
                if data is not None:
                    if len(self._audio_cache) >= self._AUDIO_CACHE_MAX:
                        self._audio_cache.pop(next(iter(self._audio_cache)))
                    self._audio_cache[key] = data
            if data is not None:
                # O mixer le o arquivo aos poucos: a referencia precisa viver
                self._music_buf = io.BytesIO(data)
                source = self._music_buf

        try:
            if source is path:
                pygame.mixer.music.load(path)
            else:
                pygame.mixer.music.load(source, "wav")
            pygame.mixer.music.set_volume(0.70)
            self._music_ok = True
            print(f"[music] Carregado: {path} eq={self._eq}{' (previa)' if preview else ''}")
        except Exception as e:
            print(f"[music] Falha ao carregar musica: {e}")

    @staticmethod
    def _generate_wav(path):
        """Fallback de sintese para modo FPGA (sem audio externo). Usa mesma sintese do modo sim."""
        _score_to_wav(path)

    def _update_music(self, state):
        """Controla reproducao com base no estado do jogo."""
        # Troca o audio quando musica ou eq mudam, ou ao entrar/sair da previa.
        # Nunca em PLAY/PAUSE: la o eq e a musica estao fixos.
        wanted = (self._selected_song, tuple(self._eq), state == STATE_SETTINGS)
        if wanted != self._loaded_audio and state not in (STATE_PLAY, STATE_PAUSE):
            pygame.mixer.music.stop()
            self._load_audio(preview=wanted[2])
        if not self._music_ok:
            return
        playing = pygame.mixer.music.get_busy()
        if state == STATE_SETTINGS:
            if not playing:
                pygame.mixer.music.play(loops=-1)  # previa em loop
        elif state == STATE_PLAY:
            if not playing:
                if self._music_paused:
                    pygame.mixer.music.unpause()  # retoma de onde parou
                    self._music_paused = False
                elif self._audio_offset >= 0:
                    # inicia do zero, pulando o trecho que a latencia atrasaria
                    pygame.mixer.music.play(start=self._audio_offset)
                else:
                    now = time.perf_counter()
                    if self._music_start_at is None:
                        self._music_start_at = now - self._audio_offset
                    if now >= self._music_start_at:
                        pygame.mixer.music.play()
                        self._music_start_at = None
        elif state == STATE_PAUSE:
            if playing:
                pygame.mixer.music.pause()
                self._music_paused = True
        else:  # IDLE, SELECT, COUNTDOWN, WIN, LOSE
            if playing:
                pygame.mixer.music.stop()
            self._music_paused = False
            self._music_start_at = None

    # ── Pre-renderizacao ──────────────────────────────────────────────────────

    def _build_glow_surfaces(self):
        self.glow_surfs = []
        for color in TRACK_COLORS:
            s = pygame.Surface((GLOW_SIZE, GLOW_SIZE), pygame.SRCALPHA)
            pygame.draw.circle(s, (*color, 35), (GLOW_CENTER, GLOW_CENTER), 58)
            pygame.draw.circle(s, (*color, 70), (GLOW_CENTER, GLOW_CENTER), 40)
            pygame.draw.circle(s, (*color, 150), (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS + 4)
            pygame.draw.circle(s, color, (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS)
            pygame.draw.circle(s, (255, 255, 255), (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS, 2)
            self.glow_surfs.append(s)

    def _build_btn_surfaces(self):
        btn_radius = NOTE_RADIUS + 2
        size = btn_radius * 2 + 16
        center = size // 2
        self.btn_size = size
        self.btn_off = []
        self.btn_on = []
        for color in TRACK_COLORS:
            # OFF: circulo com borda opaca e miolo escuro
            off = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(off, (40, 40, 60, 200), (center, center), btn_radius + 6)
            pygame.draw.circle(off, (*color, 100), (center, center), btn_radius + 2, 3)
            pygame.draw.circle(off, (20, 20, 30, 200), (center, center), btn_radius - 1)
            self.btn_off.append(off)

            # ON: estilo arcade aceso (brilho + preenchido + reflexo plastico)
            on = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(on, (*color, 100), (center, center), btn_radius + 8)
            pygame.draw.circle(on, (*color, 200), (center, center), btn_radius + 4)
            pygame.draw.circle(on, color, (center, center), btn_radius)
            pygame.draw.circle(on, (255, 255, 255), (center, center), btn_radius, 2)
            pygame.draw.ellipse(on, (255, 255, 255, 120), (center - btn_radius // 2, center - btn_radius + 4, btn_radius, btn_radius // 2))
            self.btn_on.append(on)

    def _build_scanline_surf(self):
        """Pre-renderiza overlay de scanlines CRT (linhas escuras a cada 2px)."""
        self.scanline_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        for row in range(0, SCREEN_H, 2):
            pygame.draw.rect(self.scanline_surf, (0, 0, 0, 60), (0, row, SCREEN_W, 1))

    def _build_bg_grid_surf(self):
        """Pre-renderiza grid horizontal sutil no fundo."""
        self.bg_grid_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        for y in range(0, SCREEN_H, _py(40)):
            pygame.draw.line(
                self.bg_grid_surf, (20, 40, 90, 255), (0, y), (SCREEN_W, y), 1
            )

    def _build_vignette_surf(self):
        """Pre-renderiza overlay de vignette (bordas escuras para dar profundidade)."""
        self.vignette_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        steps = 55
        step_px = min(SCREEN_W, SCREEN_H) // (2 * steps)
        for i in range(steps):
            ratio = 1.0 - i / steps
            alpha = int(145 * ratio ** 1.8)
            margin = i * step_px
            thick = max(1, step_px + 2)
            pygame.draw.rect(
                self.vignette_surf,
                (0, 0, 0, alpha),
                (margin, margin, SCREEN_W - 2 * margin, SCREEN_H - 2 * margin),
                thick,
            )

    def _build_progress_gradient(self):
        """Pre-renderiza gradiente laranja→azul para a barra de progresso."""
        self._prog_bw = SCREEN_W - _px(40)
        self._prog_bh = max(4, _py(8))
        self._progress_grad = pygame.Surface((self._prog_bw, self._prog_bh))
        for x in range(self._prog_bw):
            t = x / max(1, self._prog_bw - 1)
            r = int(255 * (1 - t))
            g = int(110 * (1 - t) + 140 * t)
            bv = int(255 * t)
            pygame.draw.line(self._progress_grad, (r, g, bv), (x, 0), (x, self._prog_bh - 1))

    def _build_score_backdrop(self):
        """Pre-renderiza painel escuro atras do HUD de score."""
        self.score_backdrop = pygame.Surface((260, 76), pygame.SRCALPHA)
        pygame.draw.rect(
            self.score_backdrop, (0, 0, 0, 150), (0, 0, 260, 76), border_radius=6
        )
        pygame.draw.rect(
            self.score_backdrop, (40, 80, 170, 180), (0, 0, 260, 76), 1, border_radius=6
        )

    def _build_ghost_note_surfs(self):
        """Versao fantasma das notas para a animacao da tela idle."""
        self.ghost_note_surfs = []
        for color in TRACK_COLORS:
            s = pygame.Surface((GLOW_SIZE, GLOW_SIZE), pygame.SRCALPHA)
            pygame.draw.circle(s, (*color, 12), (GLOW_CENTER, GLOW_CENTER), 58)
            pygame.draw.circle(s, (*color, 25), (GLOW_CENTER, GLOW_CENTER), 40)
            pygame.draw.circle(s, (*color, 55), (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS + 4)
            pygame.draw.circle(s, (*color, 90), (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS)
            pygame.draw.circle(s, (200, 200, 255, 50), (GLOW_CENTER, GLOW_CENTER), NOTE_RADIUS, 2)
            self.ghost_note_surfs.append(s)

    def _init_idle_anim(self):
        """Inicializa estado do Game of Life para a tela idle."""
        self._gol_cell_size = 20
        self._gol_cols = SCREEN_W // self._gol_cell_size
        self._gol_rows = SCREEN_H // self._gol_cell_size
        # 0 = morta, 1 = azul, 2 = laranja
        self._gol_grid = [
            [random.choice([1, 2]) if random.random() < 0.2 else 0 for _ in range(self._gol_cols)]
            for _ in range(self._gol_rows)
        ]
        self._gol_last_update = 0
        self._gol_update_interval = 150  # ms

    def _update_gol(self):
        new_grid = [[0 for _ in range(self._gol_cols)] for _ in range(self._gol_rows)]
        total_alive = 0
        for r in range(self._gol_rows):
            for c in range(self._gol_cols):
                cell = self._gol_grid[r][c]
                alive = cell > 0
                neighbors = 0
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0:
                            continue
                        nr = (r + dr) % self._gol_rows
                        nc = (c + dc) % self._gol_cols
                        if self._gol_grid[nr][nc] > 0:
                            neighbors += 1
                if alive and (neighbors == 2 or neighbors == 3):
                    new_grid[r][c] = cell  # preserva a cor
                    total_alive += 1
                elif not alive and neighbors == 3:
                    new_grid[r][c] = random.choice([1, 2])
                    total_alive += 1

        # Reiniciar aleatoriamente se a tela ficar muito vazia
        if total_alive < (self._gol_cols * self._gol_rows) * 0.05:
            for r in range(self._gol_rows):
                for c in range(self._gol_cols):
                    if random.random() < 0.1:
                        new_grid[r][c] = random.choice([1, 2])

        self._gol_grid = new_grid

    def _draw_idle_anim(self):
        """Atualiza e desenha a animacao de fundo da tela idle (Game of Life)."""
        ticks = pygame.time.get_ticks()
        if ticks - self._gol_last_update > self._gol_update_interval:
            self._update_gol()
            self._gol_last_update = ticks

        self.scratch.fill((0, 0, 0, 0))
        _blue   = (0, 140, 255, 50)
        _orange = (255, 110, 0, 50)

        for r in range(self._gol_rows):
            for c in range(self._gol_cols):
                cell = self._gol_grid[r][c]
                if cell:
                    color = _blue if cell == 1 else _orange
                    x = c * self._gol_cell_size
                    y = r * self._gol_cell_size
                    pygame.draw.rect(
                        self.scratch,
                        color,
                        (x + 2, y + 2, self._gol_cell_size - 4, self._gol_cell_size - 4),
                        border_radius=4,
                    )

        self.screen.blit(self.scratch, (0, 0))

    # ── Fontes ────────────────────────────────────────────────────────────────

    def font(self, size):
        scaled = max(6, _ps(size))
        if scaled not in self._font_cache:
            self._font_cache[scaled] = pygame.font.Font(
                os.path.join(_ASSETS_DIR, "fonts", "Pixeltype.ttf"), scaled
            )
        return self._font_cache[scaled]

    # ── Leitura UART ──────────────────────────────────────────────────────────

    def _process_serial(self):
        """Drena o buffer serial e atualiza o estado da FPGA."""
        new_data = self.serial.read_buf()
        if new_data:
            self._serial_buf.extend(new_data)

        while len(self._serial_buf) >= PACKET_SIZE:
            pkt, consumed = find_packet(self._serial_buf)
            self._serial_buf = self._serial_buf[consumed:]
            if pkt is None:
                break
            self._prev_notes = [list(t) for t in self.fpga_notes]
            self._prev_score = self.fpga_score
            self._prev_combo = self.fpga_combo
            self.fpga_state = pkt["state"]
            self.fpga_countdown = pkt["countdown"]
            self.fpga_notes = pkt["notes"]
            self.fpga_score = pkt["score"]
            self.fpga_misses = pkt["misses"]
            self.fpga_combo = pkt["combo"]
            self.fpga_frame_counter = pkt["frame_counter"]
            # _update_music recarrega o audio se musica ou eq mudarem
            self.fpga_song_id = pkt["song_id"]
            self._selected_song = pkt["song_id"]
            self._eq = pkt["eq"]
            self._detect_note_events()

    def _detect_note_events(self):
        """Detecta notas que sumiram e cria efeito de acerto ou erro."""
        scored = (
            self.fpga_score > self._prev_score or self.fpga_combo > self._prev_combo
        )
        for i in range(4):
            prev_set = set(self._prev_notes[i])
            curr_set = set(self.fpga_notes[i])
            for y in prev_set - curr_set:
                if abs(y - HIT_Y) <= 60 and scored:
                    # Nota sumiu perto da hit zone E score/combo subiu → acerto
                    self._spawn_hit_effect(i, TRACK_X[i], HIT_Y)
                elif y > HIT_Y + 60:
                    pass  # erro: feedback apenas no HUD (contador + combo)

    def _process_sim(self, events):
        """Atualiza o estado a partir da simulacao Python (modo --sim)."""
        btn = [False] * 5
        key_map = {
            pygame.K_d: 0,
            pygame.K_f: 1,
            pygame.K_j: 2,
            pygame.K_k: 3,
            pygame.K_SPACE: 4,
        }
        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key in key_map:
                btn[key_map[ev.key]] = True
            # ESC em pausa = voltar ao menu (mesmo que btn[0]). So na pausa:
            # na tela inicial, btn[0] abriria as configuracoes.
            elif (
                ev.type == pygame.KEYDOWN
                and ev.key == pygame.K_ESCAPE
                and self.fpga_state == STATE_PAUSE
            ):
                btn[0] = True

        # Relogio de parede: a simulacao da 1 passo a cada 1/60 s reais, como o
        # frame_tick da FPGA. Nao da para contar frames renderizados: o
        # clock.tick(60) roda a ~59 fps e as notas atrasariam em relacao ao audio
        # (~1 s ao fim de uma musica de 64 s).
        now = time.perf_counter()
        if self._sim_t0 is None:
            self._sim_t0 = now
        due = int((now - self._sim_t0) * FPS) - self._sim_steps
        if due > _SIM_MAX_CATCHUP:
            # Ficou parado demais (maquina suspensa): reancora
            self._sim_t0 = now - (self._sim_steps + 1) / FPS
            due = 1
        # Teclas apertadas num frame sem passo ficam para o proximo passo
        self._sim_btn = [a or b for a, b in zip(self._sim_btn, btn)]
        if due <= 0:
            return

        self._prev_notes = [list(t) for t in self.fpga_notes]
        self._prev_score = self.fpga_score
        self._prev_combo = self.fpga_combo

        # As teclas valem no ultimo passo, que corresponde ao instante atual
        for _ in range(due - 1):
            self.sim.step([False] * 5)
        pkt = self.sim.step(self._sim_btn)
        self._sim_btn = [False] * 5
        self._sim_steps += due
        # _update_music recarrega o audio se musica ou eq mudarem
        self._selected_song = pkt["song_id"]
        self._eq = pkt["eq"]
        self.fpga_state = pkt["state"]
        self.fpga_countdown = pkt["countdown"]
        self.fpga_notes = pkt["notes"]
        self.fpga_score = pkt["score"]
        self.fpga_misses = pkt["misses"]
        self.fpga_combo = pkt["combo"]
        self.fpga_frame_counter = pkt["frame_counter"]
        self._detect_note_events()

    # ── Spawn de efeitos ──────────────────────────────────────────────────────

    def _spawn_hit_effect(self, lane, x, y):
        """Cria burst de acerto: 3 aneis expansivos + 7 particulas + flash."""
        particles = []
        for i in range(7):
            angle = 2 * math.pi * i / 7 + random.uniform(-0.3, 0.3)
            speed = random.uniform(2.5, 5.5)
            size = random.choice([3, 4, 4, 5])
            particles.append(
                {
                    "px": float(x),
                    "py": float(y),
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed,
                    "alpha": 255,
                    "size": size,
                }
            )
        self._hit_effects.append(
            {
                "lane": lane,
                "x": x,
                "y": y,
                "color": TRACK_COLORS[lane],
                "age": 0,
                "rings": [
                    {"r": 8, "alpha": 255, "delay": 0},
                    {"r": 5, "alpha": 220, "delay": 5},
                    {"r": 3, "alpha": 180, "delay": 10},
                ],
                "particles": particles,
                "flash_alpha": 255,
                "active": True,
            }
        )

    # ── Atualizacao de efeitos ────────────────────────────────────────────────

    def _advance_effects(self):
        """Avanca o estado de todos os efeitos. Chamado uma vez por frame."""
        for eff in self._hit_effects:
            if not eff["active"]:
                continue
            eff["age"] += 1
            eff["flash_alpha"] = max(0, eff["flash_alpha"] - 30)
            for ring in eff["rings"]:
                if eff["age"] >= ring["delay"]:
                    ring["r"] += 6
                    ring["alpha"] = max(0, ring["alpha"] - 12)
            for p in eff["particles"]:
                p["px"] += p["vx"]
                p["py"] += p["vy"]
                p["vy"] += 0.15  # gravidade
                p["alpha"] = max(0, p["alpha"] - 10)
            if eff["age"] >= 45:
                eff["active"] = False

        self._hit_effects = [e for e in self._hit_effects if e["active"]]

    # ── Componentes de desenho ────────────────────────────────────────────────

    def _draw_bg_grid(self):
        self.screen.blit(self.bg_grid_surf, (0, 0))

    def _draw_tracks(self, alpha=255):
        for i, x in enumerate(TRACK_X):
            s = pygame.Surface((TRACK_W, SCREEN_H), pygame.SRCALPHA)
            s.fill((*TRACK_BG, alpha))
            self.screen.blit(s, (x - TRACK_W // 2, 0))
            pygame.draw.line(
                self.screen,
                (*LINE_COLOR, alpha),
                (x - TRACK_W // 2, 0),
                (x - TRACK_W // 2, SCREEN_H),
                1,
            )
            pygame.draw.line(
                self.screen,
                (*LINE_COLOR, alpha),
                (x + TRACK_W // 2, 0),
                (x + TRACK_W // 2, SCREEN_H),
                1,
            )

    def _draw_hit_zone(self, active_tracks=None):
        """Desenha a linha da hit zone (pulsante) e os indicadores de botao pixel art."""
        ticks = pygame.time.get_ticks()
        pulse = 0.5 + 0.5 * math.sin(ticks * 0.003)
        b = int(80 + 80 * pulse)
        hit_col = (b // 2, b // 2, b)

        for offset, a in [(3, 40), (2, 80), (1, 160), (0, 255)]:
            color = (*hit_col, a) if offset else hit_col
            pygame.draw.line(
                self.screen, color, (0, HIT_Y + offset), (SCREEN_W, HIT_Y + offset), 1
            )

        size = self.btn_size
        for i, x in enumerate(TRACK_X):
            surf = (
                self.btn_on[i]
                if (active_tracks and i in active_tracks)
                else self.btn_off[i]
            )
            self.screen.blit(surf, (x - size // 2, HIT_Y - size // 2))

    def _draw_notes(self, notes):
        for i, track in enumerate(notes):
            for y in track:
                if 0 <= y <= SCREEN_H + NOTE_RADIUS:
                    self.screen.blit(
                        self.glow_surfs[i], (TRACK_X[i] - GLOW_CENTER, y - GLOW_CENTER)
                    )

    def _draw_hit_effects(self):
        """Desenha burst de acerto: flash central, aneis e particulas."""
        self.scratch.fill((0, 0, 0, 0))
        drawn = False

        for eff in self._hit_effects:
            if not eff["active"]:
                continue
            color = eff["color"]
            x, y = eff["x"], eff["y"]

            # Flash central
            if eff["flash_alpha"] > 0:
                pygame.draw.circle(
                    self.scratch, (*color, eff["flash_alpha"]), (x, y), 28
                )
                pygame.draw.circle(
                    self.scratch, (255, 255, 255, eff["flash_alpha"] // 2), (x, y), 14
                )
                drawn = True

            # Aneis expansivos
            for ring in eff["rings"]:
                if eff["age"] >= ring["delay"] and ring["alpha"] > 0:
                    pygame.draw.circle(
                        self.scratch, (*color, ring["alpha"]), (x, y), ring["r"], 3
                    )
                    drawn = True

            # Particulas
            for p in eff["particles"]:
                if p["alpha"] > 0:
                    px, py = int(p["px"]), int(p["py"])
                    pygame.draw.circle(
                        self.scratch, (*color, p["alpha"]), (px, py), p["size"]
                    )
                    drawn = True

        if drawn:
            self.screen.blit(self.scratch, (0, 0))

    def _draw_hud(self, score, misses, combo):
        self.screen.blit(self.score_backdrop, (8, 8))

        score_txt = self.font(36).render(f"SCORE  {score:06d}", True, (180, 220, 255))
        self.screen.blit(score_txt, (_px(20), _py(16)))

        miss_color = (255, 80, 80) if misses > 5 else (100, 160, 220)
        miss_txt = self.font(28).render(
            f"ERROS {misses}", True, miss_color
        )
        self.screen.blit(miss_txt, (_px(20), _py(52)))

        if combo >= 3:
            ticks = pygame.time.get_ticks()
            if combo >= 20:
                size = 52
                color = (255, 255, 200)
                shadow = (180, 120, 0)
                wobble = int(3 * math.sin(ticks * 0.015))
            elif combo >= 10:
                size = 44
                color = (255, 160, 0)
                shadow = None
                wobble = int(2 * math.sin(ticks * 0.01))
            else:
                size = 36
                color = (255, 170, 0)
                shadow = None
                wobble = 0

            combo_txt = self.font(size).render(f"COMBO x{combo}", True, color)
            base_rect = combo_txt.get_rect(
                topright=(SCREEN_W - _px(20) + wobble, _py(16))
            )
            if shadow:
                shadow_surf = self.font(size).render(f"COMBO x{combo}", True, shadow)
                self.screen.blit(shadow_surf, (base_rect.x + 2, base_rect.y + 2))
            self.screen.blit(combo_txt, base_rect)

        if self._sim_mode:
            status_color = (255, 180, 0)
            status_label = "SIM"
        else:
            status_color = (0, 255, 100) if self.serial.connected else (100, 100, 100)
            status_label = "FPGA ON" if self.serial.connected else "FPGA OFF"
        status_txt = self.font(20).render(status_label, True, status_color)
        self.screen.blit(
            status_txt, status_txt.get_rect(topright=(SCREEN_W - _px(20), _py(54)))
        )

        # Barra de progresso baseada no frame_counter da FPGA (ou sim)
        if self.fpga_state == STATE_PLAY:
            _depth = (
                len(self.sim._chart)
                if self._sim_mode
                else int(SONGS[self._selected_song]["duration"] * FPS)
            )
            ratio = min(1.0, self.fpga_frame_counter / max(1, _depth - 1))
            by = SCREEN_H - _py(14)
            pygame.draw.rect(self.screen, (20, 40, 80), (_px(20), by, self._prog_bw, self._prog_bh), border_radius=4)
            fill_w = int(self._prog_bw * ratio)
            if fill_w > 0:
                self.screen.blit(self._progress_grad, (_px(20), by), (0, 0, fill_w, self._prog_bh))

    # ── Telas ─────────────────────────────────────────────────────────────────

    def _draw_idle(self):
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self._draw_idle_anim()
        self.screen.blit(self.scanline_surf, (0, 0))

        # Titulo 3D Arcade com glow pulsante e flutuacao
        ticks = pygame.time.get_ticks()
        float_y = 6 * math.sin(ticks * 0.002)

        logo_rect = self._logo.get_rect(center=(SCREEN_W // 2, _py(210) + int(float_y)))
        self.screen.blit(self._logo, logo_rect)

        if ticks % 1000 < 650:
            sub = self.font(50).render("Aperte START para jogar", True, (255, 130, 0))
            self.screen.blit(sub, sub.get_rect(center=(SCREEN_W // 2, _py(360))))

        # Fixo (sem piscar): entrada das configuracoes de som
        cfg = self.font(36).render(
            "Aperte D / F / J / K para configurar o som", True, (140, 170, 220)
        )
        self.screen.blit(cfg, cfg.get_rect(center=(SCREEN_W // 2, _py(400))))

        if self._sim_mode:
            hint_text = "[SIM]  SPACE = START   |   D / F / J / K = configuracoes"
        else:
            hint_text = ""
        hint = self.font(30).render(hint_text, True, (60, 95, 155))
        self.screen.blit(hint, hint.get_rect(center=(SCREEN_W // 2, _py(540))))

    def _wrap_text(self, text, size, max_w):
        """Quebra texto em linhas que cabem em max_w pixels."""
        words = text.split()
        lines, current = [], ""
        f = self.font(size)
        for w in words:
            test = (current + " " + w).strip()
            if f.size(test)[0] <= max_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines

    def _draw_song_select(self):
        """Tela de selecao de musica: 4 caixas alinhadas com as trilhas."""
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self.screen.blit(self.scanline_surf, (0, 0))

        now = pygame.time.get_ticks()

        # Logo centralizado no topo
        logo_rect = self._logo_title.get_rect(center=(SCREEN_W // 2, _py(55)))
        self.screen.blit(self._logo_title, logo_rect)

        # Titulo abaixo do logo
        title = self.font(52).render("SELECIONAR MUSICA", True, (180, 220, 255))
        self.screen.blit(title, title.get_rect(center=(SCREEN_W // 2, _py(120))))

        BOX_W, BOX_H = _px(118), _py(230)
        BOX_TOP = _py(155)

        # Caixas com espaçamento uniforme, centradas na tela
        gap = _px(25)
        total_w = 4 * BOX_W + 3 * gap
        x_start = (SCREEN_W - total_w) // 2
        song_cx = [x_start + BOX_W // 2 + i * (BOX_W + gap) for i in range(4)]

        for i, song in enumerate(SONGS):
            cx = song_cx[i]
            color = TRACK_COLORS[i]
            avail = song["available"]

            box_rect = pygame.Rect(cx - BOX_W // 2, BOX_TOP, BOX_W, BOX_H)

            # Fundo
            bg_alpha = 60 if avail else 20
            bg = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
            bg.fill((*color, bg_alpha))
            self.screen.blit(bg, box_rect.topleft)

            # Pulso na caixa disponivel
            if avail:
                pulse = abs((now % 1200) - 600) / 600.0
                glow_a = int(25 + 35 * pulse)
                glow_s = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
                glow_s.fill((*color, glow_a))
                self.screen.blit(glow_s, box_rect.topleft)

            # Borda
            border_col = color if avail else (50, 50, 80)
            pygame.draw.rect(
                self.screen, border_col, box_rect, 2 if avail else 1, border_radius=5
            )

            # Tecla
            key_col = color if avail else (70, 70, 100)
            key_s = self.font(40).render(TRACK_LABELS[i], True, key_col)
            self.screen.blit(key_s, key_s.get_rect(center=(cx, BOX_TOP + _py(26))))

            # Separador
            pygame.draw.line(
                self.screen,
                (*border_col, 100),
                (cx - BOX_W // 2 + _px(10), BOX_TOP + _py(46)),
                (cx + BOX_W // 2 - _px(10), BOX_TOP + _py(46)),
                1,
            )

            if avail:
                y_off = BOX_TOP + _py(58)
                for line in self._wrap_text(song["title"], 18, BOX_W - _px(8)):
                    ls = self.font(18).render(line, True, (235, 240, 255))
                    self.screen.blit(ls, ls.get_rect(center=(cx, y_off)))
                    y_off += _py(20)
                y_off += _py(4)
                for line in self._wrap_text(song["subtitle"], 15, BOX_W - _px(8)):
                    ls = self.font(15).render(line, True, (130, 175, 225))
                    self.screen.blit(ls, ls.get_rect(center=(cx, y_off)))
                    y_off += _py(17)
                if song.get("author"):
                    au = self.font(14).render(song["author"], True, (90, 140, 200))
                    self.screen.blit(au, au.get_rect(center=(cx, y_off + _py(4))))
            else:
                em = self.font(17).render("EM BREVE", True, (60, 80, 130))
                self.screen.blit(em, em.get_rect(center=(cx, BOX_TOP + BOX_H // 2)))

        # Dica
        if self._sim_mode:
            hint_text = "[SIM]  D = Hobbit  |  F = Ocarina  |  J = Power Rangers  |  K = Pokemon  |  SPACE = Voltar"
        else:
            hint_text = ""
        hint = self.font(26).render(hint_text, True, (55, 90, 150))
        self.screen.blit(
            hint, hint.get_rect(center=(SCREEN_W // 2, BOX_TOP + BOX_H + 28))
        )

    def _draw_settings(self):
        """
        Tela de configuracoes: uma coluna por faixa do atenuador, alinhada com a
        trilha do botao que a controla. Estatica de proposito (sem piscar nem
        pulsar): o publico-alvo inclui jogadores sensiveis a estimulos.
        """
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self.screen.blit(self.scanline_surf, (0, 0))

        logo_rect = self._logo_title.get_rect(center=(SCREEN_W // 2, _py(55)))
        self.screen.blit(self._logo_title, logo_rect)
        title = self.font(52).render("CONFIGURAR SOM", True, (180, 220, 255))
        self.screen.blit(title, title.get_rect(center=(SCREEN_W // 2, _py(120))))

        # Mesma grade de caixas da selecao de musica
        BOX_W, BOX_H = _px(118), _py(270)
        BOX_TOP = _py(155)
        gap = _px(25)
        total_w = 4 * BOX_W + 3 * gap
        x_start = (SCREEN_W - total_w) // 2

        SEG_W, SEG_H, SEG_GAP = _px(60), _py(22), _py(6)
        for i, band in enumerate(EQ_BANDS):
            cx = x_start + BOX_W // 2 + i * (BOX_W + gap)
            color = TRACK_COLORS[i]
            level = self._eq[i]
            box_rect = pygame.Rect(cx - BOX_W // 2, BOX_TOP, BOX_W, BOX_H)

            bg = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
            bg.fill((*color, 40))
            self.screen.blit(bg, box_rect.topleft)
            pygame.draw.rect(self.screen, color, box_rect, 2, border_radius=5)

            key_s = self.font(40).render(TRACK_LABELS[i], True, color)
            self.screen.blit(key_s, key_s.get_rect(center=(cx, BOX_TOP + _py(26))))
            pygame.draw.line(
                self.screen,
                color,
                (cx - BOX_W // 2 + _px(10), BOX_TOP + _py(46)),
                (cx + BOX_W // 2 - _px(10), BOX_TOP + _py(46)),
                1,
            )

            name_s = self.font(26).render(band["name"], True, (235, 240, 255))
            self.screen.blit(name_s, name_s.get_rect(center=(cx, BOX_TOP + _py(64))))
            range_s = self.font(16).render(band["range"], True, (130, 175, 225))
            self.screen.blit(range_s, range_s.get_rect(center=(cx, BOX_TOP + _py(84))))

            # Medidor: segmentos acesos = quanto da faixa ainda soa
            # (4 = sem atenuacao, 1 = -18 dB)
            lit = EQ_LEVELS - level
            seg_bottom = BOX_TOP + _py(220)
            for k in range(EQ_LEVELS):
                y = seg_bottom - (k + 1) * SEG_H - k * SEG_GAP
                seg = pygame.Rect(cx - SEG_W // 2, y, SEG_W, SEG_H)
                if k < lit:
                    pygame.draw.rect(self.screen, color, seg, border_radius=3)
                else:
                    pygame.draw.rect(self.screen, (50, 60, 90), seg, 1, border_radius=3)

            db_text = "0 dB" if level == 0 else f"-{EQ_STEP_DB * level} dB"
            db_s = self.font(30).render(db_text, True, (235, 240, 255))
            self.screen.blit(db_s, db_s.get_rect(center=(cx, BOX_TOP + _py(246))))

        info_y = BOX_TOP + BOX_H + _py(24)
        steps = " > ".join(
            "0 dB" if k == 0 else f"-{EQ_STEP_DB * k}" for k in range(EQ_LEVELS)
        )
        info = self.font(30).render(
            f"Cada botao muda uma faixa:  {steps}  > 0 dB", True, (160, 190, 235)
        )
        self.screen.blit(info, info.get_rect(center=(SCREEN_W // 2, info_y)))
        back = self.font(30).render("START = salvar e voltar", True, (255, 130, 0))
        self.screen.blit(back, back.get_rect(center=(SCREEN_W // 2, info_y + _py(26))))

        if self._sim_mode:
            hint = self.font(26).render(
                "[SIM]  D / F / J / K = faixas   |   SPACE = voltar", True, (55, 90, 150)
            )
            self.screen.blit(hint, hint.get_rect(center=(SCREEN_W // 2, info_y + _py(52))))

    def _draw_countdown(self, sec):
        self.screen.fill(BG_COLOR)
        self._draw_tracks()
        self._draw_hit_zone()

        if sec > 0:
            num = self.font(220).render(str(sec), True, (255, 120, 0))
            self.screen.blit(
                num, num.get_rect(center=(SCREEN_W // 2, SCREEN_H // 2 - _py(40)))
            )

        # Nome da musica durante a contagem
        sn = self.font(28).render(
            SONGS[self._selected_song]["title"], True, (150, 200, 240)
        )
        self.screen.blit(
            sn, sn.get_rect(center=(SCREEN_W // 2, SCREEN_H // 2 + _py(70)))
        )

    def _draw_play(self, notes, score, misses, combo):
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()       # 1. grid horizontal sutil
        self._draw_tracks()        # 2. trilhas + bordas verticais
        self._draw_notes(notes)    # 3. notas com glow
        self._draw_hit_zone()      # 4. linha pulsante + botoes
        self._advance_effects()    # 5. avanca estado dos efeitos
        self._draw_hit_effects()   # 6. burst de acerto: aneis + particulas + flash
        self._draw_hud(score, misses, combo)  # 7. painel HUD
        self.screen.blit(self.scanline_surf, (0, 0))  # 8. scanlines CRT

    def _draw_star(self, x, y, size, color):
        pts = []
        for i in range(10):
            angle = math.pi * i / 5 - math.pi / 2
            r = size if i % 2 == 0 else size // 2
            pts.append((x + int(r * math.cos(angle)), y + int(r * math.sin(angle))))
        pygame.draw.polygon(self.screen, color, pts)

    def _draw_pause(self):
        self.screen.fill(BG_COLOR)
        self._draw_tracks(alpha=50)
        self.screen.blit(self.scanline_surf, (0, 0))

        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 155))
        self.screen.blit(overlay, (0, 0))

        cx = SCREEN_W // 2
        ticks = pygame.time.get_ticks()
        pulse = 0.5 + 0.5 * math.sin(ticks * 0.003)

        # Painel central
        pw, ph = _px(310), _py(430)
        px_ = (SCREEN_W - pw) // 2
        py_ = (SCREEN_H - ph) // 2
        panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
        panel.fill((6, 12, 35, 210))
        pygame.draw.rect(panel, (40, 85, 175, 200), (0, 0, pw, ph), 2, border_radius=14)
        self.screen.blit(panel, (px_, py_))

        # Logo
        self.screen.blit(self._logo_pause, self._logo_pause.get_rect(center=(cx, py_ + _py(90))))

        # Separador laranja
        sep_y = py_ + _py(172)
        pygame.draw.line(self.screen, (255, 110, 0, 200), (px_ + _px(25), sep_y), (px_ + pw - _px(25), sep_y), 2)

        # "PAUSADO" com glow pulsante e sombra
        glow_col = (int(120 + 60 * pulse), int(190 + 30 * pulse), 255)
        shadow_s = self.font(85).render("PAUSADO", True, (10, 20, 55))
        pause_s  = self.font(85).render("PAUSADO", True, glow_col)
        txt_y = py_ + _py(255)
        self.screen.blit(shadow_s, shadow_s.get_rect(center=(cx + 3, txt_y + 3)))
        self.screen.blit(pause_s,  pause_s.get_rect(center=(cx, txt_y)))

        # Separador sutil
        sep2_y = py_ + _py(305)
        pygame.draw.line(self.screen, (30, 55, 110), (px_ + _px(40), sep2_y), (px_ + pw - _px(40), sep2_y), 1)

        # Hints
        if self._sim_mode:
            line1, line2 = "SPACE: continuar", "ESC: menu inicial"
        else:
            line1, line2 = "START: continuar", "Amarelo: menu inicial"
        c = (120, 170, 220)
        for i, line in enumerate([line1, line2]):
            s = self.font(36).render(line, True, c)
            self.screen.blit(s, s.get_rect(center=(cx, py_ + _py(340) + i * _py(48))))

        self._draw_hud(self.fpga_score, self.fpga_misses, self.fpga_combo)

    def _draw_endscreen(self, win, score, misses):
        # Fundo vivo: grid + trilhas fantasmas
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)

        cx = SCREEN_W // 2
        ticks = pygame.time.get_ticks()
        pulse = 0.5 + 0.5 * math.sin(ticks * 0.004)

        overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 20, 60, 155) if win else (38, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))
        self.screen.blit(self.scanline_surf, (0, 0))

        # Logo
        self.screen.blit(self._logo_pause, self._logo_pause.get_rect(center=(cx, _py(88))))

        # Separador colorido
        sep_col = (255, 110, 0) if win else (255, 60, 60)
        pygame.draw.line(self.screen, sep_col, (_px(180), _py(168)), (SCREEN_W - _px(180), _py(168)), 2)

        # Mensagem principal com sombra + glow pulsante
        color = (0, 190, 255) if win else (255, 60, 60)
        message = "VITORIA!" if win else "GAME OVER"
        shadow_s = self.font(110).render(message, True, (5, 15, 45) if win else (50, 0, 0))
        msg_txt  = self.font(110).render(message, True, color)
        glow_s   = self.font(110).render(message, True, color)
        glow_surf = pygame.Surface(glow_s.get_size(), pygame.SRCALPHA)
        glow_surf.blit(glow_s, (0, 0))
        glow_surf.set_alpha(int(60 + 50 * pulse))
        msg_y = _py(255)
        self.screen.blit(shadow_s, shadow_s.get_rect(center=(cx + 4, msg_y + 4)))
        self.screen.blit(glow_surf, glow_surf.get_rect(center=(cx, msg_y)))
        self.screen.blit(msg_txt,   msg_txt.get_rect(center=(cx, msg_y)))

        # Painel de score
        pw, ph = _px(290), _py(130)
        px_ = (SCREEN_W - pw) // 2
        py_ = _py(318)
        panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
        panel.fill((6, 12, 35, 210))
        border_col = (0, 140, 255, 200) if win else (200, 50, 50, 200)
        pygame.draw.rect(panel, border_col, (0, 0, pw, ph), 2, border_radius=10)
        self.screen.blit(panel, (px_, py_))

        score_txt = self.font(58).render(f"Score: {score:06d}", True, (255, 200, 100))
        self.screen.blit(score_txt, score_txt.get_rect(center=(cx, py_ + _py(42))))
        miss_txt = self.font(34).render(f"Erros: {misses}", True, (170, 200, 235))
        self.screen.blit(miss_txt, miss_txt.get_rect(center=(cx, py_ + _py(88))))

        # Estrelas (vitoria)
        if win:
            stars = 3 if misses == 0 else (2 if misses <= 10 else (1 if misses <= 20 else 0))
            for i in range(3):
                col = (255, int(180 + 20 * pulse), 0) if i < stars else (35, 45, 75)
                self._draw_star(cx + (i - 1) * _px(55), _py(478), _ps(22), col)

        # Restart pulsante
        if ticks % 1000 < 650:
            restart_y = _py(530) if win else _py(490)
            restart = self.font(40).render("Aperte START para jogar novamente", True, (175, 210, 245))
            self.screen.blit(restart, restart.get_rect(center=(cx, restart_y)))

    # ── Loop principal ────────────────────────────────────────────────────────

    def run(self):
        while True:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    if self.serial:
                        self.serial.stop()
                    pygame.quit()
                    exit()

            if self._sim_mode:
                self._process_sim(events)
            else:
                self._process_serial()

            s = self.fpga_state
            self._update_music(s)

            if s == STATE_IDLE:
                self._draw_idle()
            elif s == STATE_SONG_SELECT:
                self._draw_song_select()
            elif s == STATE_SETTINGS:
                self._draw_settings()
            elif s == STATE_COUNTDOWN:
                self._draw_countdown(self.fpga_countdown)
            elif s == STATE_PLAY:
                self._draw_play(
                    self.fpga_notes, self.fpga_score, self.fpga_misses, self.fpga_combo
                )
            elif s == STATE_PAUSE:
                self._draw_pause()
            elif s == STATE_WIN:
                self.screen.fill(BG_COLOR)
                self._draw_endscreen(
                    win=True, score=self.fpga_score, misses=self.fpga_misses
                )
            elif s == STATE_LOSE:
                self.screen.fill(BG_COLOR)
                self._draw_endscreen(
                    win=False, score=self.fpga_score, misses=self.fpga_misses
                )

            self.screen.blit(self.vignette_surf, (0, 0))
            self._present()
            self.clock.tick(FPS)

    def _present(self):
        """Escala o frame 1800x1080 para a janela, mantendo a proporcao (letterbox)."""
        self.window = pygame.display.get_surface()
        win_w, win_h = self.window.get_size()
        k = min(win_w / SCREEN_W, win_h / SCREEN_H)
        w, h = max(1, int(SCREEN_W * k)), max(1, int(SCREEN_H * k))
        if (w, h) == (SCREEN_W, SCREEN_H):
            self.window.blit(self.screen, (0, 0))
        else:
            self.window.fill((0, 0, 0))
            self.window.blit(
                pygame.transform.smoothscale(self.screen, (w, h)),
                ((win_w - w) // 2, (win_h - h) // 2),
            )
        pygame.display.flip()


# ─── Entrada ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Beat by Bit - Renderer")
    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="Porta serial da FPGA (padrao: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud", type=int, default=115200, help="Baudrate UART (padrao: 115200)"
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Modo simulacao: teclado (D/F/J/K=trilhas, SPACE=START), sem FPGA",
    )
    parser.add_argument(
        "--audio-offset",
        type=int,
        default=0,
        metavar="MS",
        help="Compensa latencia extra do audio em ms (ex.: 200 para fone Bluetooth; "
        "negativo se as notas chegam depois do som)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Escala da janela sobre 1800x1080, ex.: 0.6 (padrao: cabe em 90%% da tela)",
    )
    args = parser.parse_args()

    Renderer(
        serial_port=args.port,
        baudrate=args.baud,
        sim_mode=args.sim,
        scale=args.scale,
        audio_offset_ms=args.audio_offset,
    ).run()
