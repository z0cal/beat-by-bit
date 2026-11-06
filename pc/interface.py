"""
Beat by Bit - Semestre 2
Renderer puro: recebe pacotes da FPGA via UART e renderiza o jogo.
Nenhuma logica de jogo aqui — tudo roda na FPGA (Verilog).

Protocolo UART (921600 baud, 8N1; --baud muda):
  Pacote de 56 bytes a ~60fps (multibyte em big-endian):
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
    --- nome do perfil (nome_perfil.v), editado no fim de musica ---
    [45]    {cursor[1:0], c0}  c0 = caractere da esquerda
    [46]    c1
    [47]    c2                 caractere: 0='-', 1..26='A'..'Z', 27..36='0'..'9'
    --- sensor HC-SR04, nota de gesto, configuracao e metronomo ---
    [48]    distancia em cm (saturada em 255; 0 = sem eco)
    [49]    idade da nota de gesto (0xFF = nenhuma)
    [50]    gestos apresentados
    [51]    gestos acertados
    [52]    config: [1:0] modo do metronomo (0 desl., 1 fixo, 2 adaptativo=fixo),
            [2] perfil sensorial, [3] sem som, [4] pagina das configuracoes
            (0 = atenuador, 1 = jogo), [7:5] progresso do gesto de confirmacao (0-7)
    [53]    metronomo: [1:0] nivel (0=60, 1=30, 2=15 graus, 3=parado), [2] lado do braco
    [54]    reservado (0)
    [55] 0xAA              marcador de fim

  Retorno (PC -> FPGA): 1 byte ACK (0xA5) a cada ACK_INTERVAL s enquanto
  chegam pacotes validos. A FPGA acende LEDR3 enquanto recebe ACKs.

Conversao age -> Y: y = age * NOTE_SPEED  (age=HIT_AGE=130 cai exatamente em HIT_Y)

Niveis do atenuador (eq): 0=0 dB, 1=-6 dB, 2=-12 dB, 3=-18 dB por faixa.
"""

import pygame

# Tamanho do buffer de audio.
# Maior buffer = menos crackling em maquinas lentas, mas mais latencia.
# 4096 @ 44100Hz ≈ 92,9 ms: compensado no inicio da musica (AUDIO_BUFFER_S, ver
# Renderer._play_music), nao no chart (CHART_LEAD_FRAMES).
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
# Antecedencia FIXA do chart (ROM da FPGA), independente do hardware de audio do PC:
# o som do instante t (s) da musica deve sair quando a nota chega ao alvo, no quadro
# t*60 + CHART_LEAD_FRAMES contado a partir do quadro 0 do PLAY (quadro em que a nota
# do chart[0] nasce, age 0; frame_counter = 1 no primeiro pacote do PLAY). A nota do
# chart[f] chega ao alvo (age HIT_AGE) no quadro f + HIT_AGE, entao
#   f = round(t*60) - HIT_AGE + CHART_LEAD_FRAMES.
# O PC cumpre a promessa iniciando o WAV adiantado (play com pos calculado); a folga
# de 6 quadros (100 ms) e o tempo que o PC tem para isso e e igual ao buffer de audio
# nominal, mas nao depende dele.
CHART_LEAD_FRAMES = 6
# Latencia real do buffer do mixer (float, s): entre play() e o som sair.
AUDIO_BUFFER_S = _AUDIO_BUFFER / 44100

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
        "title": "Super Patrulha Delta",
        "subtitle": "Power Rangers",
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
        "title": "Opening Theme",
        "subtitle": "Pokemon",
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

