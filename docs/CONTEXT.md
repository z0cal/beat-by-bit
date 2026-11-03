# Beat by Bit — Contextualização do Projeto

Projeto de laboratório digital (USP, ELE3323). Jogo de ritmo estilo Guitar Hero rodando em FPGA DE0-CV + interface Python.

Layout: `rtl/` (Verilog + Quartus + charts `.hex`), `tb/` (testbenches), `pc/` (Python + assets), `songs/` (MIDI + WAV), `docs/`.

---

## Arquitetura

```
FPGA (Verilog) ──UART 115200 baud──► Python (Pygame)
  projeto.v           (22 bytes/frame)    interface.py
  unidade_controle.v                      SimulationEngine (--sim)
  fluxo_dados.v
  packet_sender.v
```

- **FPGA**: lógica do jogo (FSM, notas, score). Envia pacotes UART de 22 bytes a 60fps.
- **Python**: renderiza o que recebe via UART. Em `--sim`, roda a FSM localmente sem FPGA.
- **Teclas (sim)**: D/F/J/K = trilhas 0-3 | SPACE = START/PAUSE | ESC = voltar (pausa)

---

## Músicas

| Slot | Tecla/Botão | Título | WAV | Duração | ROM_DEPTH |
|------|-------------|--------|-----|---------|-----------|
| 0 | D / Btn0 | Concerning Hobbits | `songs/condado/Condado_curta.wav` | 63.86s | 3831 |
| 1 | F / Btn1 | Song of Storms | `songs/ocarina/Ocarina_curta.wav` | 70.33s | 4219 |
| 2 | J / Btn2 | Power Rangers | `songs/power_rangers/Power_Rangers.wav` | 64.67s | 3880 |

Arquivos MIDI: `songs/condado/condado_curta.mid`, `songs/ocarina/Ocarina_curta.mid`, `songs/power_rangers/power_rangers.mid`, `songs/pokemon/opening_pokemon.mid`

**Todos os MIDIs são tipo 1 (multi-track)** — notas estão em `track[1]`. O código usa `for msg in mid` (mido itera sobre MidiFile diretamente, retornando `msg.time` em **segundos**, não ticks). Não usar `mid.tracks[0]` (vazio) nem `mido.tick2second()` (desnecessário).

Charts `.hex` gerados automaticamente em `rtl/` no startup do SimulationEngine: `condado_curta.hex`, `ocarina_curta.hex`, `power_rangers.hex`. Esses arquivos são necessários para o Quartus (`$readmemh`).

**Algoritmo de geração de chart** (`_midi_to_chart`):
- Mapeamento de pitch → lane por **quartis** do MIDI atual (não thresholds fixos) — distribui ~25% das notas em cada lane
- Agrupamento por frame + escolha do lane **menos recentemente usado** ao resolver colisões simultâneas — garante que todos os 4 lanes recebam notas
- Usa apenas `_MIN_NOTE_SPACING` (espaçamento por trilha) e `_MAX_SIMULTANEOUS`; `_GLOBAL_SPACING` não é mais aplicado no gerador de chart
- `_DIFF_PRESETS["facil"] = (50, 18, 1)` — min_lane=50 (≈0.83s entre notas na mesma lane), max_sim=1

---

## `interface.py` — Estrutura principal

```
linhas 28-35   : _AUDIO_BUFFER=16384 + pygame.mixer.pre_init(44100,-16,2,_AUDIO_BUFFER) + pygame.init()
                 pre_init ANTES de pygame.init() — evita double-init
linhas 52-55   : FPS=60, AUDIO_LATENCY_FRAMES=round(_AUDIO_BUFFER/44100*FPS) ≈ 22
                 Compensa latencia do buffer: notas spawnam AUDIO_LATENCY_FRAMES mais tarde
linhas 56-74   : HIT_AGE = HIT_Y // NOTE_SPEED  ← float! usar int(HIT_AGE) em índices
linhas 100-140 : SONG_DURATION_MS, ROM_DEPTH, SONGS[], PACKET_SIZE
linhas 265-350 : _midi_to_chart(song_config=None, save_hex=False)
linhas 328-490 : class SimulationEngine
                   __init__: pré-gera self._charts[3], self._selected_song=0
                   _fsm: btn[0/1/2] em SONG_SELECT setam _selected_song
                   _start_countdown: carrega self._charts[_selected_song]
linhas 494-600 : class Renderer.__init__ + _init_music + _update_music
                   _init_music: usa SONGS[self._selected_song]["audio"]
linhas 810-840 : _process_sim: sincroniza _selected_song, chama _init_music se mudou
linha  1090    : barra de progresso usa len(self.sim._chart)
linhas 1170-1255: _draw_song_select, _draw_countdown
linha  1267    : _draw_countdown usa SONGS[self._selected_song]["title"]
```

