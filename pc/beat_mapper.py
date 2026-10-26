#!/usr/bin/env python3
"""
beat_mapper.py — Gera ROM de notas para Beat by Bit a partir de uma musica.

Analisa o audio offline e produz um arquivo .hex para ser carregado na FPGA
via $readmemh. Cada linha do .hex corresponde a um frame do jogo (60fps) e
contem um bitmask de 4 bits indicando quais trilhas spawnam uma nota.

Uso:
    python beat_mapper.py musica.ogg
    python beat_mapper.py musica.ogg --duration 60 --output song_notes.hex
    python beat_mapper.py musica.ogg --mode freq --preview
    python beat_mapper.py musica.ogg --mode roundrobin --sensitivity 0.6

Modos de distribuicao de notas nas trilhas (--mode):
    freq        Distribui por banda de frequencia (recomendado):
                  trilha 0 = graves  (<200 Hz)
                  trilha 1 = med-baixo (200-800 Hz)
                  trilha 2 = med-alto  (800-3000 Hz)
                  trilha 3 = agudos  (>3000 Hz)
    roundrobin  Distribui ciclicamente: 0, 1, 2, 3, 0, 1, ...
    random      Distribui aleatoriamente entre as trilhas

Dependencias:
    pip install librosa numpy soundfile
    pip install matplotlib          (opcional, para --preview)
"""

import argparse
import sys
import numpy as np

try:
    import librosa
except ImportError:
    print("[ERRO] librosa nao encontrado. Instale com: pip install librosa")
    sys.exit(1)

# ─── Parametros do jogo (devem bater com o Verilog) ──────────────────────────

GAME_FPS        = 60       # frames por segundo
NUM_LANES       = 4        # numero de trilhas
MIN_SPACING     = 25       # frames minimos entre notas na mesma trilha (~0.4s)
MAX_NOTES_FRAME = 2        # maximo de trilhas spawando no mesmo frame

# Bandas de frequencia para o modo 'freq' (Hz)
FREQ_BANDS = [
    (  20,  200),   # trilha 0 — graves (bumbo, baixo)
    ( 200,  800),   # trilha 1 — medios-baixos (snare, voz baixa)
    ( 800, 3000),   # trilha 2 — medios-altos (voz, guitarra)
    (3000, 16000),  # trilha 3 — agudos (chimbal, hi-hat)
]

# ─── Deteccao de onsets ───────────────────────────────────────────────────────

def load_audio(path, duration=None):
    """Carrega o audio e retorna (y, sr)."""
    print(f"[1/4] Carregando audio: {path}")
    y, sr = librosa.load(path, sr=None, mono=True, duration=duration)
    minutes = len(y) / sr / 60
    print(f"      Duracao: {len(y)/sr:.1f}s ({minutes:.1f} min) | Sample rate: {sr} Hz")
    return y, sr


def detect_onsets_global(y, sr, sensitivity):
    """
    Detecta onsets no sinal completo.
    sensitivity: 0.0 (pega tudo) a 1.0 (so os picos mais fortes).
    Retorna array de timestamps em segundos.
    """
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    # Normaliza e aplica limiar de sensibilidade
    onset_env = onset_env / (onset_env.max() + 1e-9)
    frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        units='time',
        pre_max=3, post_max=3,
        pre_avg=5, post_avg=5,
        delta=sensitivity * 0.5,
        wait=int(sr / 512 * MIN_SPACING / GAME_FPS),
    )
    return frames


