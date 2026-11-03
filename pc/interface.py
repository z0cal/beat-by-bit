"""
Beat by Bit - Semestre 2
Renderer puro: recebe pacotes da FPGA via UART e renderiza o jogo.
Nenhuma logica de jogo aqui — tudo roda na FPGA (Verilog).

Protocolo UART (115200 baud, 8N1):
  Pacote de 46 bytes a ~60fps (multibyte em big-endian):
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
    --- metricas da sessao (metricas.v), zeradas no COUNTDOWN ---
    [21]    maior combo
    [22-23] N = acertos em botao
    [24-26] soma de e = age - 130 (24 bits, complemento de 2; e<0 = adiantado)
    [27-29] soma de |e|
    [30-32] soma de e^2
    [33-44] por trilha t, 3 bytes a partir de 33+3t: {notas[t], acertos[t]}
            (12 bits cada; notas = notas julgadas, acerto ou nota perdida)
    [45] 0xAA              marcador de fim

  Retorno (PC -> FPGA): 1 byte ACK (0xA5) a cada ACK_INTERVAL s enquanto
  chegam pacotes validos. A FPGA acende LEDR3 enquanto recebe ACKs.

Conversao age -> Y: y = age * NOTE_SPEED  (age=HIT_AGE=130 cai exatamente em HIT_Y)

Niveis do atenuador (eq): 0=0 dB, 1=-6 dB, 2=-12 dB, 3=-18 dB por faixa.
"""

import pygame

# Tamanho do buffer de audio.
# Maior buffer = menos crackling em maquinas lentas, mas mais latencia.
# 4096 @ 44100Hz ≈ 93ms ≈ 6 frames a 60fps — compensado no chart (AUDIO_LATENCY_FRAMES).
_AUDIO_BUFFER = 4096
# Passos que a simulacao recupera de uma vez (10 s). Recupera tudo para nao
# perder a sincronia com o audio; acima disso (ex.: maquina suspensa) reancora.
_SIM_MAX_CATCHUP = 600

# pre_init DEVE ser chamado antes de pygame.init() para garantir as configuracoes
# corretas do mixer desde o inicio (evita double-init e distorcao em algumas maquinas).
pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=_AUDIO_BUFFER)
pygame.init()

import csv
import io
import re
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
AUDIO_LATENCY_FRAMES = round(_AUDIO_BUFFER / 44100 * FPS)  # ≈ 6 frames

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


def _scale_pixel_art(surf, target_w):
    """Escala pixel art por multiplo inteiro (nearest-neighbor), mais proximo de target_w."""
    w, h = surf.get_size()
    k = max(1, round(target_w / w))
    return pygame.transform.scale(surf, (w * k, h * k))


# Parametros que devem bater com note_track.v (HIT_CENTER/HIT_WINDOW/MISS_AGE)
HIT_AGE = 130  # age da nota no centro da hit zone
HIT_WIN = 11  # janela de acerto: ±11 frames
MISS_AGE = 151  # nota escapa apos este age
HIT_Y = _py(520)
# Derivado de HIT_AGE para que age=130 caia exatamente em HIT_Y em qualquer resolucao
# (antes, 4*SY com divisao inteira em float dava HIT_AGE=129 em 1080p).
NOTE_SPEED = HIT_Y / HIT_AGE
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

MAX_MISSES = 10  # derrota ao atingir este numero de erros (igual a 'perdeu' em Verilog)

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

PACKET_SIZE = 46
PKT_HEADER = 0x55
PKT_FOOTER = 0xAA
ACK_BYTE = 0xA5
ACK_INTERVAL = 0.25  # s; a FPGA apaga o LED apos 1 s sem ACK
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
        self._stop_evt = threading.Event()
        self._ack = threading.Event()
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
        last_ack = 0.0
        while not self._stop_evt.is_set():
            try:
                now = time.monotonic()
                if self._ack.is_set() and now - last_ack >= ACK_INTERVAL:
                    self._ack.clear()
                    ser.write(bytes([ACK_BYTE]))
                    last_ack = now
                # read(n>=1) bloqueia ate o timeout (50ms) em vez de girar a CPU em loop
                data = ser.read(max(1, ser.in_waiting))
                if data:
                    with self.buf_lock:
                        self._buf.extend(data)
            except Exception as e:
                print(f"[Serial] Erro: {e}")
                self.connected = False
                break
        try:
            ser.close()
        except Exception:
            pass

    def read_buf(self):
        """Retorna e limpa o buffer acumulado (thread-safe)."""
        with self.buf_lock:
            data = bytes(self._buf)
            self._buf.clear()
        return data

    def request_ack(self):
        """Pede um ACK para a FPGA (enviado pela thread serial, no maximo 1 a cada ACK_INTERVAL)."""
        self._ack.set()

    def stop(self):
        self._stop_evt.set()


# ─── Parser de pacote ─────────────────────────────────────────────────────────