---

## `fluxo_dados.v` — Estrutura principal

```
linhas 83-110  : debounce (btn0/1/2 reset=reset apenas; btn3 reset=reset|limpaR)
                 song_ok_pulso = btn_pulse[0|1|2]
                 song_id_reg [1:0] captura qual botão foi pressionado
linhas 154-197 : 3 ROMs: chart_rom0 (condado_curta.hex), chart_rom1, chart_rom2
                 current_rom_depth muxado por song_id_reg
                 frame_counter limitado por current_rom_depth-1
linhas 230-255 : chart_spawn_r: mux de ROM baseado em song_id_reg
```

`unidade_controle.v` e `projeto.v`: sem alterações recentes. UC tem `song_ok` (1-bit, qualquer dos 3 botões).

---

## Protocolo UART (FPGA → Python)

Pacote de 22 bytes a 60fps:
```
[0]  = 0x55 (header)
[1]  = state (4 bits baixos): 0=idle,1=countdown,2=play,3=pause,4=win,5=lose,6=select
[2]  = countdown_sec (3,2,1,0)
[3..14] = ages das notas: 4 trilhas × 3 slots × 1 byte (0xFF = slot vazio)
[15..16] = score (16 bits big-endian)
[17] = misses
[18] = combo
[19..20] = frame_counter (14 bits: [19]bits13-8, [20]bits7-0)
[21] = 0xAA (footer)
```

---

## Como rodar

```bash
python pc/interface.py --sim          # modo simulação (sem FPGA)
python pc/interface.py --port COM3    # modo FPGA (Windows)
python pc/interface.py --port /dev/ttyUSB0  # modo FPGA (Linux)
```

---

## Dependências Python

`pygame`, `mido`, `pyserial` (opcional, apenas modo FPGA)

---

## Dificuldade

Configurada em `interface.py` linha ~243:
```python
DIFFICULTY = "facil"  # 'facil', 'medio', 'dificil'
```
Presets `_DIFF_PRESETS`: `(min_lane_spacing, global_spacing, max_simultaneous)`.

---

## Arquivos Verilog (`rtl/`)

| Arquivo | Papel |
|---------|-------|
| `projeto.v` | Top-level, instancia UC + FD + packet_sender |
| `unidade_controle.v` | FSM (idle→select→countdown→play→pause/win/lose) |
| `fluxo_dados.v` | Lógica do jogo: ROMs, frame counter, note tracks, score |
| `packet_sender.v` | Serializa estado em pacote UART |
| `note_track.v` | 1 trilha com 3 slots de nota |
| `debounce_pulse.v` | Debounce + detecção de borda |
| `contador_m.v` | Contador parametrizável |

Testbenches (`tb/`): `tb_fd.v`, `tb_uc_ganha.v`, `tb_uc_perde.v`, `tb_countdown_transition.v`, etc.

---

## Hardware e pinagem (DE0-CV)

Cola de montagem com desenho do conector: `docs/cola/cola_beat_by_bit.pdf` (fonte `.tex` ao lado).

Tudo no **JP1 (GPIO_0)**; JP2 livre. Cabeçalho de 40 pinos: pinos 1-10 = GPIO[0..9], 11 = 5 V,
12 = GND, 13-28 = GPIO[10..25], 29 = 3,3 V, 30 = GND, 31-40 = GPIO[26..35].

| Sinal | Pino JP1 | Pino FPGA | Liga em |
|-------|----------|-----------|---------|
| `uart_tx_out` | 1 | N16 | RXD do CP2102 (GND do CP2102 no pino 12) |
| `uart_rx_in` | 2 | B16 | TXD do CP2102 (ACK do PC) |
| `reset` | 16 | T22 | botão → GND + pull-up 10 kΩ (apertado = reset) |
| `botoes[4]` START/PAUSE | 36 | T20 | botão → GND + pull-up 10 kΩ |
| `botoes[1]` | 37 | T19 | idem |
| `botoes[3]` | 38 | T18 | idem |
| `botoes[0]` | 39 | T17 | idem |
| `botoes[2]` | 40 | T15 | idem |
| 3,3 V / GND | 29 / 30 | — | trilhos + / − dos pull-ups e botões |

