"""
Verifica os charts (rtl/*.hex) e o rtl/bpm_params.vh gerados por gerar_hex.py.

Por musica:
  - formato: uma linha de 2 digitos hex, profundidade = int(duracao * 60);
  - notas: apenas trilhas de botao (bits 0..3), zero gestos (bit 4 sempre 0, nenhum bit
    acima do 3 em uso) e toda nota julgada antes do fim (frame + 153 < total);
  - metronomo: PERIODO_n/FASE_n do .vh batem com o MIDI e a deriva (acumular o periodo
    inteiro em ciclos vs. o periodo exato do MIDI) fica < 1 quadro ate o fim da musica.
    Se o MIDI muda de andamento, o desvio contra o mapa de andamentos real vira AVISO.

Sai com codigo != 0 se algo falhar.
"""
import os
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mido

from interface import (
    BPM_VH, CLOCK_HZ, FPS, FRAME_CYCLES, SONGS, _LANE_SLOT_SPAN, _abs_events,
)


def le_hex(path):
    vals = []
    for i, line in enumerate(open(path), 1):
        t = line.rstrip("\n")
        if not re.fullmatch(r"[0-9A-Fa-f]{2}", t):
            raise ValueError(f"{path}:{i}: linha invalida {t!r} (esperado 2 digitos hex)")
        vals.append(int(t, 16))
    return vals


def le_vh(path):
    d = {}
    for m in re.finditer(r"`define\s+(PERIODO|FASE)_(\d+)\s+32'd(\d+)", open(path).read()):
        d[(m.group(1), int(m.group(2)))] = int(m.group(3))
    for m in re.finditer(r"`define\s+(PERIODO2|NTROCA)_(\d+)\s+32'(?:d(\d+)|h([0-9A-Fa-f]+))", open(path).read()):
        d[(m.group(1), int(m.group(2)))] = int(m.group(3)) if m.group(3) else int(m.group(4), 16)
    return d


def tempo_map(midi_path):
    """[(tick, tempo_us)] ordenado; 120 BPM se vazio. Retorna tambem ticks_per_beat."""
    mid = mido.MidiFile(midi_path)
    tm = [(tk, m.tempo) for tk, m in _abs_events(mid) if m.type == "set_tempo"]
    return (tm or [(0, 500_000)]), mid.ticks_per_beat


def beat_seconds(tm, tpb, k):
    """Instante (s) do tempo k (tick k*tpb) pelo mapa de andamentos real."""
    target, t, last_tk, last_tempo = k * tpb, 0.0, 0, tm[0][1]
    for tk, tempo in tm:
        if tk >= target:
            break
        t += (tk - last_tk) * last_tempo / (tpb * 1e6)
        last_tk, last_tempo = tk, tempo
    return t + (target - last_tk) * last_tempo / (tpb * 1e6)


def main():
    falhas = []
    vh = le_vh(BPM_VH) if os.path.exists(BPM_VH) else {}
    if not vh:
        falhas.append(f"{BPM_VH} ausente ou sem PERIODO_n/FASE_n")

    for slot, song in enumerate(SONGS):
        nome = f"[{slot}] {song['title']}"
        erros, avisos = [], []
        rom = le_hex(song["hex"])
        total = len(rom)
        if total != int(song["duration"] * FPS):
            erros.append(f"profundidade {total} != int({song['duration']}*60)")

        botoes = [f for f, v in enumerate(rom) if v & 0xF]
        gestos = [f for f, v in enumerate(rom) if v & 0x10]
        if gestos:
            erros.append(f"bit 4 (gesto) presente em {len(gestos)} quadros: {gestos[:5]}")
        if any(v & ~0x0F for v in rom):
            erros.append("bits acima do 3 em uso")
        if not botoes:
            erros.append("nenhuma nota de botao")
        for b in botoes:
            if b + _LANE_SLOT_SPAN + 1 >= total:
                erros.append(f"nota em {b} nao e julgada antes do fim (frame + 153 >= {total})")

        # Metronomo
        periodo, fase = vh.get(("PERIODO", slot)), vh.get(("FASE", slot))
        info = "sem PERIODO/FASE"
        if periodo is None or fase is None:
            erros.append("PERIODO/FASE ausentes no .vh")
        else:
            tm, tpb = tempo_map(song["midi"])
            exato = tm[0][1] * CLOCK_HZ / 1e6  # periodo exato do 1o andamento, em ciclos
            if abs(periodo - exato) >= 1:
                erros.append(f"PERIODO {periodo} difere do MIDI {exato}")
            if not 0 <= fase < periodo:
                erros.append(f"FASE {fase} fora de [0, PERIODO)")
            periodo2, ntroca = vh.get(("PERIODO2", slot)), vh.get(("NTROCA", slot))
            if periodo2 is None or ntroca is None:
                erros.append("PERIODO2/NTROCA ausentes no .vh")
                periodo2, ntroca = periodo, 0xFFFFFFFF
            dur_s = song["duration"]
            # Tempo k segundo o .vh (inteiros) e segundo o mapa de andamentos do MIDI
            def vh_s(k):
                if k < ntroca:
                    return (fase + k * periodo) / CLOCK_HZ
                return (fase + ntroca * periodo + (k - ntroca) * periodo2) / CLOCK_HZ

            n_beats, k = 0, 0
            while beat_seconds(tm, tpb, k) <= dur_s:
                k += 1
            n_beats = k
            deriva = max(
                abs(vh_s(k) - (beat_seconds(tm, tpb, k) + fase / CLOCK_HZ)) for k in range(n_beats)
            ) * FPS
            if deriva >= 1:
                erros.append(f"deriva do metronomo {deriva:.3f} quadros >= 1 contra o MIDI real")
            if len(tm) > 1 and ntroca == 0xFFFFFFFF:
                erros.append("MIDI com 2 andamentos mas NTROCA desligado")
            if len(tm) > 2:
                avisos.append(f"{len(tm)} andamentos no MIDI; o .vh usa so os 2 primeiros")
            if len(tm) > 1 and tm[1][0] % tpb:
                avisos.append("troca de andamento fora de um tempo (arredondada)")
            troca = f", troca no tempo {ntroca} para {60e6 / (periodo2 / 50):.2f} BPM" if ntroca != 0xFFFFFFFF else ""
            info = (f"{60e6 / tm[0][1]:.2f} BPM, PERIODO {periodo}, FASE {fase} "
                    f"({fase / FRAME_CYCLES:.2f} quadros), PERIODO2 {periodo2}, NTROCA {ntroca}{troca}, "
                    f"{n_beats} tempos, deriva contra o MIDI real {deriva:.4f} quadros")

        print(f"{nome}: {total} quadros, {len(botoes)} notas de botao, {len(gestos)} gestos")
        print(f"    metronomo: {info}")
        for a in avisos:
            print(f"    AVISO: {a}")
        for e in erros:
            print(f"    ERRO: {e}")
            falhas.append(f"{nome}: {e}")

    print("FALHOU" if falhas else "OK")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
