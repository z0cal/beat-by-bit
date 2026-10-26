"""Gera rtl/condado_curta.hex a partir do MIDI com a dificuldade configurada em interface.py."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from interface import _midi_to_chart, DIFFICULTY
print(f'[gerar_hex] Dificuldade: {DIFFICULTY}')
_midi_to_chart(save_hex=True)