PACKET_SIZE = 56
PKT_HEADER = 0x55
PKT_FOOTER = 0xAA
ACK_BYTE = 0xA5
ACK_INTERVAL = 0.25  # s; a FPGA apaga o LED apos 1 s sem ACK
EMPTY_SLOT = 0xFF
GESTO_COR = (230, 70, 255)  # magenta: nao pertence a nenhuma trilha
FAIXA_MIN_CM, FAIXA_MAX_CM = 5, 25  # faixa do gesto (gesto_detector.v)
GESTO_CONFIRMA_LEITURAS = 16  # leituras seguidas na faixa que trocam a pagina
GESTO_REARMA_LEITURAS = 4  # leituras seguidas fora da faixa que rearmam
LEITURA_FRAMES = 3.6  # ~60 ms entre leituras do HC-SR04, em quadros de 1/60 s
MUSIC_VOLUME = 0.70
MET_ANGULOS = (60, 30, 15, 0)  # graus por nivel do metronomo (3 = parado)

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

    def __init__(self, port="/dev/ttyUSB0", baudrate=921_600):
        super().__init__(daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.buf_lock = threading.Lock()
        self._buf = bytearray()
        # Carimbo de chegada: (posicao absoluta do fim do trecho, perf_counter) por leitura
        self._total = 0
        self._stamps = []
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
                    t = time.perf_counter()
                    with self.buf_lock:
                        self._buf.extend(data)
                        self._total += len(data)
                        self._stamps.append((self._total, t))
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

    def read_stamped(self):
        """Como read_buf, mas devolve tambem [(fim_abs, t_chegada)] dos trechos lidos."""
        with self.buf_lock:
            data = bytes(self._buf)
            stamps = self._stamps
            self._buf.clear()
            self._stamps = []
        return data, stamps

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

    gesto_age = data[offset + 49]
    cfg = data[offset + 52]
    met = data[offset + 53]
    return {
        "dist_cm": data[offset + 48],
        "gesto_age": gesto_age,
        "gesto_y": None if gesto_age == EMPTY_SLOT else round(gesto_age * NOTE_SPEED),
        "gestos_apres": data[offset + 50],
        "gestos_acert": data[offset + 51],
        "modo_met": cfg & 0x03,
        "sensorial": bool(cfg & 0x04),
        "sem_som": bool(cfg & 0x08),
        "pagina": (cfg >> 4) & 1,
        "progresso": (cfg >> 5) & 0x07,
        "met_nivel": met & 0x03,
        "met_lado": (met >> 2) & 1,
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
        "nome": "".join(_name_char(data[offset + 45 + k] & 0x3F) for k in range(3)),
        "cursor": min(2, data[offset + 45] >> 6),
    }


# Nome do perfil em estilo arcade (nome_perfil.v): 3 caracteres, "---" = sem perfil
NAME_CHARS = "-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
NO_PROFILE = "---"


def _name_char(code):
    return NAME_CHARS[code] if code < len(NAME_CHARS) else "?"


def profile_of(name):
    """Pseudonimo da sessao: o nome de 3 caracteres, ou None para "---"."""
    return None if name == NO_PROFILE or "?" in name else name


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
        "gestos_apres": data[o + 50],
        "gestos_acert": data[o + 51],
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
        "gestos_apres": 0,
        "gestos_acert": 0,
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


DEFAULT_HISTORY = os.path.join(_ROOT_DIR, "sessoes", "sessoes.csv")
TOAST_S = 6.0  # s que o aviso "sessao salva" fica na tela inicial

SESSION_FIELDS = (
    ["data_hora", "pseudonimo", "musica", "song_id", "resultado", "score", "erros",
     "acertos", "notas", "taxa_pct", "assinc_media_ms", "assinc_desvio_ms",
     "assinc_abs_media_ms", "maior_combo"]
    + [f"notas_{k}" for k in TRACK_LABELS]
    + [f"acertos_{k}" for k in TRACK_LABELS]
    + ["soma_e", "soma_abs", "soma_e2"]
    + ["gestos_apresentados", "gestos_acertados", "modo_metronomo", "perfil_sensorial"]
)
MET_MODOS = ("desligado", "fixo", "adaptativo")


def session_row(m, pseudonym, song_id, won, score, misses, when=None,
                modo_met=0, sensorial=False):
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
        "gestos_apresentados": m.get("gestos_apres", 0),
        "gestos_acertados": m.get("gestos_acert", 0),
        "modo_metronomo": MET_MODOS[min(modo_met, 2)],
        "perfil_sensorial": int(bool(sensorial)),
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

    def _migrate(self):
        _migrate_csv(self.path)

    def append(self, row):
        """Grava a linha. Retorna (ok, mensagem)."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
            if not new:
                self._migrate()
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


def _migrate_csv(path):
    """CSV antigo (sem as colunas de gesto/config): reescreve com o cabecalho novo."""
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None or list(rd.fieldnames) == SESSION_FIELDS:
            return
        rows = list(rd)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SESSION_FIELDS, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


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

# ─── Chart: notas de gesto (bit 4 da ROM) ─────────────────────────────────────
# ESPACAMENTO_GESTO: no maximo 1 gesto a cada GESTO_MIN_GAP_FRAMES e GESTO_CLEAR_FRAMES
# sem nota de botao antes e depois de cada gesto. O gesto e uma entrada sozinha.
GESTO_BIT = 4
GESTO_MIN_GAP_FRAMES = 600  # 10 s entre gestos
GESTO_CLEAR_FRAMES = 60  # 1 s livre de notas de botao antes e depois
GESTO_FIRST_FRAME = 600  # primeiro gesto so depois de ~10 s
GESTO_COUNT = 4  # gestos por musica (alvo; menos se nao houver posicao valida)
GESTO_SEARCH_FRAMES = 200  # busca a posicao ideal de cada gesto em +-esta janela

# ─── Metronomo: andamento do MIDI (rtl/bpm_params.vh) ─────────────────────────
CLOCK_HZ = 50_000_000
FRAME_CYCLES = 833_333  # ciclos por quadro: M do contador frame_tick em fluxo_dados.v
BPM_VH = os.path.join(_RTL_DIR, "bpm_params.vh")


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


def _midi_tempo_info(midi_path):
    """
    Andamento do MIDI: (tempo_us, n_tempos) com o primeiro set_tempo (500000 = 120 BPM
    se nao houver). n_tempos > 1 significa mudanca de andamento (so o primeiro vale).
    O primeiro tempo e sempre o tick 0 (inicio do MIDI = inicio do audio).
    """
    import mido

    tempos = [
        (tk, m.tempo)
        for tk, m in _abs_events(mido.MidiFile(midi_path))
        if m.type == "set_tempo"
    ]
    if not tempos:
        return 500_000, 0
    return tempos[0][1], len(tempos)


def _midi_tempo_list(midi_path):
    """([(tick, tempo_us)], ticks_per_beat); lista vazia se o MIDI nao tem set_tempo."""
    import mido

    mid = mido.MidiFile(midi_path)
    return [(tk, m.tempo) for tk, m in _abs_events(mid) if m.type == "set_tempo"], mid.ticks_per_beat


def _abs_events(mid):
    """Eventos (tick absoluto, msg) de todas as trilhas, em ordem de tick."""
    ev = []
    for tr in mid.tracks:
        tk = 0
        for m in tr:
            tk += m.time
            ev.append((tk, m))
    return sorted(ev, key=lambda e: e[0])


def bpm_params(song_config):
    """
    Parametros do metronomo de uma musica, em ciclos de 50 MHz:
      periodo: duracao de um tempo = tempo_us * 50 (inteiro exato).
      fase: ciclos desde frame_counter = 0 (inicio do PLAY) ate o primeiro tempo
            CHEGAR a hit zone: tempo em t = 0 s do audio, que a nota correspondente
            atinge o alvo em t*60 + CHART_LEAD_FRAMES quadros (a nota e criada
            HIT_AGE quadros antes: ver _midi_to_chart). Quadro = FRAME_CYCLES ciclos.
    Retorna dict(periodo, fase, bpm, n_tempos, tempo_us).
    """
    tempos, tpb = _midi_tempo_list(song_config["midi"])
    tempo_us, n = _midi_tempo_info(song_config["midi"])
    periodo = tempo_us * (CLOCK_HZ // 1_000_000)
    # Segundo andamento: so o primeiro tempo que ja o usa (arredondado para o tempo mais
    # proximo; nao_alinhado avisa se o set_tempo nao cai exatamente num tempo).
    periodo2, ntroca, nao_alinhado = periodo, 0xFFFFFFFF, False
    if len(tempos) > 1:
        tk, t2 = tempos[1]
        periodo2 = t2 * (CLOCK_HZ // 1_000_000)
        ntroca = round(tk / tpb)
        nao_alinhado = tk % tpb != 0
    fase = CHART_LEAD_FRAMES * FRAME_CYCLES  # primeiro tempo em t = 0 s
    fase %= periodo
    return {
        "periodo": periodo,
        "fase": fase,
        "bpm": 60e6 / tempo_us,
        "n_tempos": n,
        "tempo_us": tempo_us,
        "periodo2": periodo2,
        "ntroca": ntroca,
        "nao_alinhado": nao_alinhado,
        "bpm2": 60e6 / (periodo2 / 50) if n > 1 else None,
    }


def write_bpm_params(songs=None, path=None):
    """Gera rtl/bpm_params.vh (PERIODO_n/FASE_n por slot de musica)."""
    songs = songs or SONGS
    lines = [
        "// Gerado por pc/gerar_hex.py (pc/interface.py: bpm_params). NAO EDITAR A MAO.",
        "//",
        "// Parametros do metronomo por musica (slot = indice em SONGS / song_id_reg:",
        "// 0 condado, 1 ocarina, 2 power_rangers, 3 pokemon). Tudo em ciclos de 50 MHz.",
        "//",
        "// PERIODO_n = duracao de um tempo = (microssegundos por tempo do primeiro",
        "//             set_tempo do MIDI) * 50, inteiro exato. Sem set_tempo: 120 BPM.",
        "//             Os tempos 0..NTROCA_n-1 usam PERIODO_n.",
        "// PERIODO2_n / NTROCA_n = segundo andamento do MIDI (2o set_tempo): PERIODO2_n e o",
        "//             tempo depois da troca e NTROCA_n o indice k do primeiro tempo que ja usa",
        "//             PERIODO2_n (round(tick_da_troca / ticks_por_tempo); AVISO se a troca nao",
        "//             cai exatamente num tempo). Um so andamento: PERIODO2_n = PERIODO_n e",
        "//             NTROCA_n = 32'hFFFFFFFF. Mais de 2 andamentos: so os 2 primeiros (AVISO).",
        "//             Tempo k chega em FASE + k*PERIODO (k < NTROCA) ou",
        "//             FASE + NTROCA*PERIODO + (k-NTROCA)*PERIODO2 (k >= NTROCA).",
        "// FASE_n    = ciclos desde frame_counter = 0 (inicio do PLAY) ate o primeiro",
        "//             tempo CHEGAR a hit zone. O primeiro tempo e o tick 0 do MIDI (t = 0",
        "//             s do audio); a nota correspondente seria criada HIT_AGE (130)",
        "//             quadros antes e atinge o alvo em t*60 + CHART_LEAD_FRAMES",
        f"//             quadros (= {CHART_LEAD_FRAMES} quadros aqui), igual ao deslocamento de",
        f"//             _midi_to_chart. Quadro = {FRAME_CYCLES} ciclos (M do frame_tick em",
        "//             fluxo_dados.v). FASE_n = CHART_LEAD_FRAMES * 833333, mod PERIODO_n.",
        "// O tempo k chega ao alvo em FASE_n + k * PERIODO_n ciclos apos o inicio do PLAY.",
        "//",
    ]
    for i, song in enumerate(songs):
        p = bpm_params(song)
        aviso = (
            (f"  troca para {p['bpm2']:.3f} BPM no tempo {p['ntroca']}"
             + ("  AVISO: troca fora do tempo, arredondada" if p["nao_alinhado"] else "")
             + (f"  AVISO: {p['n_tempos']} andamentos, usados so os 2 primeiros" if p["n_tempos"] > 2 else ""))
            if p["n_tempos"] > 1
            else ""
        )
        lines.append(f"// slot {i}: {song['title']}, {p['bpm']:.3f} BPM{aviso}")
    lines.append("")
    for i, song in enumerate(songs):
        p = bpm_params(song)
        lines.append(f"`define PERIODO_{i} 32'd{p['periodo']}")
        lines.append(f"`define FASE_{i}    32'd{p['fase']}")
        lines.append(f"`define PERIODO2_{i} 32'd{p['periodo2']}")
        lines.append(f"`define NTROCA_{i}  32'd{p['ntroca']}" if p["ntroca"] != 0xFFFFFFFF
                     else f"`define NTROCA_{i}  32'hFFFFFFFF")
    text = "\n".join(lines) + "\n"
    with open(path or BPM_VH, "w") as fh:
        fh.write(text)
    return text


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

    # A nota de t chega ao alvo no quadro t*60 + CHART_LEAD_FRAMES (ver a constante).
    # round() e nao int(): truncar atrasaria a media em -1/2 quadro. Em t*60 exatamente
    # em .5 o round do Python e half-even (pares): estavel e determinista, erro 1/2 quadro.
    for n in cands:
        n["frame"] = round(n["t"] * FPS) - int(HIT_AGE) + CHART_LEAD_FRAMES
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

    def select(forbidden):
        """Notas de botao por prioridade; `forbidden` = quadros que nao podem receber nota."""
        chosen, by_lane = [], [[] for _ in range(4)]
        # Notas monofonicas: _MAX_SIMULTANEOUS nao se aplica (o skyline ja tirou acordes)
        for n in sorted(cands, key=lambda n: (n["metric"], not n["fill"], n["dur"]), reverse=True):
            if len(chosen) >= target:
                break
            f = n["frame"]
            if f in forbidden or not fits(chosen, f, _GLOBAL_SPACING):
                continue
            # Trilha preferida primeiro, depois as vizinhas por distancia
            for lane in sorted(range(4), key=lambda l: (abs(l - n["lane"]), l)):
                if fits(by_lane[lane], f, _MIN_NOTE_SPACING) and slots_ok(by_lane[lane], f):
                    chosen.append(f)
                    by_lane[lane].append(f)
                    break
        return chosen, by_lane

    chosen, by_lane = select(set())

    # Notas de gesto: GESTO_COUNT posicoes espalhadas, nas cabecas de compasso/tempos
    # que atrapalham menos notas de botao. Depois, o chart de botoes e refeito sem
    # nada a +-GESTO_CLEAR_FRAMES de cada gesto (preenche com outras candidatas).
    gestos = []
    lo, hi = GESTO_FIRST_FRAME, total_frames - _LANE_SLOT_SPAN - 2
    if GESTO_COUNT > 0 and hi > lo:
        seg = (hi - lo) / GESTO_COUNT
        for k in range(GESTO_COUNT):
            alvo = lo + (k + 0.5) * seg
            lo_k = max(lo, gestos[-1] + GESTO_MIN_GAP_FRAMES) if gestos else lo
            pos = [
                n for n in cands
                if lo_k <= n["frame"] <= hi
                and abs(n["frame"] - alvo) <= GESTO_SEARCH_FRAMES
            ]
            if not pos:
                continue

            def custo(n):
                f = n["frame"]
                removidas = sum(abs(f - g) < GESTO_CLEAR_FRAMES for g in chosen)
                return (removidas, -n["metric"], abs(f - alvo))

            gestos.append(min(pos, key=custo)["frame"])
    if gestos:
        proibido = {
            f for g in gestos
            for f in range(g - GESTO_CLEAR_FRAMES + 1, g + GESTO_CLEAR_FRAMES)
        }
        chosen, by_lane = select(proibido)

    rom = [0] * total_frames
    for lane, frames in enumerate(by_lane):
        for f in frames:
            rom[f] |= 1 << lane
    for g in gestos:
        rom[g] = 1 << GESTO_BIT

    if save_hex:
        with open(song_config["hex"], "w") as fh:
            for v in rom:
                fh.write(f"{v:02X}\n")
        print(
            f"[chart] {song_config['hex']} gerado: {len(rom)} frames, {len(chosen)} notas "
            f"+ {len(gestos)} gestos {gestos} "
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
        # Nome do perfil: como o nome_perfil.v, so zera no reset ("---")
        self._nome = [0, 0, 0]
        self._cursor = 0
        # Pre-gera charts de todas as musicas disponíveis e grava os .hex no disco
        self._charts = [
            _midi_to_chart(s, save_hex=True) for s in SONGS if s["available"]
        ]
        self._chart = self._charts[self._selected_song]
        self._rom_depth = len(self._chart)
        # Nota de gesto: 5a "trilha" com 1 slot (bit 4 do chart)
        self._gage = self.EMPTY
        # Sensor HC-SR04 / gesto_detector: leitura a cada ~60 ms
        self._dist = 0
        self._ler_acc = 0.0
        self._leitura_ok = False  # houve leitura neste quadro
        self._cnt = 0  # leituras seguidas na faixa (SETTINGS)
        self._fora = 0  # leituras seguidas fora da faixa
        self._armado = True
        # cfg_jogo: so zeram no reset, como eq_config
        self._pagina = 0
        self._modo_met = 0
        self._sensorial = False
        self._sem_som = False
        # Metronomo: ciclos de PLAY acumulados (congela fora de PLAY)
        self._met_cycles = 0
        self._met_params = [self._met_slot(s) for s in SONGS if s["available"]]

    @staticmethod
    def _met_slot(song):
        """(periodo, fase, periodo2, ntroca) em ciclos de 50 MHz (como bpm_params.vh)."""
        try:
            p = bpm_params(song)
            return p["periodo"], p["fase"], p["periodo2"], p["ntroca"]
        except Exception as e:  # mido ausente ou MIDI sem tempo: 120 BPM
            print(f"[sim] metronomo: andamento padrao 120 BPM ({e})")
            per = 500_000 * (CLOCK_HZ // 1_000_000)
            return per, CHART_LEAD_FRAMES * FRAME_CYCLES % per, per, 0xFFFFFFFF

    def _met_beats(self):
        """Tempos que ja chegaram ao alvo: tempo k chega em FASE + k*PERIODO (...PERIODO2)."""
        per, fase, per2, ntroca = self._met_params[self._selected_song]
        c = self._met_cycles
        if c < fase:
            return 0
        if ntroca == 0xFFFFFFFF or c < fase + ntroca * per:
            return (c - fase) // per + 1
        return ntroca + (c - fase - ntroca * per) // per2 + 1

    # ── API publica ──────────────────────────────────────────────────────────

    def step(self, btn, mao=False):
        """
        Avanca um frame. btn = [track0, track1, track2, track3, start] (bool);
        mao = mao na faixa do sensor (tecla G). Retorna dict compativel com parse_packet().
        """
        self._sensor(mao)
        self._fsm(btn)
        if self.state == STATE_SETTINGS:
            self._gesto_confirma()
        else:
            self._cnt = 0

        if self.state == STATE_PLAY:
            self._advance_ages()
            self._check_escapes()
            self._spawn_notes()
            self._process_buttons(btn)
            self._process_gesto()
            self._met_cycles += FRAME_CYCLES
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

        met_on = self.state == STATE_PLAY and self._modo_met != 0
        m = self._met
        return {
            "dist_cm": self._dist,
            "gesto_age": self._gage,
            "gesto_y": None if self._gage == self.EMPTY else round(self._gage * NOTE_SPEED),
            "gestos_apres": m["gestos_apres"],
            "gestos_acert": m["gestos_acert"],
            "modo_met": self._modo_met,
            "sensorial": self._sensorial,
            "sem_som": self._sem_som,
            "pagina": self._pagina,
            "progresso": min(7, self._cnt // 2) if self.state == STATE_SETTINGS else 0,
            "met_nivel": 0 if met_on else 3,
            "met_lado": self._met_beats() & 1 if self._modo_met != 0 else 0,
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
            "nome": "".join(NAME_CHARS[c] for c in self._nome),
            "cursor": self._cursor,
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
            elif self._pagina == 0:
                for i in range(4):
                    if btn[i]:
                        self._eq[i] = (self._eq[i] + 1) % EQ_LEVELS
            else:  # pagina "Jogo": D metronomo, F sensorial, J sem som
                if btn[0]:
                    self._modo_met = 0 if self._modo_met else 1
                if btn[1]:
                    self._sensorial = not self._sensorial
                if btn[2]:
                    self._sem_som = not self._sem_som
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
            else:
                self._edit_name(btn)
        if self.state not in (STATE_WIN, STATE_LOSE):
            self._cursor = 0

    def _sensor(self, mao):
        """HC-SR04: uma leitura a cada ~60 ms; mao na faixa = ~15 cm, fora = ~60 cm."""
        self._leitura_ok = False
        self._ler_acc += 1
        if self._ler_acc >= LEITURA_FRAMES:
            self._ler_acc -= LEITURA_FRAMES
            self._dist = 15 if mao else 60
            self._leitura_ok = True

    def _na_faixa(self):
        return FAIXA_MIN_CM <= self._dist <= FAIXA_MAX_CM

    def _gesto_confirma(self):
        """gesto_detector.v: 16 leituras na faixa trocam a pagina; rearma apos 4 fora."""
        if not self._leitura_ok:
            return
        if self._na_faixa():
            self._fora = 0
            if self._armado:
                self._cnt += 1
                if self._cnt >= GESTO_CONFIRMA_LEITURAS:
                    self._cnt = 0
                    self._armado = False
                    self._pagina ^= 1
        else:
            self._cnt = 0
            self._fora += 1
            if self._fora >= GESTO_REARMA_LEITURAS:
                self._armado = True

    def _process_gesto(self):
        """Nota de gesto: mesma fisica das trilhas; a mao na faixa na janela e acerto."""
        if (self._leitura_ok and self._na_faixa() and self._gage != self.EMPTY
                and abs(self._gage - self.HIT_AGE) <= self.HIT_WIN):
            self._gage = self.EMPTY
            self.score += 100 + self.combo
            self.combo += 1
            m = self._met
            m["gestos_acert"] += 1
            m["gestos_apres"] += 1
            m["max_combo"] = max(m["max_combo"], min(self.combo, 255))

    def _edit_name(self, btn):
        """D/F trocam o caractere no cursor; J/K movem o cursor (nome_perfil.v)."""
        n = len(NAME_CHARS)
        c = self._cursor
        if btn[0]:
            self._nome[c] = (self._nome[c] - 1) % n
        elif btn[1]:
            self._nome[c] = (self._nome[c] + 1) % n
        if btn[2]:
            self._cursor = max(0, c - 1)
        elif btn[3]:
            self._cursor = min(2, c + 1)

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
        self._gage = self.EMPTY
        self._met_cycles = 0
        self.score = 0
        self.misses = 0
        self.combo = 0

    # ── Logica de notas ──────────────────────────────────────────────────────

    def _advance_ages(self):
        if self._gage != self.EMPTY:
            self._gage += 1
        for track in self._ages:
            for i in range(self.SLOTS):
                if track[i] != self.EMPTY:
                    track[i] += 1

    def _check_escapes(self):
        if self._gage != self.EMPTY and self._gage > self.MISS_AGE:
            self._gage = self.EMPTY
            self.misses += 1
            self.combo = 0
            self._met["gestos_apres"] += 1  # apresentados = julgados (acerto + escape), como o RTL
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
            if mask & (1 << GESTO_BIT) and self._gage == self.EMPTY:
                self._gage = 0
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
        baudrate=921_600,
        sim_mode=False,
        scale=None,
        audio_offset_ms=0,
        history_path=DEFAULT_HISTORY,
    ):
        self._sim_mode = sim_mode
        # Sessao: so o nome de 3 caracteres identifica a crianca (PRIVACIDADE)
        self._log = SessionLog(history_path)
        self._session_live = False  # viu PLAY desde o ultimo fim de musica
        self._dash = None  # dados do dashboard, montados ao entrar no fim
        self._toast = None  # (texto, ok, instante): aviso de gravacao na tela inicial
        # Latencia de saida alem do buffer do mixer (fone Bluetooth, placa de
        # som externa). Positivo: a musica comeca adiantada esse tanto para
        # compensar; negativo: a musica comeca atrasada.
        self._audio_offset = audio_offset_ms / 1000.0
        self._baudrate = baudrate
        # perf_counter do quadro 0 do PLAY (nota do chart[0] nasce; frame_counter = 1).
        # None fora do PLAY. Serial: estimativa, o MENOR valor visto (atrasos so
        # aumentam); sim: relogio ideal da simulacao.
        self._play_anchor = None
        self._play_info = None  # ultimo play: pos pedido, ancora, instante (diagnostico)
        self._music_prep_s = 0.015  # custo medido de cortar+carregar o WAV
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
        self.fpga_nome = NO_PROFILE  # nome do perfil (nome_perfil.v)
        self.fpga_cursor = 0
        # Sensor, gesto, configuracao de jogo e metronomo (bytes 48-53 do pacote)
        self.fpga_dist = 0
        self.fpga_gesto_y = None  # Y da nota de gesto (None = nenhuma)
        self.fpga_pagina = 0
        self.fpga_progresso = 0
        self.fpga_modo_met = 0
        self.fpga_sensorial = False
        self.fpga_sem_som = False
        self.fpga_met_nivel = 3
        self.fpga_met_lado = 0
        self._prev_gestos = (0, 0)  # (apresentados, acertados) do pacote anterior
        self._prev_gesto_y = None
        self._met_disp = 0.0  # angulo mostrado no icone do metronomo (graus)
        self._gesto_fx = []  # efeitos de acerto/erro da nota de gesto

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
        self._rx_base = 0  # posicao absoluta (bytes recebidos) de _serial_buf[0]
        self._rx_stamps = []  # [(fim_abs, t_chegada)] ainda nao consumidos
        if sim_mode:
            self.sim = SimulationEngine()
            self.serial = None
            self._sim_t0 = None  # perf_counter do 1o passo da simulacao
            self._sim_steps = 0
            self._sim_btn = [False] * 5
            self._sim_mao = False  # tecla G segurada = mao na faixa
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
        self._build_gesto_surfs()
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
        # Sempre em memoria (inclusive sem atenuador): _play_music corta o WAV nas amostras
        data = self._audio_cache.get(key)
        if data is None:
            try:
                if preview or any(self._eq):
                    data = _eq_wav_bytes(path, self._eq, EQ_PREVIEW_S if preview else None)
                else:
                    with open(path, "rb") as f:
                        data = f.read()
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
            pygame.mixer.music.set_volume(self._music_volume())
            self._music_ok = True
            print(f"[music] Carregado: {path} eq={self._eq}{' (previa)' if preview else ''}")
        except Exception as e:
            print(f"[music] Falha ao carregar musica: {e}")

    def _music_volume(self):
        if self.fpga_sem_som:
            return 0.0
        return MUSIC_VOLUME * (0.5 if self.fpga_sensorial else 1.0)

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
        # Sem som: so volume 0 (a musica segue tocando, o sync nao muda).
        # Perfil sensorial: 50% do volume normal.
        pygame.mixer.music.set_volume(self._music_volume())
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
                    self._play_music()
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

    def _play_music(self):
        """
        Inicia a musica do zero, ancorada no quadro 0 do PLAY. Roda a cada quadro ate
        a musica comecar (ainda sem ancora, ou pos < 0: espera). O som do instante t da
        musica deve sair em ancora + t + CHART_LEAD_FRAMES/FPS; play() so o faz sair
        AUDIO_BUFFER_S depois, e --audio-offset adianta mais. Logo, chamando play() no
        instante T com a musica comecando em pos:
            pos(T) = (T - ancora) - CHART_LEAD_FRAMES/FPS + AUDIO_BUFFER_S + audio_offset.
        """
        anchor = self._play_anchor
        if anchor is None:
            return
        lead = CHART_LEAD_FRAMES / FPS - AUDIO_BUFFER_S - self._audio_offset
        t_zero = anchor + lead  # instante em que play() com pos = 0 e o certo
        for _ in range(3):
            now = time.perf_counter()
            if t_zero - now > self._music_prep_s + 0.002:
                return  # ainda cedo: agenda para um quadro futuro
            # WAV so aceita start em segundos inteiros no SDL_mixer: o corte e feito
            # nas amostras e o stream carregado ja comeca em pos.
            t_call = max(now + self._music_prep_s, t_zero)
            pos = max(0.0, t_call - t_zero)
            if not self._load_from(pos):
                self._music_started = True  # sem audio: nao tenta de novo
                return
            wait = t_call - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            elif wait < -0.002 and pos > 0:
                # atrasou: o corte ja nao vale, refaz com o custo real
                self._music_prep_s = min(0.1, -wait + self._music_prep_s + 0.003)
                continue
            self._music_prep_s = max(0.003, min(0.1, (time.perf_counter() - now) * 1.3))
            break
        t_play = time.perf_counter()
        self._play_info = {"pos": pos, "anchor": anchor, "t": t_play, "t_zero": t_zero}
        pygame.mixer.music.play()
        self._music_started = True

    def _load_from(self, pos):
        """Carrega no mixer a musica atual a partir de pos segundos. False se falhar."""
        data = self._audio_cache.get((self._selected_song, tuple(self._eq), False))
        if data is None:
            try:
                with open(SONGS[self._selected_song]["audio"], "rb") as f:
                    data = f.read()
            except OSError:
                return False
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                rate = w.getframerate()
                w.setpos(min(w.getnframes(), int(round(pos * rate))))
                pcm = w.readframes(w.getnframes())
                ch, width = w.getnchannels(), w.getsampwidth()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(width)
                w.setframerate(rate)
                w.writeframes(pcm)
            self._music_buf = io.BytesIO(buf.getvalue())
            pygame.mixer.music.load(self._music_buf, "wav")
            pygame.mixer.music.set_volume(self._music_volume())
        except Exception as e:
            print(f"[music] Falha ao cortar/carregar musica: {e}")
            return False
        # o stream do mixer agora e o trecho cortado: recarrega a musica inteira
        # na proxima vez que sair do PLAY
        self._loaded_audio = None
        return True

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
        if self.fpga_sensorial:  # perfil sensorial: sem animacao de fundo
            return
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
        new_data, stamps = self.serial.read_stamped()
        if new_data:
            self._serial_buf.extend(new_data)
            self._rx_stamps.extend(stamps)

        while len(self._serial_buf) >= PACKET_SIZE:
            pkt, consumed = find_packet(self._serial_buf)
            self._serial_buf = self._serial_buf[consumed:]
            self._rx_base += consumed
            if pkt is None:
                break
            self.serial.request_ack()
            # Chegada do ultimo byte do pacote (primeiro carimbo que o cobre) menos o
            # tempo de transmissao do pacote (10 bits por byte na UART 8N1).
            t_pkt = None
            for end_abs, t in self._rx_stamps:
                if end_abs >= self._rx_base:
                    t_pkt = t - PACKET_SIZE * 10 / self._baudrate
                    break
            self._rx_stamps = [x for x in self._rx_stamps if x[0] >= self._rx_base]
            self._apply_pkt(pkt, t_pkt)

    def _apply_pkt(self, pkt, t_pkt=None):
        """
        Copia um pacote (FPGA ou simulacao) para o estado da interface.
        t_pkt: instante (perf_counter) em que o estado do pacote foi produzido (serial:
        chegada - transmissao; sim: instante ideal do passo). Alimenta _play_anchor.
        """
        if pkt["state"] not in (STATE_PLAY, STATE_PAUSE):
            self._play_anchor = None
        elif pkt["state"] == STATE_PLAY and t_pkt is not None and pkt["frame_counter"] >= 1:
            # Com frame_counter = k o pacote vem k-1 quadros depois do quadro 0 do PLAY
            # (a nota do chart[0] nasce no passo que leva o contador a 1).
            est = t_pkt - (pkt["frame_counter"] - 1) / FPS
            if self._play_anchor is None or est < self._play_anchor:
                self._play_anchor = est
        self._prev_notes = [list(t) for t in self.fpga_notes]
        self._prev_score = self.fpga_score
        self._prev_combo = self.fpga_combo
        self._prev_gestos = (self.fpga_metrics["gestos_apres"], self.fpga_metrics["gestos_acert"])
        self._prev_gesto_y = self.fpga_gesto_y
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
        self.fpga_metrics = {
            k: (list(v) if isinstance(v, list) else v) for k, v in pkt["metrics"].items()
        }
        self.fpga_nome = pkt["nome"]
        self.fpga_cursor = pkt["cursor"]
        self.fpga_dist = pkt["dist_cm"]
        self.fpga_gesto_y = pkt["gesto_y"]
        self.fpga_pagina = pkt["pagina"]
        self.fpga_progresso = pkt["progresso"]
        self.fpga_modo_met = pkt["modo_met"]
        self.fpga_sensorial = pkt["sensorial"]
        self.fpga_sem_som = pkt["sem_som"]
        self.fpga_met_nivel = pkt["met_nivel"]
        self.fpga_met_lado = pkt["met_lado"]
        self._detect_note_events()
        self._detect_gesto_events()

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
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_g:
                self._sim_mao = True  # G segurada = mao na faixa do sensor
            elif ev.type == pygame.KEYUP and ev.key == pygame.K_g:
                self._sim_mao = False
            elif ev.type == pygame.KEYDOWN and ev.key in key_map:
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

        # As teclas valem no ultimo passo, que corresponde ao instante atual
        for _ in range(due - 1):
            self.sim.step([False] * 5, self._sim_mao)
        pkt = self.sim.step(self._sim_btn, self._sim_mao)
        self._sim_btn = [False] * 5
        self._sim_steps += due
        # O passo de numero n (contado de 1) tem instante ideal t0 + n/FPS
        self._apply_pkt(pkt, self._sim_t0 + self._sim_steps / FPS)

    # ── Sessao: fecha ao fim da musica ────────────────────────────────────────

    def _update_session(self, state):
        """
        Ao entrar no fim de musica monta o dashboard; enquanto o nome do
        perfil muda, atualiza a comparacao; ao sair (START), grava a sessao
        com o nome escolhido.
        """
        if state == STATE_PLAY:
            self._session_live = True
        if state in (STATE_WIN, STATE_LOSE):
            if self._dash is None:
                self._dash = self._open_dash(won=(state == STATE_WIN))
            self._refresh_profile()
        elif self._dash is not None:
            self._finish_session()
            self._dash = None

    def _open_dash(self, won):
        m = self.fpga_metrics
        dash = {
            "won": won,
            "metrics": {k: (list(v) if isinstance(v, list) else v) for k, v in m.items()},
            "summary": session_summary(m),
            "song": self._selected_song,
            "score": self.fpga_score,
            "misses": self.fpga_misses,
            # Sessao inteira vista? (a interface pode abrir com a musica no fim)
            "live": self._session_live,
            "modo_met": self.fpga_modo_met,
            "sensorial": self.fpga_sensorial,
            "name": None,
        }
        self._session_live = False
        return dash

    def _refresh_profile(self):
        """Releitura do historico so quando o nome muda."""
        dash = self._dash
        if dash["name"] == self.fpga_nome:
            return
        dash["name"] = self.fpga_nome
        dash["profile"] = profile_of(self.fpga_nome)
        dash["prev"] = self._log.previous(dash["profile"])
        dash["deltas"] = session_deltas(dash["summary"], dash["prev"])

    def _finish_session(self):
        dash = self._dash
        now = time.monotonic()
        if not dash["live"]:
            self._toast = ("Sessao nao gravada: a musica terminou antes de abrir a interface",
                           False, now)
            return
        row = session_row(dash["metrics"], dash["profile"], dash["song"], dash["won"],
                          dash["score"], dash["misses"],
                          modo_met=dash["modo_met"], sensorial=dash["sensorial"])
        ok, msg = self._log.append(row)
        who = dash["profile"] or "sem perfil"
        self._toast = (f"{msg}  ({who})", ok, now)

    # ── Spawn de efeitos ──────────────────────────────────────────────────────

    def _spawn_hit_effect(self, lane, x, y):
        """Cria burst de acerto: 3 aneis expansivos + 7 particulas + flash."""
        if self.fpga_sensorial:
            # Retorno minimo (LATENCIA_FEEDBACK): um anel fino que some em ~6 quadros
            self._hit_effects.append(
                {
                    "lane": lane, "x": x, "y": y, "color": TRACK_COLORS[lane], "age": 0,
                    "rings": [{"r": NOTE_RADIUS + 4, "alpha": 170, "delay": 0}],
                    "particles": [], "flash_alpha": 0, "active": True,
                    "grow": 1, "fade": 30, "width": 2,
                }
            )
            return
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
                    ring["r"] += eff.get("grow", 6)
                    ring["alpha"] = max(0, ring["alpha"] - eff.get("fade", 12))
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
        if self.fpga_sensorial:
            pulse = 0.5  # fixa
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

    # ── Nota de gesto (AVISO_GESTO) ───────────────────────────────────────────
    # 5a "trilha": barra hexagonal magenta cobrindo as 4 trilhas, com uma mao
    # desenhada. Forma e cor nao existem em nenhuma nota de trilha.

    @staticmethod
    def _gesto_geom():
        x0 = TRACK_X[0] - TRACK_W // 2
        x1 = TRACK_X[3] + TRACK_W // 2
        return x0, x1, (x0 + x1) // 2

    @staticmethod
    def _draw_hand_icon(surf, cx, cy, h, fill, outline):
        """Mao aberta (palma + 4 dedos + polegar) com primitivas; h = altura total."""
        u = h / 10.0
        parts = []
        palm = pygame.Rect(0, 0, int(5.6 * u), int(4.4 * u))
        palm.center = (cx, int(cy + 2.2 * u))
        parts.append(("r", palm, int(1.2 * u)))
        for k in range(4):
            fh = (4.6, 5.4, 5.0, 4.0)[k] * u
            fr = pygame.Rect(0, 0, max(2, int(1.15 * u)), int(fh))
            fr.midbottom = (int(cx - 2.1 * u + k * 1.4 * u), int(cy + 0.6 * u))
            parts.append(("r", fr, max(1, int(0.55 * u))))
        thumb = pygame.Rect(0, 0, max(2, int(1.2 * u)), int(3.2 * u))
        thumb.center = (int(cx - 3.3 * u), int(cy + 1.6 * u))
        parts.append(("t", thumb, max(1, int(0.55 * u))))
        for kind, r, rad in parts:  # contorno e depois miolo
            grow = r.inflate(max(2, int(0.5 * u)), max(2, int(0.5 * u)))
            if kind == "t":
                pygame.draw.polygon(surf, outline, [
                    (grow.right, grow.top), (grow.left - int(0.6 * u), grow.centery),
                    (grow.left, grow.bottom), (grow.right, grow.bottom)])
            else:
                pygame.draw.rect(surf, outline, grow, border_radius=rad + 1)
        for kind, r, rad in parts:
            if kind == "t":
                pygame.draw.polygon(surf, fill, [
                    (r.right, r.top), (r.left - int(0.4 * u), r.centery),
                    (r.left, r.bottom), (r.right, r.bottom)])
            else:
                pygame.draw.rect(surf, fill, r, border_radius=rad)

    def _build_gesto_surfs(self):
        x0, x1, _ = self._gesto_geom()
        w = x1 - x0 + 2 * _px(12)
        h = NOTE_RADIUS * 2 + _ps(16)
        self.gesto_size = (w, h)
        pad = _px(12)
        bevel = h // 2 + _px(6)

        def hexa(inset=0):
            return [
                (pad + inset, h // 2), (pad + bevel, inset), (w - pad - bevel, inset),
                (w - pad - inset, h // 2), (w - pad - bevel, h - 1 - inset),
                (pad + bevel, h - 1 - inset),
            ]

        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(s, (*GESTO_COR, 45), [(max(0, px - (6 if px < w // 2 else -6)), py)
                                                  for px, py in hexa()], 0)
        pygame.draw.polygon(s, (*GESTO_COR, 150), hexa(_ps(3)))
        pygame.draw.polygon(s, GESTO_COR, hexa(_ps(6)))
        pygame.draw.polygon(s, (255, 255, 255), hexa(_ps(6)), 2)
        # setas laterais ( > < ) e a mao no centro
        ay = h // 2
        for sx, d in ((pad + bevel + _px(14), 1), (w - pad - bevel - _px(14), -1)):
            for off in (0, _px(10)):
                x = sx + d * off
                pygame.draw.lines(s, (255, 255, 255), False,
                                  [(x - d * _px(4), ay - _ps(9)), (x + d * _px(4), ay),
                                   (x - d * _px(4), ay + _ps(9))], 3)
        self._draw_hand_icon(s, w // 2, h // 2, int(h * 0.72),
                             (255, 255, 255), (70, 10, 90))
        self.gesto_surf = s
        # Alvo na hit zone: so o contorno
        t = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(t, (*GESTO_COR, 200), hexa(_ps(6)), 3)
        self.gesto_target = t
        # Versao "acesa" do alvo (mao na faixa dentro da janela)
        o = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(o, (*GESTO_COR, 110), hexa(_ps(6)))
        pygame.draw.polygon(o, (255, 255, 255, 230), hexa(_ps(6)), 3)
        self.gesto_target_on = o

    def _draw_gesto_note(self):
        """Nota de gesto descendo ate a hit zone; o alvo (contorno) aparece junto."""
        y = self.fpga_gesto_y
        if y is None or self.fpga_state not in (STATE_PLAY, STATE_PAUSE):
            return
        w, h = self.gesto_size
        _, _, cx = self._gesto_geom()
        in_win = abs(y - HIT_Y) <= HIT_WIN * NOTE_SPEED
        mao = FAIXA_MIN_CM <= self.fpga_dist <= FAIXA_MAX_CM
        self.screen.blit(self.gesto_target_on if (in_win and mao) else self.gesto_target,
                         (cx - w // 2, HIT_Y - h // 2))
        if 0 <= y <= SCREEN_H + h:
            self.screen.blit(self.gesto_surf, (cx - w // 2, y - h // 2))

    def _detect_gesto_events(self):
        """Gesto acertado (contador subiu) ou perdido (nota escapou): cria o efeito."""
        if self.fpga_metrics["gestos_acert"] > self._prev_gestos[1]:
            self._gesto_fx.append({"kind": "hit", "age": 0})
        elif (self.fpga_state == STATE_PLAY and self.fpga_gesto_y is None
              and self._prev_gesto_y is not None
              and self._prev_gesto_y >= (MISS_AGE - 2) * NOTE_SPEED):
            self._gesto_fx.append({"kind": "miss", "age": 0})

    def _draw_gesto_effects(self):
        """Acerto: barra se expande e some; erro: contorno vermelho que some."""
        if not self._gesto_fx:
            return
        w, h = self.gesto_size
        _, _, cx = self._gesto_geom()
        calm = self.fpga_sensorial
        life = 8 if calm else 30
        self.scratch.fill((0, 0, 0, 0))
        for fx in self._gesto_fx:
            k = fx["age"] / life
            alpha = int(255 * (1 - k) ** 2)
            if fx["kind"] == "hit":
                grow = 0 if calm else int(k * _px(40))
                r = pygame.Rect(0, 0, w + 2 * grow, h + grow)
                r.center = (cx, HIT_Y)
                if not calm:
                    pygame.draw.rect(self.scratch, (*GESTO_COR, alpha // 3), r,
                                     border_radius=h // 2)
                pygame.draw.rect(self.scratch, (*GESTO_COR, alpha), r,
                                 2 if calm else 4, border_radius=h // 2)
            else:
                r = pygame.Rect(0, 0, w, h)
                r.center = (cx, HIT_Y)
                pygame.draw.rect(self.scratch, (255, 70, 70, alpha), r,
                                 2 if calm else 4, border_radius=h // 2)
                if not calm:
                    d = h // 3
                    for sg in (1, -1):
                        pygame.draw.line(self.scratch, (255, 70, 70, alpha),
                                         (cx - d, HIT_Y - sg * d), (cx + d, HIT_Y + sg * d), 5)
            fx["age"] += 1
        self.screen.blit(self.scratch, (0, 0))
        self._gesto_fx = [f for f in self._gesto_fx if f["age"] < life]

    # ── Metronomo no HUD ──────────────────────────────────────────────────────

    def _draw_met_hud(self):
        """Icone de metronomo: braco que alterna de lado a cada tempo (so se ligado)."""
        if self.fpga_modo_met == 0:
            return
        cx, base_y = SCREEN_W - _px(46), _py(150)
        calm = self.fpga_sensorial
        target = 0.0
        if not calm and self.fpga_met_nivel != 3:
            ang = MET_ANGULOS[self.fpga_met_nivel] / 2
            target = ang if self.fpga_met_lado else -ang
        self._met_disp = target if calm else self._met_disp + (target - self._met_disp) * 0.4
        col = (110, 150, 205) if calm else (255, 190, 80)
        bw, bh = _ps(16), _ps(40)
        body = [(cx - bw, base_y), (cx + bw, base_y), (cx + bw // 2, base_y - bh),
                (cx - bw // 2, base_y - bh)]
        pygame.draw.polygon(self.screen, (24, 36, 72), body)
        pygame.draw.polygon(self.screen, col, body, 2)
        rad = math.radians(self._met_disp)
        arm = bh - _ps(4)
        tip = (cx + math.sin(rad) * arm, base_y - _ps(2) - math.cos(rad) * arm)
        pygame.draw.line(self.screen, col, (cx, base_y - _ps(2)), tip, 3)
        pygame.draw.circle(self.screen, col, (int(tip[0]), int(tip[1])), _ps(4))
        lab = self.font(16).render("TEMPO", True, (90, 125, 185))
        self.screen.blit(lab, lab.get_rect(center=(cx, base_y + _ps(10))))

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
                        self.scratch, (*color, ring["alpha"]), (x, y), ring["r"],
                        eff.get("width", 3),
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
            if self.fpga_sensorial:
                ticks = 0  # sem tremor do combo
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
        float_y = 0 if self.fpga_sensorial else 6 * math.sin(ticks * 0.002)

        logo_rect = self._logo.get_rect(center=(SCREEN_W // 2, _py(190) + int(float_y)))
        self.screen.blit(self._logo, logo_rect)

        if ticks % 1000 < 650 or self.fpga_sensorial:
            sub = self.font(50).render("Aperte START para jogar", True, (255, 130, 0))
            self.screen.blit(sub, sub.get_rect(center=(SCREEN_W // 2, _py(360))))

        # Fixo (sem piscar): entrada das configuracoes de som
        cfg = self.font(36).render(
            "Aperte D / F / J / K para configurar som e jogo", True, (140, 170, 220)
        )
        self.screen.blit(cfg, cfg.get_rect(center=(SCREEN_W // 2, _py(400))))

        if self._sim_mode:
            hint_text = "[SIM]  SPACE = START   |   D / F / J / K = configuracoes"
        else:
            hint_text = ""
        hint = self.font(30).render(hint_text, True, (60, 95, 155))
        self.screen.blit(hint, hint.get_rect(center=(SCREEN_W // 2, _py(540))))

        if self._toast and time.monotonic() - self._toast[2] < TOAST_S:
            text, ok, _ = self._toast
            color = (110, 220, 150) if ok else (240, 170, 90)
            msg = self.font(30).render(text, True, color)
            self.screen.blit(msg, msg.get_rect(center=(SCREEN_W // 2, _py(470))))

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
                        n = bin(v & 0x0F).count("1")  # so notas de botao (bit 4 = gesto)
                        preview["bins"][min(bins_n - 1, int(f / per_bin))] += n
                        preview["notes"] += n
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
            pulse = 0.5 if self.fpga_sensorial else 0.5 + 0.5 * math.sin(now * 0.004 + i * 0.9)
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
        if now % 1400 < 1000 or self._sim_mode or self.fpga_sensorial:
            hint = self.font(28).render(hint_text, True, (90, 135, 200))
            self.screen.blit(hint, hint.get_rect(center=(cx, box_top + box_h + _py(30))))

    def _draw_settings(self):
        """
        Tela de configuracoes em duas abas ("Som" e "Jogo"), trocadas pelo gesto de
        confirmacao (mao sobre o sensor ~1 s). Estatica de proposito (sem piscar nem
        pulsar): o publico-alvo inclui jogadores sensiveis a estimulos.
        """
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self.screen.blit(self.scanline_surf, (0, 0))
        cx0 = SCREEN_W // 2
        page = self.fpga_pagina

        self.screen.blit(self._logo_select, self._logo_select.get_rect(center=(cx0, _py(36))))
        title = self.font(46).render("CONFIGURACOES", True, (180, 220, 255))
        self.screen.blit(title, title.get_rect(center=(cx0, _py(80))))

        # Abas
        TAB_W, TAB_H = _px(120), _py(30)
        for k, name in enumerate(("Som", "Jogo")):
            r = pygame.Rect(0, 0, TAB_W, TAB_H)
            r.center = (cx0 + (k - 0.5) * (TAB_W + _px(12)), _py(116))
            on = k == page
            pygame.draw.rect(self.screen, (28, 48, 100) if on else (14, 22, 48), r, border_radius=6)
            pygame.draw.rect(self.screen, (255, 130, 0) if on else LINE_COLOR, r,
                             3 if on else 1, border_radius=6)
            t = self.font(30).render(name, True, (235, 240, 255) if on else (100, 125, 175))
            self.screen.blit(t, t.get_rect(center=r.center))

        BOX_W, BOX_H = _px(118), _py(250)
        BOX_TOP = _py(142)
        gap = _px(25)
        x_start = (SCREEN_W - (4 * BOX_W + 3 * gap)) // 2
        if page == 0:
            self._draw_settings_som(x_start, BOX_W, BOX_H, BOX_TOP, gap)
            legend = "Cada botao muda uma faixa:  " + " > ".join(
                "0 dB" if k == 0 else f"-{EQ_STEP_DB * k}" for k in range(EQ_LEVELS)
            ) + "  > 0 dB"
        else:
            self._draw_settings_jogo(x_start, BOX_W, BOX_H, BOX_TOP, gap)
            legend = "D = metronomo   |   F = perfil sensorial   |   J = sem som"
        info_y = BOX_TOP + BOX_H + _py(16)
        info = self.font(28).render(legend, True, (160, 190, 235))
        self.screen.blit(info, info.get_rect(center=(cx0, info_y)))

        # Gesto de confirmacao: barra de progresso (0-7) e distancia atual
        pw, ph = 4 * BOX_W + 3 * gap, _py(86)
        panel = pygame.Rect(x_start, info_y + _py(16), pw, ph)
        pygame.draw.rect(self.screen, (10, 18, 42), panel, border_radius=8)
        pygame.draw.rect(self.screen, (40, 75, 140), panel, 2, border_radius=8)
        msg = self.font(24).render(
            "Segure a mao sobre o sensor para trocar de aba", True, (200, 215, 245))
        self.screen.blit(msg, msg.get_rect(midleft=(panel.x + _px(14), panel.y + _py(18))))
        SEGS = 8
        seg_gap = _px(5)
        bar_w = int(pw * 0.58)
        seg_w = (bar_w - (SEGS - 1) * seg_gap) // SEGS
        bx, by = panel.x + _px(14), panel.y + _py(40)
        for k in range(SEGS):
            r = pygame.Rect(bx + k * (seg_w + seg_gap), by, seg_w, _py(26))
            if k < self.fpga_progresso:
                pygame.draw.rect(self.screen, GESTO_COR, r, border_radius=3)
            else:
                pygame.draw.rect(self.screen, (50, 60, 90), r, 1, border_radius=3)
        d = self.fpga_dist
        na_faixa = FAIXA_MIN_CM <= d <= FAIXA_MAX_CM
        # Indicador de distancia: regua 0-40 cm com a faixa valida marcada
        rx0, rx1 = bx + bar_w + _px(30), panel.right - _px(14)
        ry = by + _py(13)
        pygame.draw.line(self.screen, (60, 80, 130), (rx0, ry), (rx1, ry), 2)
        fx0 = rx0 + (rx1 - rx0) * FAIXA_MIN_CM // 40
        fx1 = rx0 + (rx1 - rx0) * FAIXA_MAX_CM // 40
        pygame.draw.rect(self.screen, (*GESTO_COR,), (fx0, ry - _py(6), fx1 - fx0, _py(12)), 2)
        if d > 0:
            mx = rx0 + (rx1 - rx0) * min(d, 40) // 40
            pygame.draw.circle(self.screen, (110, 220, 150) if na_faixa else (240, 170, 90),
                               (mx, ry), _ps(6))
        dist_txt = f"{d} cm" if d else "sem leitura"
        dcol = (110, 220, 150) if na_faixa else (140, 170, 220)
        dt = self.font(28).render(f"Distancia: {dist_txt}", True, dcol)
        self.screen.blit(dt, dt.get_rect(midleft=(rx0, panel.y + _py(18))))
        rng = self.font(20).render(f"faixa valida {FAIXA_MIN_CM} a {FAIXA_MAX_CM} cm", True, (90, 115, 160))
        self.screen.blit(rng, rng.get_rect(midleft=(rx0, ry + _py(22))))

        back = self.font(30).render("START = salvar e voltar", True, (255, 130, 0))
        self.screen.blit(back, back.get_rect(center=(cx0, panel.bottom + _py(18))))
        if self._sim_mode:
            hint = self.font(24).render(
                "[SIM]  D / F / J / K = opcoes   |   G (segurar) = mao no sensor   |   SPACE = voltar",
                True, (55, 90, 150))
            self.screen.blit(hint, hint.get_rect(center=(cx0, panel.bottom + _py(40))))

    def _settings_box(self, i, x, w, h, top, color):
        box = pygame.Rect(x - w // 2, top, w, h)
        bg = pygame.Surface((w, h), pygame.SRCALPHA)
        bg.fill((*color, 40))
        self.screen.blit(bg, box.topleft)
        pygame.draw.rect(self.screen, color, box, 2, border_radius=5)
        key_s = self.font(40).render(TRACK_LABELS[i], True, color)
        self.screen.blit(key_s, key_s.get_rect(center=(x, top + _py(24))))
        pygame.draw.line(self.screen, color, (x - w // 2 + _px(10), top + _py(43)),
                         (x + w // 2 - _px(10), top + _py(43)), 1)

    def _draw_settings_som(self, x_start, BOX_W, BOX_H, BOX_TOP, gap):
        SEG_W, SEG_H, SEG_GAP = _px(60), _py(20), _py(6)
        for i, band in enumerate(EQ_BANDS):
            cx = x_start + BOX_W // 2 + i * (BOX_W + gap)
            color = TRACK_COLORS[i]
            level = self._eq[i]
            self._settings_box(i, cx, BOX_W, BOX_H, BOX_TOP, color)
            name_s = self.font(26).render(band["name"], True, (235, 240, 255))
            self.screen.blit(name_s, name_s.get_rect(center=(cx, BOX_TOP + _py(60))))
            range_s = self.font(16).render(band["range"], True, (130, 175, 225))
            self.screen.blit(range_s, range_s.get_rect(center=(cx, BOX_TOP + _py(79))))
            # Medidor: segmentos acesos = quanto da faixa ainda soa
            lit = EQ_LEVELS - level
            seg_bottom = BOX_TOP + _py(200)
            for k in range(EQ_LEVELS):
                y = seg_bottom - (k + 1) * SEG_H - k * SEG_GAP
                seg = pygame.Rect(cx - SEG_W // 2, y, SEG_W, SEG_H)
                if k < lit:
                    pygame.draw.rect(self.screen, color, seg, border_radius=3)
                else:
                    pygame.draw.rect(self.screen, (50, 60, 90), seg, 1, border_radius=3)
            db_text = "0 dB" if level == 0 else f"-{EQ_STEP_DB * level} dB"
            db_s = self.font(30).render(db_text, True, (235, 240, 255))
            self.screen.blit(db_s, db_s.get_rect(center=(cx, BOX_TOP + _py(226))))

    def _draw_settings_jogo(self, x_start, BOX_W, BOX_H, BOX_TOP, gap):
        met = ("Desligado", "Fixo")[1 if self.fpga_modo_met else 0]
        itens = [
            ("Metronomo", ("Desligado", "Fixo"), met),
            ("Perfil sensorial", ("Lig", "Desl"), "Lig" if self.fpga_sensorial else "Desl"),
            ("Sem som", ("Lig", "Desl"), "Lig" if self.fpga_sem_som else "Desl"),
            ("Reservado", (), ""),
        ]
        for i, (nome, opcoes, atual) in enumerate(itens):
            cx = x_start + BOX_W // 2 + i * (BOX_W + gap)
            color = TRACK_COLORS[i] if opcoes else (60, 75, 115)
            self._settings_box(i, cx, BOX_W, BOX_H, BOX_TOP, color)
            lines = self._wrap_text(nome, 24, BOX_W - _px(12))
            for n, ln in enumerate(lines):
                t = self.font(24).render(ln, True, (235, 240, 255) if opcoes else (90, 105, 145))
                self.screen.blit(t, t.get_rect(center=(cx, BOX_TOP + _py(62) + n * _py(18))))
            for n, op in enumerate(opcoes):
                r = pygame.Rect(0, 0, BOX_W - _px(24), _py(34))
                r.center = (cx, BOX_TOP + _py(130) + n * _py(44))
                on = op == atual
                if on:
                    pygame.draw.rect(self.screen, color, r, border_radius=5)
                else:
                    pygame.draw.rect(self.screen, (50, 60, 90), r, 1, border_radius=5)
                t = self.font(28).render(op, True, BG_COLOR if on else (120, 140, 185))
                self.screen.blit(t, t.get_rect(center=r.center))

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
        self._draw_gesto_note()    # 4b. nota de gesto (barra magenta com mao)
        self._advance_effects()    # 5. avanca estado dos efeitos
        self._draw_hit_effects()   # 6. burst de acerto: aneis + particulas + flash
        self._draw_gesto_effects() # 6b. acerto/erro da nota de gesto
        self._draw_hud(score, misses, combo)  # 7. painel HUD
        self._draw_met_hud()       # 7b. indicador do metronomo
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
        pulse = 0.5 if self.fpga_sensorial else 0.5 + 0.5 * math.sin(ticks * 0.003)

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
        title = "VITORIA!" if dash["won"] else "FIM DE JOGO"
        self._dash_text(title, 56, (0, 190, 255) if dash["won"] else (255, 90, 90),
                        center=(cx, _py(32)))
        sub = (f"{SONGS[dash['song']]['title']}   |   "
               f"Score {dash['score']:06d}   |   Erros {dash['misses']}")
        self._dash_text(sub, 26, self._DASH_LABEL, center=(cx, _py(62)))

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

        TILE_W, TILE_H, GAP = _px(180), _py(110), _px(10)
        full_w = 4 * TILE_W + 3 * GAP
        x0 = (SCREEN_W - full_w) // 2
        top = _py(80)
        for i, (label, value, note, key, unit) in enumerate(tiles):
            r = pygame.Rect(x0 + i * (TILE_W + GAP), top, TILE_W, TILE_H)
            self._dash_panel(r)
            self._dash_text(label, 26, self._DASH_LABEL, center=(r.centerx, r.y + _py(15)))
            self._dash_text(value, 64, self._DASH_TEXT, center=(r.centerx, r.y + _py(46)))
            self._dash_text(note, 22, self._DASH_MUTED, center=(r.centerx, r.y + _py(74)))
            delta, color = self._dash_delta(key, unit)
            if delta:
                self._dash_text(delta, 22, color, center=(r.centerx, r.y + _py(94)))

        # ── Paineis do meio ──────────────────────────────────────────────────
        PANEL_W = (full_w - GAP) // 2
        ptop, ph = top + TILE_H + _py(10), _py(176)
        self._draw_dash_tracks(pygame.Rect(x0, ptop, PANEL_W, ph), summ)
        self._draw_dash_timing(
            pygame.Rect(x0 + PANEL_W + GAP, ptop, PANEL_W, ph), summ
        )

        # ── Perfil (nome arcade) ─────────────────────────────────────────────
        prof_top = ptop + ph + _py(10)
        self._draw_dash_profile(pygame.Rect(x0, prof_top, full_w, _py(120)))

        # ── Rodape ───────────────────────────────────────────────────────────
        y = prof_top + _py(120) + _py(16)
        gm = dash["metrics"]
        gtxt = f"Gestos {gm['gestos_acert']}/{gm['gestos_apres']}"
        self._dash_text(
            f"{gtxt}   |   Metronomo: tempo por nivel: Semana 4",
            20, self._DASH_MUTED, center=(cx, y),
        )
        start = "SPACE" if self._sim_mode else "START"
        self._dash_text(f"Escolha o perfil e aperte {start} para salvar", 36,
                        (255, 130, 0), center=(cx, y + _py(30)))

    def _draw_dash_profile(self, rect):
        """
        Nome do perfil em estilo arcade: 3 caixas, a do cursor destacada com
        borda laranja e seta. Estatico (sem piscar).
        """
        dash = self._dash
        self._dash_panel(rect)
        x, y = rect.x + _px(14), rect.y
        self._dash_text("Perfil", 28, self._DASH_TEXT, midleft=(x, y + _py(18)))

        BOX_W, BOX_H, BOX_GAP = _px(46), _py(62), _px(10)
        box_top = y + _py(34)
        hl = (255, 130, 0)
        for k, ch in enumerate(self.fpga_nome):
            r = pygame.Rect(x + k * (BOX_W + BOX_GAP), box_top, BOX_W, BOX_H)
            active = k == self.fpga_cursor
            pygame.draw.rect(self.screen, (24, 36, 72) if active else (14, 22, 48), r,
                             border_radius=6)
            pygame.draw.rect(self.screen, hl if active else self._DASH_BORDER, r,
                             3 if active else 2, border_radius=6)
            self._dash_text(ch, 72, self._DASH_TEXT, center=(r.centerx, r.centery + _py(3)))
            if active:
                tip = r.bottom + _py(5)
                pygame.draw.polygon(self.screen, hl, [
                    (r.centerx, tip), (r.centerx - _px(7), tip + _py(9)),
                    (r.centerx + _px(7), tip + _py(9)),
                ])

        # Comandos
        hx = x + 3 * (BOX_W + BOX_GAP) + _px(26)
        for i, (keys, what) in enumerate((("D / F", "trocar a letra"),
                                          ("J / K", "mudar de posicao"))):
            ly = y + _py(52) + i * _py(30)
            self._dash_text(keys, 30, self._DASH_TEXT, midleft=(hx, ly))
            self._dash_text(what, 26, self._DASH_LABEL, midleft=(hx + _px(70), ly))

        # Situacao do perfil e da comparacao
        sx = rect.x + rect.w // 2 + _px(40)
        prev, profile = dash["prev"], dash["profile"]
        if profile is None:
            lines = [(f"Sem perfil ({NO_PROFILE})", self._DASH_TEXT),
                     ("a sessao e salva sem nome", self._DASH_LABEL),
                     ("e sem comparacao", self._DASH_LABEL)]
        elif prev is not None:
            when = prev.get("data_hora", "")
            when = f"{when[8:10]}/{when[5:7]} {when[11:16]}" if len(when) >= 16 else when
            lines = [(f"Perfil {profile}", self._DASH_TEXT),
                     (f"comparado com a sessao de {when}", self._DASH_LABEL),
                     (prev.get("musica", ""), self._DASH_MUTED)]
        else:
            lines = [(f"Perfil {profile}", self._DASH_TEXT),
                     ("primeira sessao deste perfil", self._DASH_LABEL)]
        for i, (text, color) in enumerate(lines):
            self._dash_text(text, 30 if i == 0 else 24, color,
                            midleft=(sx, y + _py(30) + i * _py(28)))

    def _draw_dash_tracks(self, rect, summ):
        """Acertos por trilha: medidor por trilha + resumo por mao."""
        self._dash_panel(rect)
        x, y = rect.x + _px(14), rect.y
        self._dash_text("Acertos por trilha", 28, self._DASH_TEXT,
                        midleft=(x, y + _py(18)))
        BAR_X, BAR_W, BAR_H = x + _px(28), _px(190), _py(14)
        for t, (hits, notes) in enumerate(summ["trilhas"]):
            ry = y + _py(44) + t * _py(26)
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
        hy = y + _py(44) + 4 * _py(26) + _py(6)
        pygame.draw.line(self.screen, self._DASH_BORDER, (x, hy - _py(10)),
                         (rect.right - _px(14), hy - _py(10)), 1)
        parts = []
        for i, (name, hits, notes) in enumerate(summ["maos"]):
            keys = "+".join(TRACK_LABELS[t] for t in HANDS[i][1])
            val = f"{100 * hits // notes}%" if notes else "--"
            parts.append(f"{name} ({keys}): {val}")
        self._dash_text("   |   ".join(parts), 24, self._DASH_LABEL, midleft=(x, hy))

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
                        self._DASH_LABEL, midleft=(x, rect.y + _py(140)))
        self._dash_text(
            f"erro medio {summ['abs_media_ms']:.0f} ms   |   janela +-{lim_ms:.0f} ms"
            f"   |   1 quadro = {FRAME_MS:.1f} ms",
            20, self._DASH_MUTED, midleft=(x, rect.y + _py(160)),
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
        "--baud", type=int, default=921_600,
        help="Baudrate UART (padrao: 921600; igual ao parametro BAUD do projeto.v)"
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Modo simulacao: teclado (D/F/J/K=trilhas, SPACE=START, G=mao no sensor), sem FPGA",
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
        "--historico",
        default=DEFAULT_HISTORY,
        metavar="CSV",
        help="Arquivo CSV do historico de sessoes (padrao: sessoes/sessoes.csv)",
    )
    args = parser.parse_args()

    Renderer(
        serial_port=args.port,
        baudrate=args.baud,
        sim_mode=args.sim,
        scale=args.scale,
        audio_offset_ms=args.audio_offset,
        history_path=args.historico,
    ).run()