- Pull-ups são **externos** (o `.qsf` não liga o pull-up interno): solto = 1, apertado = 0.
- `clock` = CLOCK_50 (M9). `HEX0` = estado da FSM, `HEX1` = `botoes[3:0]` registrados na jogada.
- LEDR0..4 = `leds_pulsos` = {start, fim_contagem, fim_tempo, link_ok, jogada_feita} na ordem
  LEDR0 = start ... LEDR4 = jogada_feita. LEDR5..9 = `botoes[0..4]` crus (aceso = solto).
- **LEDR3 = link serial ok** (`uart_link.v`): o Python manda `0xA5` a cada 0,25 s enquanto recebe
  pacotes válidos; a FPGA (`uart_rx`) mantém o LED aceso até 1 s após o último ACK. Aceso = caminho
  completo FPGA → RXD → Python → TXD → FPGA ok. Apagado com o jogo rodando = pino 1 no TXD (invertido),
  falta o fio do pino 2, GND solto ou Python sem a porta aberta.

### Pegadinhas conhecidas (verificado em 27/10/2026)

- **Segmentos dos displays** (corrigido em 27/10/2026): o `.qsf` tinha `db_estado`/`db_jogada`
  com índices `[6:0]` invertidos. Agora `[0]` = segmento `a` (U21 em HEX0, AA20 em HEX1), que é a
  ordem do `hexa7seg` (`0 = 7'b1000000`, bit 6 = `g`).
- **Jogada conta ao soltar**: `debounce_pulse` dispara na subida do sinal; com pull-up, a subida
  é quando o botão é solto. Lockout de 9 M ciclos (180 ms).
- **LEDR0, 2, 4 nunca acendem visivelmente** (pulsos de 1 ciclo). `perdeu = 1'b0` (sem limite
  de erros, então END_LOSE nunca ocorre); por isso LEDR3 foi reaproveitado para o link serial. **LEDR1** fica aceso em
  PLAY/PAUSE (o contador do countdown para em 179).
- Na **PAUSE**, qualquer botão 0..3 (`song_ok`) volta para IDLE, não só o botão 0.
- **`tb_countdown_transition` falha** desde antes de 27/10/2026 (estado fica em 0); as demais passam.
- `SerialReader` (Python) guarda `self._stop` como `Event`, o que sobrescreve `Thread._stop`:
  `join()` quebra com TypeError. O jogo não chama `join`, então não aparece em uso.

## Gravação (Quartus e fluxo aberto)

- Quartus Prime Lite 25.1 instalado em `~/intelFPGA_lite/25.1std` (só Quartus + Cyclone V, sem
  sudo, via `qinst-lite-linux-25.1std-1129.run --cli`). O `Makefile` acha `quartus/bin` sozinho
  (`QUARTUS_BIN`), sem precisar de PATH. Links antigos `downloads.intel.com/akdlm/...` dão 404 desde a
  separação Intel/Altera; o site da Altera bloqueia download automático (baixar pelo navegador).
- `make program`: Quartus (`quartus_map/fit/asm` → `.sof`) + `quartus_pgm`. `make jtag`: `jtagconfig`.
- `make program-oss`: Yosys (`synth_intel_alm`) → `.yosys/beat_by_bit.json` →
  `~/.local/bin/nextpnr-mistral` (pinagem lida do `.qsf`) → `.yosys/beat_by_bit.rbf` →
  `openFPGALoader -c usb-blaster`. Incremental; grava na SRAM (volátil). `make jtag-oss`: detecção.
- **Suspeita no fluxo aberto (27/10/2026):** com `program-oss`, a UART FPGA→PC não entregou nenhum
  byte (nem com a FPGA virando um fio pino 2→pino 1), enquanto PC→FPGA (pino 2, LEDR3) funcionou.
  `uart_tx_out` (N16) é a única saída no lado direito do chip (`IO.54.x`); suspeita de saída
  nesse banco no Mistral. Por isso `make program` voltou para o Quartus.
- Logs do fluxo aberto: `.yosys/yosys.log`, `.yosys/nextpnr.log` (`grep "Constraining IO"`).
