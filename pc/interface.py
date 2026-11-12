"""
Beat by Bit - Semestre 2
Renderer puro: recebe pacotes da FPGA via UART e renderiza o jogo.
Nenhuma logica de jogo aqui — tudo roda na FPGA (Verilog).

Protocolo UART (921600 baud, 8N1; --baud muda):
  Pacote de 56 bytes a ~60fps (multibyte em big-endian):
    [0]  0x55              marcador de inicio
    [1]  {eq_lo, state}    state (bits 3:0): 0=IDLE 1=COUNTDOWN 2=PLAY 3=PAUSE
                           4=WIN 5=LOSE 6=SELECT 7=SETTINGS 8=NOME
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
    --- nome do perfil (nome_perfil.v), editado na tela do nome (estado 8) ---
    [45]    {cursor[1:0], c0}  c0 = caractere da esquerda
    [46]    c1
    [47]    c2                 caractere: 0='-', 1..26='A'..'Z', 27..36='0'..'9'
    --- sensor HC-SR04, barra de energia (x2), configuracao e metronomo ---
    [48]    distancia em cm (saturada em 255; 0 = sem eco)
    [49]    energia (0..255; durante o x2 = tempo restante escalado)
    [50]    {x2_ativo, ativacoes[6:0]} (ativacoes saturam em 127)
    [51]    movimentos[15:8]
    [52]    config: [1:0] modo do metronomo (0 desl., 1 fixo, 2 adaptativo=fixo),
            [2] perfil sensorial, [3] sem som, [4] pagina das configuracoes
            (0 = atenuador, 1 = jogo), [7:5] progresso do gesto de confirmacao (0-7)
    [53]    metronomo: [1:0] nivel (0=60, 1=30, 2=15 graus, 3=parado), [2] lado do braco
    [54]    movimentos[7:0]
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

# Identidade da interface: tres niveis de texto (principal, apoio, dica), um
# unico destaque (laranja, so para a acao da tela) e um so estilo de painel.
T_MAIN = (236, 242, 255)
T_SUB = (150, 176, 220)
T_HINT = (112, 136, 184)
ACCENT = (255, 130, 0)
C_OK = (110, 225, 160)
C_WARN = (240, 170, 90)
C_BAD = (255, 95, 95)
# Bonus x2: branco-quente, longe do amarelo da trilha D e do laranja da acao
C_BONUS = (255, 234, 175)
C_BONUS_CALM = (222, 206, 160)  # perfil sensorial
C_DATA = (176, 200, 242)  # dado agregado no fim de sessao (neutro: nao e cor de trilha)
PANEL_BG = (11, 18, 40)
PANEL_LINE = (30, 48, 92)
# Tamanhos de fonte (base 800x600): numero principal, titulo, corpo, apoio, dica
F_HERO, F_TITLE, F_BODY, F_SMALL, F_HINT = 110, 56, 34, 26, 22

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
STATE_NAME = 8  # tela do nome do perfil, depois do fim de musica

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
FAIXA_MIN_CM, FAIXA_MAX_CM = 5, 25  # faixa do gesto de confirmacao (gesto_detector.v)
GESTO_CONFIRMA_LEITURAS = 16  # leituras seguidas na faixa que trocam a pagina
GESTO_REARMA_LEITURAS = 4  # leituras seguidas fora da faixa que rearmam
LEITURA_FRAMES = 3.6  # ~60 ms entre leituras do HC-SR04, em quadros de 1/60 s
# Parametros da barra de energia (devem bater com rtl/energia.v)
ENERGIA_DIST_MIN = 5
ENERGIA_DIST_MAX = 30
ENERGIA_GANHO_PRESENCA = 1
ENERGIA_GANHO_MOV = 2
ENERGIA_LIMIAR_MOV = 3
ENERGIA_MAX = 255
DURACAO_X2 = 480  # 8 s a 60 fps
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

    b50 = data[offset + 50]
    movimentos = (data[offset + 51] << 8) | data[offset + 54]
    cfg = data[offset + 52]
    met = data[offset + 53]
    return {
        "dist_cm": data[offset + 48],
        "energia": data[offset + 49],
        "x2_ativo": bool(b50 & 0x80),
        "ativacoes": b50 & 0x7F,
        "movimentos": movimentos,
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
        "movimentos": (data[o + 51] << 8) | data[o + 54],
        "ativacoes_x2": data[o + 50] & 0x7F,
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
        "movimentos": 0,
        "ativacoes_x2": 0,
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
    + ["movimentos", "ativacoes_x2", "modo_metronomo", "perfil_sensorial"]
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
        "movimentos": m.get("movimentos", 0),
        "ativacoes_x2": m.get("ativacoes_x2", 0),
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
        return _migrate_csv(self.path)

    def append(self, row):
        """Grava a linha. Retorna (ok, mensagem)."""
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
            fields = list(SESSION_FIELDS)
            if not new:
                fields = self._migrate() or fields
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields, restval="", extrasaction="ignore")
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
    """Migracao aditiva: preserva colunas antigas (ex.: gestos_*) e adiciona as novas."""
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None:
            return list(SESSION_FIELDS)
        old_fields = list(rd.fieldnames)
        missing = [c for c in SESSION_FIELDS if c not in old_fields]
        if not missing:
            return old_fields
        merged = old_fields + missing
        rows = list(rd)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=merged, restval="")
        w.writeheader()
        w.writerows(rows)
    return merged


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

    chosen, by_lane = [], [[] for _ in range(4)]
    # Notas monofonicas: _MAX_SIMULTANEOUS nao se aplica (o skyline ja tirou acordes)
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
        # Nome do perfil: como o nome_perfil.v, so zera no reset ("---")
        self._nome = [0, 0, 0]
        self._cursor = 0
        # Pre-gera charts de todas as musicas disponíveis e grava os .hex no disco
        self._charts = [
            _midi_to_chart(s, save_hex=True) for s in SONGS if s["available"]
        ]
        self._chart = self._charts[self._selected_song]
        self._rom_depth = len(self._chart)
        # Sensor HC-SR04 / gesto_detector (SETTINGS) / energia.v (PLAY): ~60 ms
        self._dist = 0
        self._ler_acc = 0.0
        self._leitura_ok = False  # houve leitura neste quadro
        self._h_toggle = False  # alterna 10 <-> 20 cm na tecla H
        self._cnt = 0  # leituras seguidas na faixa (SETTINGS)
        self._fora = 0  # leituras seguidas fora da faixa
        self._armado = True
        # Barra de energia e x2 (energia.v): zeram no COUNTDOWN, congelam fora de PLAY
        self._energia = 0
        self._x2_ativo = False
        self._x2_restante = 0
        self._ativacoes = 0
        self._movimentos = 0
        self._dist_ant = 0
        self._ant_valida = False
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

    def step(self, btn, mao=False, mov=False):
        """
        Avanca um frame. btn = [track0, track1, track2, track3, start] (bool);
        mao = mao parada a 15 cm (tecla G); mov = vai-e-vem 10 <-> 20 cm (tecla H).
        Retorna dict compativel com parse_packet().
        """
        self._sensor(mao, mov)
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
            self._step_energia()
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
        energia_out = (
            (self._x2_restante * ENERGIA_MAX) // DURACAO_X2
            if self._x2_ativo
            else self._energia
        )
        return {
            "dist_cm": self._dist,
            "energia": energia_out,
            "x2_ativo": self._x2_ativo,
            "ativacoes": self._ativacoes,
            "movimentos": self._movimentos,
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
                self.state = STATE_NAME
        elif self.state == STATE_NAME:
            if start:
                self.state = STATE_IDLE
                self._reset()
            else:
                self._edit_name(btn)
        if self.state != STATE_NAME:
            self._cursor = 0

    def _sensor(self, mao, mov):
        """HC-SR04: leitura a cada ~60 ms; G = 15 cm, H = alterna 10/20 cm, solto = 0."""
        self._leitura_ok = False
        self._ler_acc += 1
        if self._ler_acc >= LEITURA_FRAMES:
            self._ler_acc -= LEITURA_FRAMES
            if mov:
                self._dist = 20 if self._h_toggle else 10
                self._h_toggle = not self._h_toggle
            elif mao:
                self._dist = 15
            else:
                self._dist = 0
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

    def _step_energia(self):
        """Replica rtl/energia.v bit a bit (so roda em PLAY; congela em PAUSE)."""
        was_x2 = self._x2_ativo
        if was_x2:
            if self._x2_restante <= 1:
                self._x2_ativo = False
                self._x2_restante = 0
                self._energia = 0
            else:
                self._x2_restante -= 1

        if self._leitura_ok:
            if ENERGIA_DIST_MIN <= self._dist <= ENERGIA_DIST_MAX:
                houve_mov = (
                    self._ant_valida
                    and abs(self._dist - self._dist_ant) >= ENERGIA_LIMIAR_MOV
                )
                self._dist_ant = self._dist
                self._ant_valida = True
                if houve_mov and self._movimentos < 65535:
                    self._movimentos += 1
                if not was_x2:
                    ganho = ENERGIA_GANHO_PRESENCA + (
                        ENERGIA_GANHO_MOV if houve_mov else 0
                    )
                    soma = self._energia + ganho
                    if soma >= ENERGIA_MAX:
                        self._energia = ENERGIA_MAX
                        self._x2_ativo = True
                        self._x2_restante = DURACAO_X2
                        if self._ativacoes < 127:
                            self._ativacoes += 1
                    else:
                        self._energia = soma
            else:
                self._ant_valida = False

        self._met["movimentos"] = self._movimentos
        self._met["ativacoes_x2"] = self._ativacoes

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
        self._energia = 0
        self._x2_ativo = False
        self._x2_restante = 0
        self._ativacoes = 0
        self._movimentos = 0
        self._dist_ant = 0
        self._ant_valida = False
        self._h_toggle = False
        self._chart = self._charts[self._selected_song]
        self._rom_depth = len(self._chart)
        self.state = STATE_COUNTDOWN

    def _reset(self):
        self._cd_frame = 0
        self._chart_frame = 0
        self._ages = [[self.EMPTY] * self.SLOTS for _ in range(4)]
        self._met_cycles = 0
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
        mult = 2 if self._x2_ativo else 1
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
                    self.score = (self.score + mult * (100 + self.combo)) & 0xFFFF
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
        self._key_dots = {}  # miniaturas dos botoes das trilhas (_key_dot)

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
        # Sensor, barra de energia (x2), configuracao de jogo e metronomo (bytes 48-54)
        self.fpga_dist = 0
        self.fpga_energia = 0
        self.fpga_x2_ativo = False
        self.fpga_ativacoes = 0
        self.fpga_movimentos = 0
        self.fpga_pagina = 0
        self.fpga_progresso = 0
        self.fpga_modo_met = 0
        self.fpga_sensorial = False
        self.fpga_sem_som = False
        self.fpga_met_nivel = 3
        self.fpga_met_lado = 0
        self._mov_boost = 0  # quadros de brilho extra quando ha movimento da mao
        self._met_disp = 0.0  # angulo mostrado no icone do metronomo (graus)

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
            self._sim_mao = False  # tecla G segurada = mao parada a 15 cm
            self._sim_mov = False  # tecla H segurada = vai-e-vem 10 <-> 20 cm
        else:
            self.serial = SerialReader(port=serial_port, baudrate=baudrate)
            self.serial.start()

        self._build_glow_surfaces()
        self._build_btn_surfaces()
        self._build_track_surfaces()
        self._build_scanline_surf()
        self._build_bg_grid_surf()
        self._build_vignette_surf()
        self._build_progress_bar()
        self._build_ghost_note_surfs()
        self._build_energia_surfs()
        self._build_song_previews()
        self._init_idle_anim()
        self._init_music()
        self._load_logo()

    def _load_logo(self):
        # Logo/icone da identidade visual (identidade/png/*-1x.png, derivados dos SVGs).
        # Sao pixel art: escala por multiplo inteiro (sem suavizacao) para manter os pixels nitidos.
        img_dir = os.path.join(_ASSETS_DIR, "images")
        logo = pygame.image.load(os.path.join(img_dir, "logo-escuro-1x.png")).convert_alpha()
        self._logo = _scale_pixel_art(logo, int(SCREEN_W * 0.30))

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
            pygame.draw.rect(self.scanline_surf, (0, 0, 0, 36), (0, row, SCREEN_W, 1))

    def _build_bg_grid_surf(self):
        """Pre-renderiza grid horizontal sutil no fundo."""
        self.bg_grid_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        for y in range(0, SCREEN_H, _py(40)):
            pygame.draw.line(
                self.bg_grid_surf, (14, 24, 54, 255), (0, y), (SCREEN_W, y), 1
            )

    def _build_vignette_surf(self):
        """Pre-renderiza a vinheta: escurece os cantos em elipse, sem degraus."""
        self.vignette_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        nx = np.linspace(-1.0, 1.0, SCREEN_W)[:, None]
        ny = np.linspace(-1.0, 1.0, SCREEN_H)[None, :]
        d = np.sqrt((nx * nx + ny * ny) / 2.0)  # 0 no centro, 1 nos cantos
        alpha = pygame.surfarray.pixels_alpha(self.vignette_surf)
        alpha[:] = (100 * d ** 3).astype(np.uint8)
        del alpha  # libera o lock da surface

    def _build_progress_bar(self):
        """Dimensoes da barra de progresso da musica."""
        self._prog_bw = SCREEN_W - _px(40)
        self._prog_bh = max(3, _py(5))

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
        prev_state = self.fpga_state
        prev_mov = self.fpga_movimentos
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
        self.fpga_metrics = {
            k: (list(v) if isinstance(v, list) else v) for k, v in pkt["metrics"].items()
        }
        self.fpga_nome = pkt["nome"]
        self.fpga_cursor = pkt["cursor"]
        self.fpga_dist = pkt["dist_cm"]
        self.fpga_energia = pkt["energia"]
        self.fpga_x2_ativo = pkt["x2_ativo"]
        self.fpga_ativacoes = pkt["ativacoes"]
        self.fpga_movimentos = pkt["movimentos"]
        if pkt["state"] == STATE_PLAY and prev_state == STATE_PLAY:
            if self.fpga_movimentos > prev_mov:
                self._mov_boost = 15
        elif pkt["state"] not in (STATE_PLAY, STATE_PAUSE):
            self._mov_boost = 0
        self.fpga_pagina = pkt["pagina"]
        self.fpga_progresso = pkt["progresso"]
        self.fpga_modo_met = pkt["modo_met"]
        self.fpga_sensorial = pkt["sensorial"]
        self.fpga_sem_som = pkt["sem_som"]
        self.fpga_met_nivel = pkt["met_nivel"]
        self.fpga_met_lado = pkt["met_lado"]
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
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_g:
                self._sim_mao = True  # G segurada = mao parada a 15 cm
            elif ev.type == pygame.KEYUP and ev.key == pygame.K_g:
                self._sim_mao = False
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_h:
                self._sim_mov = True  # H segurada = vai-e-vem 10 <-> 20 cm
            elif ev.type == pygame.KEYUP and ev.key == pygame.K_h:
                self._sim_mov = False
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
            self.sim.step([False] * 5, self._sim_mao, self._sim_mov)
        pkt = self.sim.step(self._sim_btn, self._sim_mao, self._sim_mov)
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
        if state in (STATE_WIN, STATE_LOSE, STATE_NAME):
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

    def _build_track_surfaces(self):
        """Pre-renderiza colunas das trilhas (jogo, pausa e menu)."""
        self._track_surfs = {}
        for alpha in (50, 18):
            s = pygame.Surface((TRACK_W, SCREEN_H), pygame.SRCALPHA)
            s.fill((*TRACK_BG, alpha))
            self._track_surfs[alpha] = s
        # No jogo cada trilha tem um degrade vertical fixo: escura no topo, mais
        # clara e levemente tingida da sua cor perto da linha de acerto, para
        # levar o olho ate o alvo. Estatico: vale tambem no perfil sensorial.
        self._track_play = [self._track_gradient(TRACK_BG, c) for c in TRACK_COLORS]
        # Versao do x2: a mesma trilha com a cor propria mais forte. Entra e sai
        # aos poucos por cima da normal (ver _draw_tracks), sem repintar a pista.
        self._track_x2 = [self._track_gradient(TRACK_BG, c, 0.2) for c in TRACK_COLORS]
        self._x2_mix = 0.0  # 0 = trilha normal, 1 = cor do x2 no maximo

    @staticmethod
    def _track_gradient(base, tint=None, strength=0.07):
        """Coluna de trilha: brilho 0.55 no topo -> 1.45 na linha de acerto -> 0.8 embaixo."""
        s = pygame.Surface((TRACK_W, SCREEN_H)).convert()
        for y in range(SCREEN_H):
            if y <= HIT_Y:
                t = (y / HIT_Y) ** 2  # sobe devagar e concentra o brilho no alvo
                k = 0.55 + 0.9 * t
            else:
                t = 1.0 - (y - HIT_Y) / max(1, SCREEN_H - HIT_Y)
                k = 0.8 + 0.65 * t
            col = [min(255, c * k) for c in base]
            if tint:
                col = [c + strength * t * tc for c, tc in zip(col, tint)]
            pygame.draw.line(s, [min(255, int(c)) for c in col], (0, y), (TRACK_W, y))
        return s

    def _draw_tracks(self, alpha=255):
        # No x2 cada trilha fica com a propria cor um pouco mais forte, em
        # transicao suave (~0,4 s; mais fraca e mais lenta no perfil sensorial)
        boost = 0.0
        if alpha == 255:
            surfs = self._track_play
            on = self.fpga_state == STATE_PLAY and self.fpga_x2_ativo
            calm = self.fpga_sensorial
            target = (0.5 if calm else 1.0) if on else 0.0
            step = 0.02 if calm else 0.04
            self._x2_mix += max(-step, min(step, target - self._x2_mix))
            boost = self._x2_mix
        else:
            surf = self._track_surfs.get(alpha)
            if surf is None:
                surf = pygame.Surface((TRACK_W, SCREEN_H), pygame.SRCALPHA)
                surf.fill((*TRACK_BG, alpha))
            surfs = [surf] * 4
        bcol = LINE_COLOR if alpha == 255 else (*LINE_COLOR, alpha)
        bw = 1

        for i, (x, surf) in enumerate(zip(TRACK_X, surfs)):
            x0 = x - TRACK_W // 2
            x1 = x + TRACK_W // 2
            self.screen.blit(surf, (x0, 0))
            if boost > 0:
                self._track_x2[i].set_alpha(int(255 * boost))
                self.screen.blit(self._track_x2[i], (x0, 0))
            pygame.draw.line(self.screen, bcol, (x0, 0), (x0, SCREEN_H), bw)
            pygame.draw.line(self.screen, bcol, (x1, 0), (x1, SCREEN_H), bw)

    def _draw_hit_zone(self, active_tracks=None):
        """Desenha a linha da hit zone e os indicadores de botao pixel art."""
        ticks = pygame.time.get_ticks()
        calm = self.fpga_sensorial
        pulse = 0.5 if calm else 0.5 + 0.5 * math.sin(ticks * 0.003)
        if self.fpga_state == STATE_PLAY and self.fpga_x2_ativo:
            # Linha de acerto na cor do bonus (mesmo pulso suave da linha normal)
            k = 0.8 if calm else 0.7 + 0.3 * pulse
            hit_col = tuple(int(c * k) for c in (C_BONUS_CALM if calm else C_BONUS))
        else:
            b = int(80 + 80 * pulse)
            hit_col = (b // 2, b // 2, b)

        # A linha cobre so as trilhas; as margens ficam livres para o HUD
        lx0 = TRACK_X[0] - TRACK_W // 2
        lx1 = TRACK_X[-1] + TRACK_W // 2
        for offset, a in [(3, 40), (2, 80), (1, 160), (0, 255)]:
            color = (*hit_col, a) if offset else hit_col
            pygame.draw.line(
                self.screen, color, (lx0, HIT_Y + offset), (lx1, HIT_Y + offset), 1
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

    # ── Barra de energia, medidor da mao e selo x2 ────────────────────────────

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

    def _build_energia_surfs(self):
        """Pre-renderiza icones da mao para o medidor lateral (zero alocacao por frame)."""
        sz = _ps(36)
        ih = int(sz * 0.78)
        self._hand_icons = {}
        for key, fill, outline in (
            ("off", (90, 115, 160), (22, 34, 68)),
            ("on", (115, 245, 175), (16, 70, 52)),
            ("x2", C_BONUS, (84, 70, 36)),
        ):
            s = pygame.Surface((sz, sz), pygame.SRCALPHA)
            self._draw_hand_icon(s, sz // 2, sz // 2, ih, fill, outline)
            self._hand_icons[key] = s

    def _draw_energia_hud(self):
        """Barra de energia vertical, indicador da mao (5-30 cm) e selo x2."""
        calm = self.fpga_sensorial
        x2 = self.fpga_x2_ativo
        ener = max(0, min(ENERGIA_MAX, self.fpga_energia))
        d = self.fpga_dist
        mao_ok = ENERGIA_DIST_MIN <= d <= ENERGIA_DIST_MAX
        mov = self._mov_boost > 0 and not x2
        if self.fpga_state == STATE_PLAY and self._mov_boost > 0:
            self._mov_boost -= 1
        gold = C_BONUS_CALM if calm else C_BONUS

        # ── Barra de energia (0..255) na margem esquerda ─────────────────────
        x = _px(18)
        self._text("ENERGIA", F_HINT, gold if x2 else T_HINT, topleft=(x, _py(128)))
        bar = pygame.Rect(x, _py(150), _px(26), _py(330))
        rad = _ps(5)
        pygame.draw.rect(self.screen, PANEL_BG, bar, border_radius=rad)
        if ener > 0:
            fh = max(3, bar.h * ener // ENERGIA_MAX)
            if x2:
                fcol = gold
            elif mov:
                fcol = (75, 200, 225) if calm else (95, 245, 255)
            else:
                fcol = (40, 150, 200) if calm else (25, 165, 235)
            pygame.draw.rect(
                self.screen, fcol, (bar.x, bar.bottom - fh, bar.w, fh), border_radius=rad
            )
        if x2:
            line = gold
        elif mov and not calm:
            line = (140, 250, 255)  # movimento da mao: a barra enche mais rapido
        else:
            line = PANEL_LINE
        pygame.draw.rect(self.screen, line, bar, 2, border_radius=rad)

        # ── Mao no sensor: icone aceso na faixa valida + distancia ───────────
        hx = bar.right + _px(36)
        ikey = "x2" if (x2 and mao_ok) else ("on" if mao_ok else "off")
        hicon = self._hand_icons[ikey]
        self.screen.blit(hicon, hicon.get_rect(center=(hx, bar.bottom - _py(40))))
        self._text(f"{d} cm" if d > 0 else "-- cm", F_HINT,
                   C_OK if mao_ok else T_HINT, center=(hx, bar.bottom - _py(10)))
        if self._sim_mode:
            self._text("G  mao parada", F_HINT, T_HINT, topleft=(x, bar.bottom + _py(6)))
            self._text("H  mao mexendo", F_HINT, T_HINT, topleft=(x, bar.bottom + _py(22)))

        # ── Selo x2 no topo central (vao entre as trilhas 1 e 2) ─────────────
        if x2:
            cx = SCREEN_W // 2
            self._text("x2", 84, gold, center=(cx, _py(40)))
            rest_s = ener * (DURACAO_X2 / FPS) / ENERGIA_MAX
            self._text(f"{rest_s:.1f} s", F_SMALL, T_SUB, center=(cx, _py(76)))

    # ── Metronomo no HUD ──────────────────────────────────────────────────────

    def _draw_met_hud(self):
        """Icone de metronomo: braco que alterna de lado a cada tempo (so se ligado)."""
        if self.fpga_modo_met == 0:
            return
        cx, base_y = SCREEN_W - _px(42), _py(150)
        calm = self.fpga_sensorial
        target = 0.0
        if not calm and self.fpga_met_nivel != 3:
            ang = MET_ANGULOS[self.fpga_met_nivel] / 2
            target = ang if self.fpga_met_lado else -ang
        self._met_disp = target if calm else self._met_disp + (target - self._met_disp) * 0.4
        col = T_HINT if calm else T_SUB
        bw, bh = _ps(16), _ps(40)
        body = [(cx - bw, base_y), (cx + bw, base_y), (cx + bw // 2, base_y - bh),
                (cx - bw // 2, base_y - bh)]
        pygame.draw.polygon(self.screen, PANEL_BG, body)
        pygame.draw.polygon(self.screen, PANEL_LINE, body, 2)
        rad = math.radians(self._met_disp)
        arm = bh - _ps(4)
        tip = (cx + math.sin(rad) * arm, base_y - _ps(2) - math.cos(rad) * arm)
        pygame.draw.line(self.screen, col, (cx, base_y - _ps(2)), tip, 3)
        pygame.draw.circle(self.screen, col, (int(tip[0]), int(tip[1])), _ps(4))

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

    # ── Blocos de interface ───────────────────────────────────────────────────
    # Toda tela usa os mesmos tres niveis de texto (T_MAIN, T_SUB, T_HINT), um
    # so estilo de painel e o rodape como unico lugar das instrucoes.

    def _text(self, text, size, color, **pos):
        surf = self.font(size).render(text, True, color)
        rect = surf.get_rect(**pos)
        self.screen.blit(surf, rect)
        return rect

    def _panel(self, rect, line=PANEL_LINE):
        bg = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(bg, (*PANEL_BG, 235), bg.get_rect(), border_radius=_ps(8))
        self.screen.blit(bg, rect.topleft)
        pygame.draw.rect(self.screen, line, rect, 2, border_radius=_ps(8))

    def _key_dot(self, i, r=None):
        """Botao da trilha em miniatura (cor; tecla so no --sim), para citar botoes no texto."""
        r = r or _ps(10)
        if (i, r) not in self._key_dots:
            s = pygame.Surface((2 * r, 2 * r), pygame.SRCALPHA)
            pygame.draw.circle(s, TRACK_COLORS[i], (r, r), r)
            if self._sim_mode:  # a tecla so interessa na simulacao por teclado
                lab = self.font(int(2.6 * r / SS)).render(TRACK_LABELS[i], True, BG_COLOR)
                s.blit(lab, lab.get_rect(center=(r, r + max(1, r // 8))))
            self._key_dots[(i, r)] = s
        return self._key_dots[(i, r)]

    def _keys(self, idx=(0, 1, 2, 3), r=None):
        return [self._key_dot(i, r) for i in idx]

    def _inline(self, parts, size, color=T_HINT, **pos):
        """Linha com trechos de cores diferentes: str, (str, cor) ou Surface."""
        surfs = []
        for p in parts:
            if isinstance(p, pygame.Surface):
                surfs.append(p)
            else:
                text, col = (p, color) if isinstance(p, str) else p
                surfs.append(self.font(size).render(text, True, col))
        gap = _px(5)
        rect = pygame.Rect(
            0, 0,
            sum(s.get_width() for s in surfs) + gap * (len(surfs) - 1),
            max(s.get_height() for s in surfs),
        )
        for k, v in pos.items():
            setattr(rect, k, v)
        x = rect.x
        for s in surfs:
            self.screen.blit(s, (x, rect.centery - s.get_height() // 2))
            x += s.get_width() + gap
        return rect

    @property
    def _start_key(self):
        return "SPACE" if self._sim_mode else "START"

    def _footer(self, action=None, hint=None):
        """Rodape: a acao principal (tecla em laranja) e, abaixo, uma dica."""
        cx = SCREEN_W // 2
        if action:
            self._inline(action, F_BODY + 2, T_MAIN, center=(cx, _py(552)))
        if hint:
            self._inline(hint, F_HINT, T_HINT, center=(cx, _py(578)))

    def _title(self, text):
        """Titulo de tela: sempre centralizado, na mesma altura e cor."""
        self._text(text, F_TITLE, T_MAIN, center=(SCREEN_W // 2, _py(56)))

    def _draw_menu_bg(self):
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self.screen.blit(self.scanline_surf, (0, 0))

    def _draw_mode_tag(self):
        """Canto inferior direito: so aparece em simulacao ou sem a FPGA."""
        if self._sim_mode:
            text, color = "SIM", T_HINT
        elif not self.serial.connected:
            text, color = "FPGA OFF", C_WARN
        else:
            return
        self._text(text, F_HINT, color,
                   bottomright=(SCREEN_W - _px(14), SCREEN_H - _py(22)))

    def _draw_hud(self, score, misses, combo):
        # Score (principal) e erros na margem esquerda
        x = _px(18)
        self._text("SCORE", F_HINT, T_HINT, topleft=(x, _py(14)))
        self._text(f"{score:06d}", 60, T_MAIN, topleft=(x, _py(30)))
        self._text("ERROS", F_HINT, T_HINT, topleft=(x, _py(78)))
        pw, ph, pg = _px(8), _py(8), _px(3)
        for k in range(MAX_MISSES):
            pip = pygame.Rect(x + k * (pw + pg), _py(98), pw, ph)
            if k < misses:
                pygame.draw.rect(self.screen, C_BAD, pip, border_radius=2)
            else:
                pygame.draw.rect(self.screen, PANEL_LINE, pip, 2, border_radius=2)

        # Combo na margem direita, espelhando o score
        if combo >= 3:
            wobble = 0
            if combo >= 10 and not self.fpga_sensorial:  # sem tremor no perfil sensorial
                wobble = int(2 * math.sin(pygame.time.get_ticks() * 0.012))
            rx = SCREEN_W - _px(18)
            self._text("COMBO", F_HINT, T_HINT, topright=(rx, _py(14)))
            self._text(f"x{combo}", 60, T_MAIN, topright=(rx + wobble, _py(30)))

        # Barra de progresso baseada no frame_counter da FPGA (ou sim)
        if self.fpga_state == STATE_PLAY:
            _depth = (
                len(self.sim._chart)
                if self._sim_mode
                else int(SONGS[self._selected_song]["duration"] * FPS)
            )
            ratio = min(1.0, self.fpga_frame_counter / max(1, _depth - 1))
            by = SCREEN_H - _py(14)
            pygame.draw.rect(self.screen, PANEL_LINE, (_px(20), by, self._prog_bw, self._prog_bh), border_radius=3)
            fill_w = int(self._prog_bw * ratio)
            if fill_w > 0:
                pygame.draw.rect(self.screen, T_SUB, (_px(20), by, fill_w, self._prog_bh), border_radius=3)

    # ── Telas ─────────────────────────────────────────────────────────────────

    def _draw_idle(self):
        self.screen.fill(BG_COLOR)
        self._draw_bg_grid()
        self._draw_tracks(alpha=18)
        self._draw_idle_anim()
        self.screen.blit(self.scanline_surf, (0, 0))

        # Logo com flutuacao
        ticks = pygame.time.get_ticks()
        float_y = 0 if self.fpga_sensorial else 6 * math.sin(ticks * 0.002)
        cx = SCREEN_W // 2
        logo_rect = self._logo.get_rect(center=(cx, _py(190) + int(float_y)))
        self.screen.blit(self._logo, logo_rect)

        # Unica chamada da tela (pisca; fixa no perfil sensorial)
        if ticks % 1000 < 650 or self.fpga_sensorial:
            self._text(f"Aperte {self._start_key} para jogar", 50, ACCENT,
                       center=(cx, _py(372)))

        if self._toast and time.monotonic() - self._toast[2] < TOAST_S:
            text, ok, _ = self._toast
            self._text(text, F_SMALL, C_OK if ok else C_WARN, center=(cx, _py(430)))

        self._footer(hint=[*self._keys(), ("  configuracoes", T_SUB)])

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
                        n = bin(v & 0x0F).count("1")  # notas de botao nas 4 trilhas
                        preview["bins"][min(bins_n - 1, int(f / per_bin))] += n
                        preview["notes"] += n
            except (OSError, ValueError):
                preview = None
            self._song_previews.append(preview)

    # Grade comum dos menus: 4 colunas, uma por botao (D, F, J, K)
    _COL_W, _COL_GAP = _px(150), _px(18)

    def _col_rect(self, i, top, h, n=4):
        x0 = (SCREEN_W - (n * self._COL_W + (n - 1) * self._COL_GAP)) // 2
        return pygame.Rect(x0 + i * (self._COL_W + self._COL_GAP), top, self._COL_W, h)

    def _col_header(self, i, rect, active=True):
        """Painel da coluna com o botao da trilha no topo (tecla so no --sim)."""
        color = TRACK_COLORS[i]
        line = tuple(int(c * 0.45) for c in color) if active else PANEL_LINE
        self._panel(rect, line)
        btn = self.btn_on[i] if active else self.btn_off[i]
        by = rect.y + _py(46)
        self.screen.blit(btn, btn.get_rect(center=(rect.centerx, by)))
        if self._sim_mode:  # a tecla so interessa na simulacao por teclado
            self._text(TRACK_LABELS[i], 40, BG_COLOR if active else T_HINT,
                       center=(rect.centerx, by + 2))

    def _draw_song_card(self, i, song, rect):
        """Card de musica: botao da trilha, titulo, preview do chart e estatisticas."""
        color = TRACK_COLORS[i]
        avail = song["available"]
        cx = rect.centerx
        self._col_header(i, rect, avail)
        if not avail:
            self._text("EM BREVE", F_SMALL, T_HINT, center=rect.center)
            return

        # Titulo (principal), origem e autor
        text_w = rect.w - _px(20)
        y = rect.y + _py(104)
        for line in self._wrap_text(song["title"], 30, text_w)[:2]:
            self._text(line, 30, T_MAIN, center=(cx, y))
            y += _py(22)
        y = rect.y + _py(104) + 2 * _py(22) + _py(4)  # sempre 2 linhas de titulo
        for line in self._wrap_text(song["subtitle"], F_HINT, text_w):
            self._text(line, F_HINT, T_SUB, center=(cx, y))
            y += _py(17)
        if song.get("author"):
            self._text(song["author"], F_HINT, T_HINT, center=(cx, y + _py(2)))

        # Mini-preview do chart (densidade de notas ao longo da musica)
        bh = _py(56)
        bw = rect.w - 2 * _px(16)
        bx = rect.x + _px(16)
        bottom = rect.bottom - _py(40)
        dim = tuple(int(c * 0.4) for c in color)  # recessivo: o titulo manda
        preview = self._song_previews[i] if i < len(self._song_previews) else None
        notes_txt = ""
        if preview and preview["notes"]:
            bins = preview["bins"]
            peak = max(bins)
            slot = bw / len(bins)
            for k, v in enumerate(bins):
                h = max(2, int(bh * v / peak)) if v else 2
                bar = pygame.Rect(int(bx + k * slot) + 1, bottom - h, max(1, int(slot) - 2), h)
                pygame.draw.rect(self.screen, dim if v else PANEL_LINE, bar)
            notes_txt = f"{preview['notes']} notas"

        # Estatisticas: duracao e numero de notas
        mins, secs = divmod(int(round(song["duration"])), 60)
        stat_y = rect.bottom - _py(20)
        self._text(f"{mins}:{secs:02d}", F_HINT, T_SUB, midleft=(bx, stat_y))
        if notes_txt:
            self._text(notes_txt, F_HINT, T_SUB, midright=(bx + bw, stat_y))

    def _draw_song_select(self):
        """Tela de selecao de musica: 4 cards, cada um no botao da sua cor."""
        self._draw_menu_bg()
        self._title("ESCOLHA A MUSICA")
        for i, song in enumerate(SONGS):
            self._draw_song_card(i, song, self._col_rect(i, _py(130), _py(330)))
        self._footer([*self._keys(), "  aperte o botao da cor da musica"],
                     [(self._start_key, T_SUB), "voltar"])

    def _draw_settings(self):
        """
        Tela de configuracoes em duas abas ("Som" e "Jogo"), trocadas pelo gesto de
        confirmacao (mao sobre o sensor ~1 s). Estatica de proposito (sem piscar nem
        pulsar): o publico-alvo inclui jogadores sensiveis a estimulos.
        """
        self._draw_menu_bg()
        cx0 = SCREEN_W // 2
        page = self.fpga_pagina
        self._title("CONFIGURACOES")

        # Abas: a ativa em destaque, com sublinhado laranja
        TAB_W = _px(110)
        for k, name in enumerate(("SOM", "JOGO")):
            on = k == page
            r = self._text(name, 40, T_MAIN if on else T_HINT,
                           center=(cx0 + (k - 0.5) * TAB_W, _py(96)))
            if on:
                pygame.draw.rect(self.screen, T_MAIN,
                                 (r.x, _py(110), r.w, max(3, _py(3))), border_radius=2)

        # Gesto de confirmacao numa linha: mao, instrucao, progresso (0-7), distancia
        d = self.fpga_dist
        na_faixa = FAIXA_MIN_CM <= d <= FAIXA_MAX_CM
        SEGS, sw, sh, sg = 8, _px(10), _py(8), _px(3)
        segs = pygame.Surface((SEGS * sw + (SEGS - 1) * sg, sh), pygame.SRCALPHA)
        for k in range(SEGS):
            r = pygame.Rect(k * (sw + sg), 0, sw, sh)
            if k < self.fpga_progresso:
                pygame.draw.rect(segs, T_MAIN, r, border_radius=2)
            else:
                pygame.draw.rect(segs, PANEL_LINE, r, 2, border_radius=2)
        self._inline(
            [self._hand_icons["on" if na_faixa else "off"],
             "segure a mao no sensor para trocar de aba ", segs,
             (f" {d} cm" if d else " -- cm", C_OK if na_faixa else T_HINT)],
            F_HINT, T_SUB, center=(cx0, _py(138)),
        )

        top, h = _py(164), _py(330)
        if page == 0:
            self._draw_settings_som(top, h)
        else:
            self._draw_settings_jogo(top, h)

        self._footer(
            [(self._start_key, ACCENT), "salvar e voltar"],
            ["segure G para simular a mao no sensor"] if self._sim_mode else None,
        )

    def _draw_settings_som(self, top, h):
        SEG_W, SEG_H, SEG_GAP = _px(70), _py(22), _py(8)
        for i, band in enumerate(EQ_BANDS):
            rect = self._col_rect(i, top, h)
            cx = rect.centerx
            color = TRACK_COLORS[i]
            level = self._eq[i]
            self._col_header(i, rect)
            self._text(band["name"], 30, T_MAIN, center=(cx, top + _py(104)))
            self._text(band["range"], F_HINT, T_HINT, center=(cx, top + _py(126)))
            # Medidor: segmentos acesos = quanto da faixa ainda soa
            lit = EQ_LEVELS - level
            seg_bottom = top + _py(270)
            for k in range(EQ_LEVELS):
                y = seg_bottom - (k + 1) * SEG_H - k * SEG_GAP
                seg = pygame.Rect(cx - SEG_W // 2, y, SEG_W, SEG_H)
                if k < lit:
                    pygame.draw.rect(self.screen, color, seg, border_radius=4)
                else:
                    pygame.draw.rect(self.screen, PANEL_LINE, seg, 2, border_radius=4)
            db_text = "0 dB" if level == 0 else f"-{EQ_STEP_DB * level} dB"
            self._text(db_text, F_BODY, T_SUB, center=(cx, top + _py(298)))

    def _draw_settings_jogo(self, top, h):
        # Opcao "ligada" sempre em cima; o botao K nao tem funcao nesta aba
        lig = ("Ligado", "Desligado")
        itens = [
            ("Metronomo", ("Fixo", "Desligado"), "Fixo" if self.fpga_modo_met else "Desligado"),
            ("Perfil sensorial", lig, lig[0 if self.fpga_sensorial else 1]),
            ("Sem som", lig, lig[0 if self.fpga_sem_som else 1]),
        ]
        for i, (nome, opcoes, atual) in enumerate(itens):
            rect = self._col_rect(i, top, h, len(itens))
            cx = rect.centerx
            color = TRACK_COLORS[i]
            self._col_header(i, rect, bool(opcoes))
            for n, ln in enumerate(self._wrap_text(nome, 30, rect.w - _px(16))):
                self._text(ln, 30, T_MAIN, center=(cx, top + _py(104) + n * _py(22)))
            for n, op in enumerate(opcoes):
                r = pygame.Rect(0, 0, rect.w - _px(32), _py(38))
                r.center = (cx, top + _py(190) + n * _py(50))
                on = op == atual
                if on:
                    pygame.draw.rect(self.screen, color, r, border_radius=_ps(6))
                else:
                    pygame.draw.rect(self.screen, PANEL_LINE, r, 2, border_radius=_ps(6))
                self._text(op, F_BODY, BG_COLOR if on else T_SUB,
                           center=(r.centerx, r.centery + 2))

    def _draw_countdown(self, sec):
        self.screen.fill(BG_COLOR)
        self._draw_tracks()
        self._draw_hit_zone()
        cx = SCREEN_W // 2

        # Nome da musica acima da contagem
        self._text(SONGS[self._selected_song]["title"], F_BODY, T_SUB,
                   center=(cx, SCREEN_H // 2 - _py(130)))
        if sec > 0:
            self._text(str(sec), 220, ACCENT, center=(cx, SCREEN_H // 2 - _py(40)))

    def _draw_play(self, notes, score, misses, combo):
        self.screen.fill(BG_COLOR)
        self._draw_tracks()        # 1. trilhas com degrade + bordas verticais
        self._draw_notes(notes)    # 2. notas com glow
        # 3. linha pulsante + alvos; o alvo da trilha acende por alguns quadros
        #    depois de um acerto (menos tempo no perfil sensorial)
        lit_frames = 5 if self.fpga_sensorial else 9
        self._draw_hit_zone({e["lane"] for e in self._hit_effects if e["age"] < lit_frames})
        self._advance_effects()    # 4. avanca estado dos efeitos
        self._draw_hit_effects()   # 5. burst de acerto: aneis + particulas + flash
        self._draw_hud(score, misses, combo)  # 6. score, erros, combo, progresso
        self._draw_energia_hud()   # 6b. barra de energia, mao e selo x2
        self._draw_met_hud()       # 6c. indicador do metronomo
        self.screen.blit(self.scanline_surf, (0, 0))  # 7. scanlines CRT

    def _draw_pause(self):
        self.screen.fill(BG_COLOR)
        self._draw_tracks(alpha=50)
        self._draw_hud(self.fpga_score, self.fpga_misses, self.fpga_combo)
        self.scratch.fill((0, 0, 0, 150))  # escurece o jogo atras da pausa
        self.screen.blit(self.scratch, (0, 0))
        self.screen.blit(self.scanline_surf, (0, 0))

        cx = SCREEN_W // 2
        self._text("PAUSADO", 96, T_MAIN, center=(cx, _py(290)))
        if self._sim_mode:
            hint = [("ESC", T_SUB), "menu inicial"]
        else:
            hint = [self._key_dot(0), " menu inicial"]
        self._footer([(self._start_key, ACCENT), "continuar"], hint)

    # ── Dashboard de fim de sessao (DASHBOARD_SESSAO) ─────────────────────────
    # Estatico de proposito (sem piscar nem pulsar), como a tela de
    # configuracoes. A taxa de acerto e o numero principal; as demais metricas
    # ficam em texto neutro, e a cor da trilha so marca a identidade na barra.

    def _dash_delta(self, key, unit):
        """Texto e cor da variacao em relacao a sessao anterior."""
        dash = self._dash
        if dash["prev"] is None or key not in dash["deltas"]:
            return "", T_HINT
        d, better = dash["deltas"][key]
        if better is None:
            return "igual a anterior", T_HINT
        word = "melhor" if better else "pior"
        color = C_OK if better else C_WARN
        if key == "media_ms":
            # Melhor = mais perto de 0; a diferenca com sinal confunde aqui
            old = dash["summary"]["media_ms"] - d
            return f"antes {old:+.0f}{unit} ({word})", color
        return f"{d:+.0f}{unit} ({word})", color

    def _draw_dashboard(self):
        dash = self._dash
        if dash is None:  # primeiro quadro do fim, antes de _update_session
            return
        summ = dash["summary"]
        self._draw_menu_bg()

        GAP = _px(10)
        full_w = _px(750)
        x0 = (SCREEN_W - full_w) // 2
        x1 = x0 + full_w

        # ── Cabecalho: resultado no centro, musica a esquerda, perfil a direita
        self._title("VITORIA!" if dash["won"] else "FIM DE JOGO")
        self._text(SONGS[dash["song"]]["title"], F_SMALL, T_SUB, midleft=(x0, _py(76)))
        prev, profile = dash["prev"], dash["profile"]
        if profile is None:
            self._text("sem perfil", F_SMALL, T_HINT, midright=(x1, _py(76)))
        else:
            self._text(f"Perfil {profile}", F_BODY, T_SUB, midright=(x1, _py(52)))
            if prev is not None:
                when = prev.get("data_hora", "")
                when = f"{when[8:10]}/{when[5:7]} {when[11:16]}" if len(when) >= 16 else when
                self._text(f"comparado com a sessao de {when}", F_HINT, T_HINT,
                           midright=(x1, _py(76)))
            else:
                self._text("primeira sessao deste perfil", F_HINT, T_HINT,
                           midright=(x1, _py(76)))

        # ── Os dois numeros principais: score e taxa de acerto ───────────────
        top, HERO_H = _py(98), _py(170)
        HERO_W = (full_w - GAP) // 2

        score = pygame.Rect(x0, top, HERO_W, HERO_H)
        self._panel(score)
        hx, hr = score.x + _px(18), score.right - _px(18)
        self._text("Score", F_SMALL, T_SUB, midleft=(hx, top + _py(22)))
        self._text(f"{dash['score']:06d}", 124, T_MAIN, midleft=(hx, top + _py(80)))
        erros = "erro" if dash["misses"] == 1 else "erros"
        self._inline(
            [(str(summ["maior_combo"]), T_MAIN), " maior combo        ",
             (str(dash["misses"]), T_MAIN), f" {erros}"],
            F_BODY, T_SUB, midleft=(hx, top + _py(140)),
        )
        delta, color = self._dash_delta("maior_combo", "")
        if delta:
            self._text(delta, F_HINT, color, midright=(hr, top + _py(140)))

        taxa_r = pygame.Rect(score.right + GAP, top, x1 - score.right - GAP, HERO_H)
        self._panel(taxa_r)
        hx, hr = taxa_r.x + _px(18), taxa_r.right - _px(18)
        self._text("Taxa de acerto", F_SMALL, T_SUB, midleft=(hx, top + _py(22)))
        taxa = summ["taxa"]
        vr = self._text("--" if taxa is None else f"{taxa}%", 124, T_MAIN,
                        midleft=(hx, top + _py(80)))
        # Contagem na mesma base do numero grande; variacao na linha do rotulo
        base = vr.bottom - _py(14)
        if taxa is None:
            self._text("nenhuma nota julgada", F_SMALL, T_SUB, bottomright=(hr, base))
        else:
            self._text(f"{summ['acertos']} de {summ['notas']} notas", F_SMALL, T_SUB,
                       bottomright=(hr, base))
        delta, color = self._dash_delta("taxa", " pp")
        if delta:
            self._text(delta, F_HINT, color, midright=(hr, top + _py(22)))
        # Barra segmentada (cada segmento = 5%)
        SEGS, sg = 20, _px(3)
        sw = (hr - hx - (SEGS - 1) * sg) // SEGS
        lit = 0 if taxa is None else round(taxa * SEGS / 100)
        for k in range(SEGS):
            seg = pygame.Rect(hx + k * (sw + sg), top + _py(132), sw, _py(16))
            if k < lit:
                pygame.draw.rect(self.screen, C_DATA, seg, border_radius=3)
            else:
                pygame.draw.rect(self.screen, PANEL_LINE, seg, 2, border_radius=3)

        # ── Detalhes: acertos por trilha e assincronia ───────────────────────
        ptop, ph = top + HERO_H + GAP, _py(222)
        self._draw_dash_tracks(pygame.Rect(x0, ptop, HERO_W, ph), summ)
        self._draw_dash_timing(
            pygame.Rect(score.right + GAP, ptop, x1 - score.right - GAP, ph), summ
        )

        # ── Rodape ───────────────────────────────────────────────────────────
        gm = dash["metrics"]
        self._footer(
            [(self._start_key, ACCENT), "continuar"],
            [(f"movimentos da mao {gm['movimentos']}      "
              f"x2 ativado {gm['ativacoes_x2']} vezes", T_SUB)],
        )

    def _draw_dash_tracks(self, rect, summ):
        """Acertos por trilha: medidor por trilha + resumo por mao."""
        self._panel(rect)
        x, y = rect.x + _px(18), rect.y
        self._text("Acertos por trilha", F_SMALL, T_SUB, midleft=(x, y + _py(22)))
        BAR_X, BAR_W, BAR_H = x + _px(30), _px(190), _py(14)
        for t, (hits, notes) in enumerate(summ["trilhas"]):
            ry = y + _py(62) + t * _py(34)
            dot = self._key_dot(t, _ps(9))
            self.screen.blit(dot, dot.get_rect(midleft=(x, ry)))
            color = TRACK_COLORS[t]
            bar = pygame.Rect(BAR_X, ry - BAR_H // 2, BAR_W, BAR_H)
            pygame.draw.rect(self.screen, tuple(int(c * 0.2) for c in color), bar,
                             border_radius=4)
            if not notes:
                self._text("sem notas", F_HINT, T_HINT, midleft=(bar.right + _px(10), ry))
                continue
            fill = int(BAR_W * hits / notes)
            if fill > 0:
                pygame.draw.rect(self.screen, color, (bar.x, bar.y, fill, BAR_H),
                                 border_radius=4)
            self._inline([(f"{100 * hits // notes}%", T_MAIN), (f" {hits}/{notes}", T_HINT)],
                         F_SMALL, midleft=(bar.right + _px(10), ry))
        parts = []
        for i, (name, hits, notes) in enumerate(summ["maos"]):
            val = f"{100 * hits // notes}%" if notes else "--"
            parts.append(f"{name} {val}")
        self._text("      ".join(parts), F_HINT, T_SUB, midleft=(x, y + _py(198)))

    def _draw_dash_timing(self, rect, summ):
        """Assincronia: media e desvio em texto + eixo da janela de acerto."""
        self._panel(rect)
        x, xr = rect.x + _px(18), rect.right - _px(18)
        self._text("Assincronia dos acertos", F_SMALL, T_SUB, midleft=(x, rect.y + _py(22)))

        lim_ms = SimulationEngine.HIT_WIN * FRAME_MS  # borda da janela (~183 ms)
        ax0, ax1 = rect.x + _px(28), rect.right - _px(28)
        ay = rect.y + _py(144)
        bh = _py(24)  # altura da faixa media +- desvio
        mean, std = summ["media_ms"], summ["desvio_ms"]

        def to_x(ms):
            ms = max(-lim_ms, min(lim_ms, ms))
            return int(ax0 + (ms + lim_ms) / (2 * lim_ms) * (ax1 - ax0))

        # Media e desvio-padrao: apoio, menores que score e taxa
        if mean is not None:
            sense = ("no tempo" if abs(mean) < 0.5 else
                     "adiantado" if mean < 0 else "atrasado")
            self._inline([(f"{mean:+.0f} ms", T_MAIN), f" media, {sense}"],
                         F_SMALL, T_SUB, midleft=(x, rect.y + _py(58)))
            self._inline([(f"{std:.0f} ms", T_MAIN), " desvio-padrao"],
                         F_SMALL, T_SUB, midright=(xr, rect.y + _py(58)))
            delta, color = self._dash_delta("media_ms", " ms")
            if delta:
                self._text(delta, F_HINT, color, midleft=(x, rect.y + _py(80)))
            delta, color = self._dash_delta("desvio_ms", " ms")
            if delta:
                self._text(delta, F_HINT, color, midright=(xr, rect.y + _py(80)))

        # Faixa media +- desvio, acima do eixo (nao cobre os rotulos)
        if mean is not None:
            band = pygame.Rect(to_x(mean - std), ay - bh, 0, bh)
            band.width = max(2, to_x(mean + std) - band.x)
            surf = pygame.Surface(band.size, pygame.SRCALPHA)
            surf.fill((*C_DATA, 70))
            self.screen.blit(surf, band.topleft)

        # Eixo e marcas (recessivos)
        pygame.draw.line(self.screen, PANEL_LINE, (ax0, ay), (ax1, ay), 2)
        for ms in (-150, -100, -50, 0, 50, 100, 150):
            tx = to_x(ms)
            h = _py(8) if ms == 0 else _py(4)
            pygame.draw.line(self.screen, T_HINT if ms == 0 else PANEL_LINE,
                             (tx, ay - h), (tx, ay + h), 2)
            self._text(f"{ms:+d}" if ms else "0", F_HINT, T_HINT, center=(tx, ay + _py(18)))
        self._text("adiantado", F_HINT, T_SUB, midleft=(ax0, ay + _py(44)))
        self._text("atrasado", F_HINT, T_SUB, midright=(ax1, ay + _py(44)))

        if mean is None:
            self._text("sem acertos nesta musica", F_SMALL, T_SUB,
                       center=(rect.centerx, ay - _py(40)))
            return
        # Marcador da media
        mx = to_x(mean)
        pygame.draw.line(self.screen, C_DATA, (mx, ay - bh - _py(4)), (mx, ay + _py(4)), 3)
        pygame.draw.circle(self.screen, BG_COLOR, (mx, ay - bh - _py(8)), _ps(7))
        pygame.draw.circle(self.screen, C_DATA, (mx, ay - bh - _py(8)), _ps(5))

    # ── Tela do nome do perfil (estado NOME, depois do fim de sessao) ─────────

    def _draw_name(self):
        """
        Nome do perfil em estilo arcade: 3 caixas grandes, a do cursor com
        borda laranja e seta. Estatica (sem piscar). START grava a sessao.
        """
        dash = self._dash
        if dash is None:  # primeiro quadro, antes de _update_session
            return
        self._draw_menu_bg()
        cx = SCREEN_W // 2
        self._title("QUEM JOGOU?")
        self._text("escolha o perfil para salvar a sessao", F_SMALL, T_SUB,
                   center=(cx, _py(94)))

        BOX_W, BOX_H, BOX_GAP = _px(96), _py(140), _px(18)
        bx0 = cx - (3 * BOX_W + 2 * BOX_GAP) // 2
        box_top = _py(150)
        for k, ch in enumerate(self.fpga_nome):
            r = pygame.Rect(bx0 + k * (BOX_W + BOX_GAP), box_top, BOX_W, BOX_H)
            active = k == self.fpga_cursor
            pygame.draw.rect(self.screen, PANEL_BG, r, border_radius=_ps(10))
            pygame.draw.rect(self.screen, ACCENT if active else PANEL_LINE, r,
                             4 if active else 2, border_radius=_ps(10))
            self._text(ch, 180, T_MAIN, center=(r.centerx, r.centery + _py(8)))
            if active:
                tip = r.bottom + _py(8)
                pygame.draw.polygon(self.screen, ACCENT, [
                    (r.centerx, tip), (r.centerx - _px(9), tip + _py(12)),
                    (r.centerx + _px(9), tip + _py(12)),
                ])

        # Comandos: quais botoes fazem o que
        r = _ps(11)
        self._inline(
            [*self._keys((0, 1), r), " trocar a letra          ",
             *self._keys((2, 3), r), " mudar de posicao"],
            F_BODY, T_SUB, center=(cx, _py(356)),
        )

        # Situacao do perfil e da comparacao com a sessao anterior
        prev, profile = dash["prev"], dash["profile"]
        if profile is None:
            lines = [("sem perfil: a sessao e salva sem nome e sem comparacao", T_SUB)]
        elif prev is not None:
            when = prev.get("data_hora", "")
            when = f"{when[8:10]}/{when[5:7]} {when[11:16]}" if len(when) >= 16 else when
            lines = [(f"ultima sessao em {when}   {prev.get('musica', '')}", T_SUB)]
            delta, color = self._dash_delta("taxa", " pp")
            if delta:
                lines.append((f"taxa de acerto {delta}", color))
        else:
            lines = [("primeira sessao deste perfil", T_SUB)]
        for i, (text, color) in enumerate(lines):
            self._text(text, F_SMALL, color, center=(cx, _py(420) + i * _py(26)))

        self._footer([(self._start_key, ACCENT), "salvar a sessao"])

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
            elif s == STATE_NAME:
                self._draw_name()

            self._draw_mode_tag()
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
        help="Modo simulacao: teclado (D/F/J/K=trilhas, SPACE=START, G=mao parada, H=mao mexendo), sem FPGA",
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