def parse_packet(data, offset):
    """
    Tenta interpretar um pacote de PACKET_SIZE bytes a partir de 'offset' em 'data'.
    Retorna um dict com o estado do jogo, ou None se invalido.
    """
    if offset + PACKET_SIZE > len(data):
        return None
    if data[offset] != PKT_HEADER or data[offset + PACKET_SIZE - 1] != PKT_FOOTER:
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
                track_notes.append(round(age * NOTE_SPEED))
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
        "metrics": _parse_metrics(data, offset),
    }


def _parse_metrics(data, o):
    """Bytes 21-44 do pacote: acumuladores do metricas.v."""

    def u24(i):
        return (data[o + i] << 16) | (data[o + i + 1] << 8) | data[o + i + 2]

    soma_e = u24(24)
    if soma_e & 0x800000:
        soma_e -= 1 << 24
    notas, acertos = [], []
    for t in range(4):
        v = u24(33 + 3 * t)
        notas.append(v >> 12)
        acertos.append(v & 0xFFF)
    return {
        "max_combo": data[o + 21],
        "n_acertos": (data[o + 22] << 8) | data[o + 23],
        "soma_e": soma_e,
        "soma_abs": u24(27),
        "soma_e2": u24(30),
        "notas": notas,
        "acertos": acertos,
    }


