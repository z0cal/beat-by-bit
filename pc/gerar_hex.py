"""Regera os charts .hex de todas as musicas (rtl/) com a dificuldade e o CHART_NOTE_FACTOR de interface.py."""
import os
import sys

# Roda sem janela/audio: importar interface.py chama pygame.init().
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interface import _midi_to_chart, DIFFICULTY, CHART_NOTE_FACTOR, SONGS

if __name__ == "__main__":
    print(f"[gerar_hex] Dificuldade: {DIFFICULTY}, fator de notas: {CHART_NOTE_FACTOR}")
    for song in SONGS:
        if song["available"]:
            _midi_to_chart(song, save_hex=True)
