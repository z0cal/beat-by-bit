"""Regera os charts .hex de todas as musicas (rtl/) com a dificuldade e o CHART_NOTE_FACTOR de interface.py."""
import os
import sys

# Roda sem janela/audio: importar interface.py chama pygame.init().
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interface import (_midi_to_chart, DIFFICULTY, CHART_NOTE_FACTOR, SONGS,
                       bpm_params, write_bpm_params, BPM_VH)

if __name__ == "__main__":
    print(f"[gerar_hex] Dificuldade: {DIFFICULTY}, fator de notas: {CHART_NOTE_FACTOR}")
    for song in SONGS:
        if song["available"]:
            _midi_to_chart(song, save_hex=True)
    write_bpm_params()
    print(f"[gerar_hex] {BPM_VH} gerado")
    for i, song in enumerate(SONGS):
        p = bpm_params(song)
        aviso = (f", PERIODO2 {p['periodo2']} NTROCA {p['ntroca']}"
                 + ("  AVISO: troca fora do tempo, arredondada" if p["nao_alinhado"] else "")
                 + ("  AVISO: >2 andamentos, usados so 2" if p["n_tempos"] > 2 else "")) if p["n_tempos"] > 1 else ""
        print(f"[bpm] slot {i} {song['title']}: {p['bpm']:.3f} BPM, periodo {p['periodo']}, fase {p['fase']}{aviso}")