def empty_metrics():
    return {
        "max_combo": 0,
        "n_acertos": 0,
        "soma_e": 0,
        "soma_abs": 0,
        "soma_e2": 0,
        "notas": [0] * 4,
        "acertos": [0] * 4,
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
    # Nao encontrou pacote completo; guarda so os ultimos PACKET_SIZE-1 bytes
    keep = min(len(buf), PACKET_SIZE - 1)
    return None, max(0, len(buf) - keep)


# ─── Metricas da sessao e historico (DASHBOARD_SESSAO) ────────────────────────
# A FPGA so acumula (metricas.v); aqui saem taxa, media e desvio. Um quadro vale
# 1000/60 = 16,7 ms, que e a resolucao da assincronia.

FRAME_MS = 1000.0 / FPS
HANDS = (("Mao esquerda", (0, 1)), ("Mao direita", (2, 3)))  # D+F, J+K


def session_summary(m):
    """Metricas derivadas dos acumuladores. Campos sem dado ficam None."""
    n = m["n_acertos"]
    notas = sum(m["notas"])
    out = {
        "acertos": n,
        "notas": notas,
        # floor, igual ao taxa_acerto.v dos displays HEX
        "taxa": (100 * n // notas) if notas else None,
        "maior_combo": m["max_combo"],
        "media_ms": None,
        "desvio_ms": None,
        "abs_media_ms": None,
        "trilhas": list(zip(m["acertos"], m["notas"])),
        "maos": [],
    }
    if n:
        mean = m["soma_e"] / n
        var = max(0.0, m["soma_e2"] / n - mean * mean)
        out["media_ms"] = mean * FRAME_MS
        out["desvio_ms"] = math.sqrt(var) * FRAME_MS
        out["abs_media_ms"] = m["soma_abs"] / n * FRAME_MS
    for name, tracks in HANDS:
        a = sum(m["acertos"][t] for t in tracks)
        nt = sum(m["notas"][t] for t in tracks)
        out["maos"].append((name, a, nt))
    return out


# Pseudonimo vira nivel de topico MQTT (Semana 4): so letras, digitos, _ e -
PSEUDONYM_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
DEFAULT_HISTORY = os.path.join(_ROOT_DIR, "sessoes", "sessoes.csv")

SESSION_FIELDS = (
    ["data_hora", "pseudonimo", "musica", "song_id", "resultado", "score", "erros",
     "acertos", "notas", "taxa_pct", "assinc_media_ms", "assinc_desvio_ms",
     "assinc_abs_media_ms", "maior_combo"]
    + [f"notas_{k}" for k in TRACK_LABELS]
    + [f"acertos_{k}" for k in TRACK_LABELS]
    + ["soma_e", "soma_abs", "soma_e2"]
)


def session_row(m, pseudonym, song_id, won, score, misses, when=None):
    """Linha do CSV de historico (e, na Semana 4, o JSON do MQTT)."""
    summ = session_summary(m)

    def fmt(v):
        return "" if v is None else f"{v:.1f}"

    row = {
        "data_hora": (when or time.strftime("%Y-%m-%dT%H:%M:%S")),
        "pseudonimo": pseudonym or "",
        "musica": SONGS[song_id]["title"] if song_id < len(SONGS) else str(song_id),
        "song_id": song_id,
        "resultado": "vitoria" if won else "derrota",
        "score": score,
        "erros": misses,
        "acertos": summ["acertos"],
        "notas": summ["notas"],
        "taxa_pct": "" if summ["taxa"] is None else summ["taxa"],
        "assinc_media_ms": fmt(summ["media_ms"]),
        "assinc_desvio_ms": fmt(summ["desvio_ms"]),
        "assinc_abs_media_ms": fmt(summ["abs_media_ms"]),
        "maior_combo": summ["maior_combo"],
        "soma_e": m["soma_e"],
        "soma_abs": m["soma_abs"],
        "soma_e2": m["soma_e2"],
    }
    for t, k in enumerate(TRACK_LABELS):
        row[f"notas_{k}"] = m["notas"][t]
        row[f"acertos_{k}"] = m["acertos"][t]
    return row


class SessionLog:
    """
    Historico local das sessoes em CSV (uma linha por musica terminada).
    Nunca derruba o jogo: falha de disco vira mensagem (INDEPENDENCIA_REDE).
    So o pseudonimo identifica a crianca (PRIVACIDADE).
    """

    def __init__(self, path=DEFAULT_HISTORY):
        self.path = path

    def previous(self, pseudonym):
        """Ultima sessao gravada do mesmo pseudonimo, ou None."""
        if not pseudonym or not os.path.exists(self.path):
            return None
        last = None
        try:
            with open(self.path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("pseudonimo") == pseudonym:
                        last = row
        except (OSError, csv.Error) as e:
            print(f"[historico] nao foi possivel ler {self.path}: {e}")
            return None
        return last

    def append(self, row):
        """Grava a linha. Retorna (ok, mensagem)."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=SESSION_FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
            shown = self.path
            if os.path.abspath(self.path).startswith(_ROOT_DIR + os.sep):
                shown = os.path.relpath(self.path, _ROOT_DIR)
            return True, f"Sessao salva em {shown}"
        except OSError as e:
            print(f"[historico] falha ao gravar {self.path}: {e}")
            return False, "Falha ao salvar a sessao (veja o terminal)"


def _num(v):
    """Campo numerico do CSV; vazio ou invalido vira None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def session_deltas(summ, prev):
    """
    Variacao em relacao a sessao anterior: {campo: (delta, melhorou)}.
    melhorou e None quando nao ha mudanca. Campos sem dado ficam fora.
    """
    if prev is None:
        return {}
    # (campo do resumo, coluna do CSV, maior e melhor?)
    pairs = [
        ("taxa", "taxa_pct", True),
        ("media_ms", "assinc_media_ms", None),  # sinal: mais perto de 0 e melhor
        ("desvio_ms", "assinc_desvio_ms", False),
        ("maior_combo", "maior_combo", True),
    ]
    out = {}
    for key, col, higher_better in pairs:
        cur, old = summ.get(key), _num(prev.get(col))
        if cur is None or old is None:
            continue
        d = cur - old
        if abs(d) < 0.05:
            out[key] = (0.0, None)
        elif higher_better is None:
            out[key] = (d, abs(cur) < abs(old))
        else:
            out[key] = (d, (d > 0) == higher_better)
    return out


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
    # Toda nota precisa ser julgada (acerto ou perdida) antes do fim do chart,
    # senao fica sem jogar e fora das metricas: a nota perdida sai da tela com
    # age 152 (_LANE_SLOT_SPAN), e o jogo vai para o END no ultimo frame.
    cands = [
        n for n in cands if 0 <= n["frame"] and n["frame"] + _LANE_SLOT_SPAN + 1 < total_frames
    ]

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
    HIT_WIN = HIT_WIN  # janela de acerto: ±11 frames
    MISS_AGE = MISS_AGE  # nota escapa apos este age
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
        # Metricas da sessao: como o metricas.v, zeram so no COUNTDOWN
        self._met = empty_metrics()
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
            if self.misses >= MAX_MISSES:
                self.state = STATE_LOSE
            elif self._chart_frame >= self._rom_depth:
                self.state = STATE_WIN

        countdown_sec = (
            max(0, 3 - self._cd_frame // 60) if self.state == STATE_COUNTDOWN else 0
        )
        notes = []
        for track in self._ages:
            notes.append([round(age * NOTE_SPEED) for age in track if age != self.EMPTY])

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
            "metrics": {
                k: (list(v) if isinstance(v, list) else v) for k, v in self._met.items()
            },
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
        self._met = empty_metrics()
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
        for t, track in enumerate(self._ages):
            for i in range(self.SLOTS):
                if track[i] != self.EMPTY and track[i] > self.MISS_AGE:
                    track[i] = self.EMPTY
                    self.misses += 1
                    self.combo = 0
                    self._met["notas"][t] += 1

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
                    e = track[i] - self.HIT_AGE  # assincronia em quadros
                    track[i] = self.EMPTY
                    self.score += 100 + self.combo
                    self.combo += 1
                    m = self._met
                    m["n_acertos"] += 1
                    m["soma_e"] += e
                    m["soma_abs"] += abs(e)
                    m["soma_e2"] += e * e
                    m["notas"][t] += 1
                    m["acertos"][t] += 1
                    m["max_combo"] = max(m["max_combo"], min(self.combo, 255))
                    hit = True
                    break
            if not hit:
                self.combo = 0


# ─── Renderer ─────────────────────────────────────────────────────────────────


class Renderer:
    def __init__(
        self,
        serial_port="/dev/ttyUSB0",
        baudrate=115200,
        sim_mode=False,
        scale=None,
        audio_offset_ms=0,
        pseudonym=None,
        history_path=DEFAULT_HISTORY,
    ):
        self._sim_mode = sim_mode
        # Sessao: so o pseudonimo identifica a crianca (PRIVACIDADE)
        self._pseudonym = pseudonym
        self._log = SessionLog(history_path)
        self._session_live = False  # viu PLAY desde o ultimo fim de musica
        self._dash = None  # dados do dashboard, montados ao entrar no fim
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
        self.fpga_metrics = empty_metrics()  # acumuladores do metricas.v

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
        self._build_song_previews()
        self._init_idle_anim()
        self._init_music()
        self._load_logo()

    def _load_logo(self):
        # Logo/icone da identidade visual (identidade/png/*-1x.png, derivados dos SVGs).
        # Sao pixel art: escala por multiplo inteiro (sem suavizacao) para manter os pixels nitidos.
        img_dir = os.path.join(_ASSETS_DIR, "images")
        logo = pygame.image.load(os.path.join(img_dir, "logo-escuro-1x.png")).convert_alpha()
        icon = pygame.image.load(os.path.join(img_dir, "icone-1x.png")).convert_alpha()
        self._logo = _scale_pixel_art(logo, int(SCREEN_W * 0.30))
        self._logo_select = _scale_pixel_art(icon, _ps(50))
        self._logo_pause = _scale_pixel_art(logo, int(SCREEN_W * 0.14))

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
        self._music_started = False  # evita reiniciar a musica quando ela termina antes do WIN
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
                elif not self._music_started:
                    if self._audio_offset >= 0:
                        # inicia do zero, pulando o trecho que a latencia atrasaria
                        pygame.mixer.music.play(start=self._audio_offset)
                        self._music_started = True
                    else:
                        now = time.perf_counter()
                        if self._music_start_at is None:
                            self._music_start_at = now - self._audio_offset
                        if now >= self._music_start_at:
                            pygame.mixer.music.play()
                            self._music_start_at = None
                            self._music_started = True
        elif state == STATE_PAUSE:
            if playing:
                pygame.mixer.music.pause()
                self._music_paused = True
                self._music_started = True
        else:  # IDLE, SELECT, COUNTDOWN, WIN, LOSE
            if playing:
                pygame.mixer.music.stop()
            self._music_paused = False
            self._music_started = False
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

            # ON: estilo arcade aceso (brilho + preenchido)
            on = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(on, (*color, 100), (center, center), btn_radius + 8)
            pygame.draw.circle(on, (*color, 200), (center, center), btn_radius + 4)
            pygame.draw.circle(on, color, (center, center), btn_radius)
            pygame.draw.circle(on, (255, 255, 255), (center, center), btn_radius, 2)
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
            self.serial.request_ack()
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
            self.fpga_metrics = pkt["metrics"]
            self._detect_note_events()

    def _detect_note_events(self):
        """Detecta notas que sumiram e cria efeito de acerto ou erro."""
        scored = (
            self.fpga_score > self._prev_score or self.fpga_combo > self._prev_combo
        )
        if not scored:
            return
        hit_tol = (HIT_WIN + 1) * NOTE_SPEED
        for i in range(4):
            # Toda nota anda a cada frame, entao comparar valores de Y exatos marcaria
            # todas como "sumidas". Casa cada nota anterior (da mais velha para a mais
            # nova) com uma nota atual que esteja na mesma posicao ou mais adiante;
            # a que ficar sem par foi removida.
            curr = sorted(self.fpga_notes[i], reverse=True)
            for y in sorted(self._prev_notes[i], reverse=True):
                match = next((c for c in curr if c >= y), None)
                if match is not None:
                    curr.remove(match)
                elif abs(y - HIT_Y) <= hit_tol:
                    # Nota sumiu na janela de acerto E score/combo subiu → acerto
                    self._spawn_hit_effect(i, TRACK_X[i], HIT_Y)

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
        self.fpga_metrics = pkt["metrics"]
        self._detect_note_events()

    # ── Sessao: fecha ao fim da musica ────────────────────────────────────────

    def _update_session(self, state):
        """Monta o dashboard (e grava o historico) uma vez por fim de musica."""
        if state == STATE_PLAY:
            self._session_live = True
        if state in (STATE_WIN, STATE_LOSE):
            if self._dash is None:
                self._dash = self._close_session(won=(state == STATE_WIN))
        else:
            self._dash = None

    def _close_session(self, won):
        m = self.fpga_metrics
        summ = session_summary(m)
        prev = self._log.previous(self._pseudonym)
        if self._session_live:
            row = session_row(
                m, self._pseudonym, self._selected_song, won,
                self.fpga_score, self.fpga_misses,
            )
            ok, msg = self._log.append(row)
        else:
            # A interface abriu com a musica ja terminada: nao ha sessao inteira
            ok, msg = False, "Sessao nao gravada: a musica terminou antes de abrir a interface"
        self._session_live = False
        return {
            "won": won,
            "summary": summ,
            "deltas": session_deltas(summ, prev),
            "prev": prev,
            "saved": ok,
            "msg": msg,
        }

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

        logo_rect = self._logo.get_rect(center=(SCREEN_W // 2, _py(190) + int(float_y)))
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

    def _build_song_previews(self):
        """Le os .hex de cada musica e resume a densidade de notas em barras (mini-preview do chart)."""
        bins_n = 36
        self._song_previews = []
        for song in SONGS:
            preview = {"bins": [0] * bins_n, "notes": 0}
            try:
                with open(song["hex"], encoding="ascii") as fh:
                    rom = [int(line, 16) for line in fh if line.strip()]
                per_bin = max(1, len(rom) / bins_n)
                for f, v in enumerate(rom):
                    if v:
                        preview["bins"][min(bins_n - 1, int(f / per_bin))] += bin(v).count("1")
                        preview["notes"] += bin(v).count("1")
            except (OSError, ValueError):
                preview = None
            self._song_previews.append(preview)

    def _draw_song_card(self, i, song, rect, now):
        """Desenha um card de musica: botao da trilha, titulo, preview do chart e estatisticas."""
        color = TRACK_COLORS[i]
        avail = song["available"]
        cx = rect.centerx

        # Painel (fundo escuro + tonalidade da cor da trilha)
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*TRACK_BG, 235), panel.get_rect(), border_radius=_ps(10))
        if avail:
            pulse = 0.5 + 0.5 * math.sin(now * 0.004 + i * 0.9)
            pygame.draw.rect(panel, (*color, int(14 + 14 * pulse)), panel.get_rect(), border_radius=_ps(10))
        self.screen.blit(panel, rect.topleft)

        # Faixa colorida no topo + borda (brilho pulsante nos disponiveis)
        border_col = color if avail else (50, 50, 80)
        if avail:
            glow = pygame.Surface((rect.w + 24, rect.h + 24), pygame.SRCALPHA)
            pygame.draw.rect(glow, (*color, int(18 + 30 * pulse)), glow.get_rect(), 6, border_radius=_ps(14))
            self.screen.blit(glow, (rect.x - 12, rect.y - 12))
        pygame.draw.rect(self.screen, border_col, rect, 2, border_radius=_ps(10))
        strip = pygame.Rect(rect.x + _ps(10), rect.y, rect.w - 2 * _ps(10), _ps(5))
        pygame.draw.rect(self.screen, border_col, strip, border_radius=_ps(3))

        # Botao arcade da trilha com a tecla
        btn = self.btn_on[i] if avail else self.btn_off[i]
        by = rect.y + _py(44)
        self.screen.blit(btn, btn.get_rect(center=(cx, by)))
        key_s = self.font(40).render(TRACK_LABELS[i], True, BG_COLOR if avail else (90, 90, 120))
        self.screen.blit(key_s, key_s.get_rect(center=(cx, by + 2)))

        sep_y = rect.y + _py(82)
        pygame.draw.line(self.screen, border_col, (rect.x + _px(14), sep_y), (rect.right - _px(14), sep_y), 1)

        if not avail:
            em = self.font(22).render("EM BREVE", True, (60, 80, 130))
            self.screen.blit(em, em.get_rect(center=rect.center))
            return

        # Titulo e subtitulo
        text_w = rect.w - _px(16)
        y = sep_y + _py(18)
        for line in self._wrap_text(song["title"], 24, text_w):
            ls = self.font(24).render(line, True, (240, 245, 255))
            self.screen.blit(ls, ls.get_rect(center=(cx, y)))
            y += _py(18)
        y += _py(4)
        for line in self._wrap_text(song["subtitle"], 17, text_w):
            ls = self.font(17).render(line, True, (130, 175, 225))
            self.screen.blit(ls, ls.get_rect(center=(cx, y)))
            y += _py(14)
        if song.get("author"):
            au = self.font(15).render(song["author"], True, (90, 140, 200))
            self.screen.blit(au, au.get_rect(center=(cx, y + _py(4))))

        # Mini-preview do chart (densidade de notas ao longo da musica)
        bh = _py(90)
        bw = rect.w - 2 * _px(14)
        bx = rect.x + _px(14)
        bottom = rect.bottom - _py(34)
        pygame.draw.line(self.screen, LINE_COLOR, (bx, bottom), (bx + bw, bottom), 1)
        preview = self._song_previews[i] if i < len(self._song_previews) else None
        if preview and preview["notes"]:
            bins = preview["bins"]
            peak = max(bins)
            slot = bw / len(bins)
            for k, v in enumerate(bins):
                h = max(2, int(bh * v / peak)) if v else 1
                bar = pygame.Rect(int(bx + k * slot) + 1, bottom - h, max(1, int(slot) - 2), h)
                pygame.draw.rect(self.screen, color if v else LINE_COLOR, bar)
            notes_txt = f"{preview['notes']} notas"
        else:
            notes_txt = ""

        # Estatisticas: duracao e numero de notas
        mins, secs = divmod(int(round(song["duration"])), 60)
        stat_y = rect.bottom - _py(17)
        dur = self.font(17).render(f"{mins}:{secs:02d}", True, (150, 190, 235))
        self.screen.blit(dur, dur.get_rect(midleft=(bx, stat_y)))
        if notes_txt:
            nt = self.font(17).render(notes_txt, True, (150, 190, 235))
            self.screen.blit(nt, nt.get_rect(midright=(bx + bw, stat_y)))

    def _draw_song_select(self):
        """Tela de selecao de musica: 4 cards alinhados com as cores das trilhas."""
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self.screen.blit(self.scanline_surf, (0, 0))

        now = pygame.time.get_ticks()
        cx = SCREEN_W // 2

        # Cabecalho: icone, titulo e sublinhado laranja
        self.screen.blit(self._logo_select, self._logo_select.get_rect(center=(cx, _py(62))))
        title = self.font(56).render("SELECIONAR MUSICA", True, (190, 225, 255))
        shadow = self.font(56).render("SELECIONAR MUSICA", True, (10, 20, 55))
        ty = _py(118)
        self.screen.blit(shadow, shadow.get_rect(center=(cx + 3, ty + 3)))
        self.screen.blit(title, title.get_rect(center=(cx, ty)))
        pygame.draw.line(
            self.screen, (255, 110, 0), (cx - _px(60), _py(140)), (cx + _px(60), _py(140)), 3
        )

        # Cards centralizados
        box_w, box_h = _px(130), _py(300)
        box_top = _py(165)
        gap = _px(20)
        x_start = (SCREEN_W - (4 * box_w + 3 * gap)) // 2
        for i, song in enumerate(SONGS):
            rect = pygame.Rect(x_start + i * (box_w + gap), box_top, box_w, box_h)
            self._draw_song_card(i, song, rect, now)

        # Dica
        if self._sim_mode:
            hint_text = "[SIM]  D = Hobbit  |  F = Ocarina  |  J = Power Rangers  |  K = Pokemon  |  SPACE = Voltar"
        else:
            hint_text = "Aperte o botao da cor da musica para comecar"
        if now % 1400 < 1000 or self._sim_mode:
            hint = self.font(28).render(hint_text, True, (90, 135, 200))
            self.screen.blit(hint, hint.get_rect(center=(cx, box_top + box_h + _py(30))))

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

        logo_rect = self._logo_select.get_rect(center=(SCREEN_W // 2, _py(55)))
        self.screen.blit(self._logo_select, logo_rect)
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

    # ── Dashboard de fim de sessao (DASHBOARD_SESSAO) ─────────────────────────
    # Estatico de proposito (sem piscar nem pulsar), como a tela de
    # configuracoes. Valores em texto neutro; a cor da trilha so marca a
    # identidade na barra, ao lado da tecla escrita.

    _DASH_TEXT = (235, 240, 255)
    _DASH_LABEL = (140, 170, 220)
    _DASH_MUTED = (90, 115, 160)
    _DASH_PANEL = (10, 18, 42)
    _DASH_BORDER = (40, 75, 140)
    _DASH_BETTER = (110, 220, 150)
    _DASH_WORSE = (240, 170, 90)

    def _dash_text(self, text, size, color, **pos):
        surf = self.font(size).render(text, True, color)
        self.screen.blit(surf, surf.get_rect(**pos))

    def _dash_panel(self, rect):
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        panel.fill((*self._DASH_PANEL, 225))
        self.screen.blit(panel, rect.topleft)
        pygame.draw.rect(self.screen, self._DASH_BORDER, rect, 2, border_radius=8)

    def _dash_delta(self, key, unit):
        """Texto e cor da variacao em relacao a sessao anterior."""
        dash = self._dash
        if not self._pseudonym:
            return "", self._DASH_MUTED
        if dash["prev"] is None or key not in dash["deltas"]:
            return "", self._DASH_MUTED
        d, better = dash["deltas"][key]
        if better is None:
            return "igual a anterior", self._DASH_MUTED
        word = "melhor" if better else "pior"
        color = self._DASH_BETTER if better else self._DASH_WORSE
        if key == "media_ms":
            # Melhor = mais perto de 0; a diferenca com sinal confunde aqui
            old = dash["summary"]["media_ms"] - d
            return f"antes {old:+.0f}{unit} ({word})", color
        return f"{d:+.0f}{unit} vs anterior ({word})", color

    def _draw_dashboard(self):
        dash = self._dash
        if dash is None:  # primeiro quadro do fim, antes de _update_session
            return
        summ = dash["summary"]

        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self.screen.blit(self.scanline_surf, (0, 0))

        cx = SCREEN_W // 2
        self.screen.blit(self._logo_select, self._logo_select.get_rect(center=(cx, _py(46))))
        title = "VITORIA!" if dash["won"] else "FIM DE JOGO"
        self._dash_text(title, 60, (0, 190, 255) if dash["won"] else (255, 90, 90),
                        center=(cx, _py(104)))
        who = self._pseudonym or "sem pseudonimo"
        sub = (f"{SONGS[self._selected_song]['title']}   |   {who}   |   "
               f"Score {self.fpga_score:06d}   |   Erros {self.fpga_misses}")
        self._dash_text(sub, 28, self._DASH_LABEL, center=(cx, _py(136)))

        # ── Linha de indicadores ─────────────────────────────────────────────
        tiles = []
        if summ["taxa"] is None:
            tiles.append(("Taxa de acerto", "--", "nenhuma nota julgada", "taxa", " pp"))
        else:
            tiles.append(("Taxa de acerto", f"{summ['taxa']}%",
                          f"{summ['acertos']} de {summ['notas']} notas", "taxa", " pp"))
        if summ["media_ms"] is None:
            tiles.append(("Assincronia media", "--", "sem acertos", "media_ms", " ms"))
            tiles.append(("Desvio-padrao", "--", "sem acertos", "desvio_ms", " ms"))
        else:
            mean = summ["media_ms"]
            sense = ("no tempo" if abs(mean) < 0.5 else
                     "adiantado" if mean < 0 else "atrasado")
            tiles.append(("Assincronia media", f"{mean:+.0f} ms", sense, "media_ms", " ms"))
            tiles.append(("Desvio-padrao", f"{summ['desvio_ms']:.0f} ms",
                          "variacao do tempo", "desvio_ms", " ms"))
        tiles.append(("Maior combo", str(summ["maior_combo"]), "acertos seguidos",
                      "maior_combo", ""))

        TILE_W, TILE_H, GAP = _px(180), _py(118), _px(10)
        x0 = (SCREEN_W - (4 * TILE_W + 3 * GAP)) // 2
        top = _py(156)
        for i, (label, value, note, key, unit) in enumerate(tiles):
            r = pygame.Rect(x0 + i * (TILE_W + GAP), top, TILE_W, TILE_H)
            self._dash_panel(r)
            self._dash_text(label, 26, self._DASH_LABEL, center=(r.centerx, r.y + _py(16)))
            self._dash_text(value, 64, self._DASH_TEXT, center=(r.centerx, r.y + _py(50)))
            self._dash_text(note, 22, self._DASH_MUTED, center=(r.centerx, r.y + _py(80)))
            delta, color = self._dash_delta(key, unit)
            if delta:
                self._dash_text(delta, 22, color, center=(r.centerx, r.y + _py(100)))

        # ── Paineis inferiores ───────────────────────────────────────────────
        PANEL_W = (4 * TILE_W + 3 * GAP - GAP) // 2
        ptop, ph = top + TILE_H + _py(12), _py(196)
        self._draw_dash_tracks(pygame.Rect(x0, ptop, PANEL_W, ph), summ)
        self._draw_dash_timing(
            pygame.Rect(x0 + PANEL_W + GAP, ptop, PANEL_W, ph), summ
        )

        # ── Rodape ───────────────────────────────────────────────────────────
        y = ptop + ph + _py(18)
        self._dash_text(
            "Gestos: ainda nao disponivel   |   Metronomo: ainda nao disponivel",
            22, self._DASH_MUTED, center=(cx, y),
        )
        prev = dash["prev"]
        if prev is not None:
            when = prev.get("data_hora", "").replace("T", " ")[:16]
            compare = f"Comparado com a sessao de {when} ({prev.get('musica', '')})"
        elif self._pseudonym:
            compare = f"Primeira sessao de {self._pseudonym} no historico"
        else:
            compare = "Sem pseudonimo: sem comparacao (use --pseudonimo)"
        self._dash_text(compare, 22, self._DASH_LABEL, center=(cx, y + _py(20)))
        self._dash_text(dash["msg"], 20,
                        self._DASH_MUTED if dash["saved"] else self._DASH_WORSE,
                        center=(cx, y + _py(38)))

        back = "SPACE = voltar" if self._sim_mode else "Aperte START para voltar"
        self._dash_text(back, 36, (255, 130, 0), center=(cx, y + _py(66)))

    def _draw_dash_tracks(self, rect, summ):
        """Acertos por trilha: medidor por trilha + resumo por mao."""
        self._dash_panel(rect)
        x, y = rect.x + _px(14), rect.y
        self._dash_text("Acertos por trilha", 28, self._DASH_TEXT,
                        midleft=(x, y + _py(18)))
        BAR_X, BAR_W, BAR_H = x + _px(28), _px(190), _py(14)
        for t, (hits, notes) in enumerate(summ["trilhas"]):
            ry = y + _py(48) + t * _py(28)
            self._dash_text(TRACK_LABELS[t], 30, self._DASH_TEXT, midleft=(x, ry))
            color = TRACK_COLORS[t]
            track_col = tuple(int(c * 0.28) for c in color)
            bar = pygame.Rect(BAR_X, ry - BAR_H // 2, BAR_W, BAR_H)
            pygame.draw.rect(self.screen, track_col, bar, border_radius=4)
            if notes:
                fill = int(BAR_W * hits / notes)
                if fill > 0:
                    pygame.draw.rect(self.screen, color, (bar.x, bar.y, fill, BAR_H),
                                     border_radius=4)
                txt = f"{hits}/{notes}   {100 * hits // notes}%"
            else:
                txt = "sem notas"
            self._dash_text(txt, 24, self._DASH_LABEL,
                            midleft=(BAR_X + BAR_W + _px(10), ry))
        hy = y + _py(48) + 4 * _py(28) + _py(4)
        pygame.draw.line(self.screen, self._DASH_BORDER, (x, hy - _py(10)),
                         (rect.right - _px(14), hy - _py(10)), 1)
        for i, (name, hits, notes) in enumerate(summ["maos"]):
            keys = "+".join(TRACK_LABELS[t] for t in HANDS[i][1])
            val = f"{100 * hits // notes}%" if notes else "--"
            self._dash_text(f"{name} ({keys}): {val}", 24, self._DASH_LABEL,
                            midleft=(x, hy + i * _py(20)))

    def _draw_dash_timing(self, rect, summ):
        """Assincronia: eixo da janela de acerto com a media e a faixa +-desvio."""
        self._dash_panel(rect)
        x = rect.x + _px(14)
        self._dash_text("Assincronia dos acertos (ms)", 28, self._DASH_TEXT,
                        midleft=(x, rect.y + _py(18)))

        lim_ms = SimulationEngine.HIT_WIN * FRAME_MS  # borda da janela (~183 ms)
        ax0, ax1 = rect.x + _px(28), rect.right - _px(28)
        ay = rect.y + _py(84)
        mean, std = summ["media_ms"], summ["desvio_ms"]

        def to_x(ms):
            ms = max(-lim_ms, min(lim_ms, ms))
            return int(ax0 + (ms + lim_ms) / (2 * lim_ms) * (ax1 - ax0))

        # Faixa media +- desvio, acima do eixo (nao cobre os rotulos)
        if mean is not None:
            band = pygame.Rect(to_x(mean - std), ay - _py(16), 0, _py(16))
            band.width = max(2, to_x(mean + std) - band.x)
            surf = pygame.Surface(band.size, pygame.SRCALPHA)
            surf.fill((0, 180, 255, 70))
            self.screen.blit(surf, band.topleft)

        # Eixo e marcas (recessivos)
        pygame.draw.line(self.screen, self._DASH_BORDER, (ax0, ay), (ax1, ay), 2)
        for ms in (-150, -100, -50, 0, 50, 100, 150):
            tx = to_x(ms)
            h = _py(8) if ms == 0 else _py(4)
            pygame.draw.line(self.screen, (90, 120, 180) if ms == 0 else self._DASH_BORDER,
                             (tx, ay - h), (tx, ay + h), 2 if ms == 0 else 1)
            self._dash_text(f"{ms:+d}" if ms else "0", 18, self._DASH_MUTED,
                            center=(tx, ay + _py(16)))
        self._dash_text("adiantado", 22, self._DASH_MUTED, midleft=(ax0, ay + _py(34)))
        self._dash_text("atrasado", 22, self._DASH_MUTED, midright=(ax1, ay + _py(34)))

        if mean is None:
            self._dash_text("sem acertos nesta musica", 24, self._DASH_LABEL,
                            center=(rect.centerx, ay - _py(30)))
            return
        # Marcador da media
        mx = to_x(mean)
        pygame.draw.line(self.screen, (0, 180, 255), (mx, ay - _py(20)), (mx, ay + _py(4)), 3)
        pygame.draw.circle(self.screen, BG_COLOR, (mx, ay - _py(24)), _ps(7))
        pygame.draw.circle(self.screen, (0, 180, 255), (mx, ay - _py(24)), _ps(5))

        self._dash_text(f"media {mean:+.0f} ms   (faixa = media +- desvio)", 22,
                        self._DASH_LABEL, midleft=(x, rect.y + _py(150)))
        self._dash_text(
            f"erro medio {summ['abs_media_ms']:.0f} ms   |   janela +-{lim_ms:.0f} ms"
            f"   |   1 quadro = {FRAME_MS:.1f} ms",
            20, self._DASH_MUTED, midleft=(x, rect.y + _py(172)),
        )

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
            self._update_session(s)

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
            elif s in (STATE_WIN, STATE_LOSE):
                self._draw_dashboard()

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
    parser.add_argument(
        "--pseudonimo",
        default=None,
        help="Pseudonimo da crianca (nunca o nome): letras, digitos, _ e -, ate 24. "
        "Identifica as sessoes no historico e habilita a comparacao com a anterior",
    )
    parser.add_argument(
        "--historico",
        default=DEFAULT_HISTORY,
        metavar="CSV",
        help="Arquivo CSV do historico de sessoes (padrao: sessoes/sessoes.csv)",
    )
    args = parser.parse_args()
    if args.pseudonimo is not None and not PSEUDONYM_RE.match(args.pseudonimo):
        parser.error("--pseudonimo: use so letras, digitos, _ e - (ate 24)")

    Renderer(
        serial_port=args.port,
        baudrate=args.baud,
        sim_mode=args.sim,
        scale=args.scale,
        audio_offset_ms=args.audio_offset,
        pseudonym=args.pseudonimo,
        history_path=args.historico,
    ).run()