def detect_onsets_by_band(y, sr, sensitivity):
    """
    Detecta onsets separadamente em 4 bandas de frequencia.
    Retorna lista de 4 arrays de timestamps, um por trilha.
    """
    hop_length = 512
    S = np.abs(librosa.stft(y, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    lane_times = []
    for lo, hi in FREQ_BANDS:
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            lane_times.append(np.array([]))
            continue

        band_S = S[mask, :]
        onset_env = librosa.onset.onset_strength(
            S=librosa.amplitude_to_db(band_S, ref=np.max),
            sr=sr,
            hop_length=hop_length,
            aggregate=np.median,
        )
        onset_env = onset_env / (onset_env.max() + 1e-9)
        frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            units='time',
            pre_max=2, post_max=2,
            pre_avg=4, post_avg=4,
            delta=sensitivity * 0.4,
            wait=int(sr / hop_length * MIN_SPACING / GAME_FPS),
        )
        lane_times.append(frames)

    return lane_times


# ─── Construcao da ROM ────────────────────────────────────────────────────────

def timestamps_to_bitmask(lane_times, game_duration):
    """
    Converte timestamps por trilha em array de bitmasks por frame.
    Retorna numpy array shape (num_frames,) dtype uint8.
    """
    num_frames = int(game_duration * GAME_FPS)
    rom = np.zeros(num_frames, dtype=np.uint8)

    last_spawn = [-MIN_SPACING] * NUM_LANES  # ultimo frame que cada trilha spawnou

    for lane, times in enumerate(lane_times):
        for t in sorted(times):
            frame = int(t * GAME_FPS)
            if frame >= num_frames:
                continue
            if frame - last_spawn[lane] < MIN_SPACING:
                continue  # muito perto da nota anterior nessa trilha
            # Limita notas simultaneas por frame
            if bin(rom[frame]).count('1') >= MAX_NOTES_FRAME:
                # Tenta frame adjacente
                for offset in [1, -1, 2, -2]:
                    adj = frame + offset
                    if 0 <= adj < num_frames and bin(rom[adj]).count('1') < MAX_NOTES_FRAME:
                        frame = adj
                        break
                else:
                    continue  # frame cheio, descarta
            rom[frame] |= (1 << lane)
            last_spawn[lane] = frame

    return rom


def build_rom_roundrobin(times_all, game_duration):
    """Distribui todos os onsets nas trilhas em round-robin."""
    num_frames = int(game_duration * GAME_FPS)
    rom = np.zeros(num_frames, dtype=np.uint8)
    last_spawn = [-MIN_SPACING] * NUM_LANES
    lane = 0

    for t in sorted(times_all):
        frame = int(t * GAME_FPS)
        if frame >= num_frames:
            continue
        # Procura trilha disponivel a partir da atual
        for attempt in range(NUM_LANES):
            l = (lane + attempt) % NUM_LANES
            if frame - last_spawn[l] >= MIN_SPACING:
                if bin(rom[frame]).count('1') < MAX_NOTES_FRAME:
                    rom[frame] |= (1 << l)
                    last_spawn[l] = frame
                    lane = (l + 1) % NUM_LANES
                    break

    return rom


def build_rom_random(times_all, game_duration, seed=42):
    """Distribui todos os onsets aleatoriamente nas trilhas."""
    rng = np.random.default_rng(seed)
    num_frames = int(game_duration * GAME_FPS)
    rom = np.zeros(num_frames, dtype=np.uint8)
    last_spawn = [-MIN_SPACING] * NUM_LANES

    for t in sorted(times_all):
        frame = int(t * GAME_FPS)
        if frame >= num_frames:
            continue
        candidates = [l for l in range(NUM_LANES)
                      if frame - last_spawn[l] >= MIN_SPACING]
        if not candidates:
            continue
        if bin(rom[frame]).count('1') >= MAX_NOTES_FRAME:
            continue
        lane = int(rng.choice(candidates))
        rom[frame] |= (1 << lane)
        last_spawn[lane] = frame

    return rom


# ─── Exportacao ───────────────────────────────────────────────────────────────

def save_hex(rom, output_path):
    """
    Salva a ROM como arquivo .hex compativel com $readmemh do Verilog.
    Cada linha: um byte em hexadecimal (ex: '0A').
    """
    with open(output_path, 'w') as f:
        for byte in rom:
            f.write(f'{byte:02X}\n')
    print(f"[4/4] ROM salva em: {output_path}")
    print(f"      {len(rom)} linhas | {sum(1 for b in rom if b) } frames com nota")


def print_stats(rom):
    """Exibe estatisticas da ROM gerada."""
    total = sum(1 for b in rom if b)
    per_lane = [sum(1 for b in rom if b & (1 << i)) for i in range(NUM_LANES)]
    duration_s = len(rom) / GAME_FPS
    print()
    print("── Estatisticas ─────────────────────────────")
    print(f"   Duracao:        {duration_s:.0f}s ({len(rom)} frames)")
    print(f"   Total de notas: {total}  (~{total/duration_s:.1f} notas/s)")
    print(f"   Trilha 0 (graves):    {per_lane[0]:4d} notas")
    print(f"   Trilha 1 (med-baixo): {per_lane[1]:4d} notas")
    print(f"   Trilha 2 (med-alto):  {per_lane[2]:4d} notas")
    print(f"   Trilha 3 (agudos):    {per_lane[3]:4d} notas")
    print(f"   Densidade media: {total/duration_s*60:.0f} notas/min")
    print("─────────────────────────────────────────────")
    print()


# ─── Preview (opcional) ───────────────────────────────────────────────────────

def show_preview(rom, y, sr):
    """Exibe grafico com a posicao das notas ao longo do tempo."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[AVISO] matplotlib nao encontrado. Instale com: pip install matplotlib")
        return

    fig, axes = plt.subplots(5, 1, figsize=(14, 8), sharex=True)
    fig.suptitle('Beat Mapper — Preview da ROM', fontsize=13, fontweight='bold')

    # Waveform
    times_audio = np.linspace(0, len(y) / sr, len(y))
    axes[0].plot(times_audio, y, color='#4488ff', linewidth=0.4, alpha=0.7)
    axes[0].set_ylabel('Audio', fontsize=8)
    axes[0].set_ylim(-1, 1)

    # Uma faixa por trilha
    colors = ['#FFD700', '#00B4FF', '#00FF64', '#FF3232']
    labels = ['T0 graves', 'T1 med-baixo', 'T2 med-alto', 'T3 agudos']
    for i in range(NUM_LANES):
        ax = axes[i + 1]
        note_times = [f / GAME_FPS for f in range(len(rom)) if rom[f] & (1 << i)]
        ax.vlines(note_times, 0, 1, color=colors[i], linewidth=1.2, alpha=0.85)
        ax.set_ylabel(labels[i], fontsize=8, color=colors[i])
        ax.set_ylim(0, 1.2)
        ax.set_yticks([])
        ax.set_facecolor('#0a0a14')

    axes[-1].set_xlabel('Tempo (s)')
    plt.tight_layout()
    plt.show()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Beat Mapper — Gera ROM de notas para Beat by Bit',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('audio',
        help='Arquivo de audio (.ogg, .mp3, .wav, .flac)')
    parser.add_argument('--duration', type=float, default=60.0,
        help='Duracao do jogo em segundos (padrao: 60)')
    parser.add_argument('--output', default='song_notes.hex',
        help='Arquivo de saida .hex (padrao: song_notes.hex)')
    parser.add_argument('--mode', choices=['freq', 'roundrobin', 'random'],
        default='freq',
        help='Modo de distribuicao nas trilhas (padrao: freq)')
    parser.add_argument('--sensitivity', type=float, default=0.35,
        help='Sensibilidade da deteccao 0.0-1.0 (padrao: 0.35, maior = menos notas)')
    parser.add_argument('--seed', type=int, default=42,
        help='Semente aleatoria para modo random (padrao: 42)')
    parser.add_argument('--preview', action='store_true',
        help='Exibe grafico do mapeamento antes de salvar')
    args = parser.parse_args()

    if not 0.0 <= args.sensitivity <= 1.0:
        print("[ERRO] --sensitivity deve estar entre 0.0 e 1.0")
        sys.exit(1)

    # 1. Carregar audio
    y, sr = load_audio(args.audio, duration=args.duration)
    actual_duration = min(args.duration, len(y) / sr)

    # 2. Detectar onsets
    print(f"[2/4] Detectando onsets (modo={args.mode}, sensibilidade={args.sensitivity})...")
    if args.mode == 'freq':
        lane_times = detect_onsets_by_band(y, sr, args.sensitivity)
        total_detected = sum(len(t) for t in lane_times)
        print(f"      Onsets detectados por banda: "
              f"{[len(t) for t in lane_times]} = {total_detected} total")
        rom = timestamps_to_bitmask(lane_times, actual_duration)
    else:
        times_all = detect_onsets_global(y, sr, args.sensitivity)
        print(f"      Onsets detectados: {len(times_all)}")
        if args.mode == 'roundrobin':
            rom = build_rom_roundrobin(times_all, actual_duration)
        else:
            rom = build_rom_random(times_all, actual_duration, seed=args.seed)

    # 3. Estatisticas
    print("[3/4] Processando ROM...")
    print_stats(rom)

    # Preview opcional
    if args.preview:
        show_preview(rom, y, sr)

    # 4. Salvar
    save_hex(rom, args.output)

    print()
    print("Verilog — adicione ao fluxo_dados.v:")
    print("─────────────────────────────────────────────────────────────")
    print(f'  reg [3:0] song_rom [0:{len(rom)-1}];')
    print(f'  initial $readmemh("{args.output}", song_rom);')
    print(f'  wire [3:0] spawn_mask = song_rom[frame_counter];')
    print("─────────────────────────────────────────────────────────────")
    print()


if __name__ == '__main__':
    main()
