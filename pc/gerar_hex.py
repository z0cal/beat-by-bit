"""Regera os charts .hex de todas as musicas (rtl/) com a dificuldade e o CHART_NOTE_FACTOR de interface.py."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from interface import _midi_to_chart, DIFFICULTY, CHART_NOTE_FACTOR, SONGS
print(f'[gerar_hex] Dificuldade: {DIFFICULTY}, fator de notas: {CHART_NOTE_FACTOR}')
for song in SONGS:
    if song["available"]:
        _midi_to_chart(song, save_hex=True)
