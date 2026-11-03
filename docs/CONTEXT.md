# Beat by Bit — Contextualização do Projeto

Projeto de laboratório digital (USP, ELE3323). Jogo de ritmo estilo Guitar Hero rodando em FPGA DE0-CV + interface Python.

Layout: `rtl/` (Verilog + Quartus + charts `.hex`), `tb/` (testbenches), `pc/` (Python + assets), `songs/` (MIDI + WAV), `docs/`.

---

## Arquitetura

```
FPGA (Verilog) ──UART 115200 baud──► Python (Pygame)
  projeto.v           (46 bytes/frame)    interface.py
  unidade_controle.v                      SimulationEngine (--sim)
  fluxo_dados.v
  packet_sender.v
```

- **FPGA**: lógica do jogo (FSM, notas, score) e métricas da sessão. Envia pacotes UART de 46
  bytes a 60fps.
- **Python**: renderiza o que recebe via UART. Em `--sim`, roda a FSM localmente sem FPGA.
- **Teclas (sim)**: D/F/J/K = trilhas 0-3 | SPACE = START/PAUSE | ESC = voltar (pausa)
- **Configurações de som (estado SETTINGS = 7)**: na tela inicial, qualquer botão de trilha
  abre o atenuador de frequências; START volta. Lá, cada botão avança o nível da sua faixa
  (0 → −6 → −12 → −18 dB → 0): D = graves (< 250 Hz), F = médios (250 Hz–2 kHz),
  J = agudos (2–6 kHz), K = brilho (> 6 kHz). A FPGA guarda os níveis (`eq_config.v`, zeram
  só no reset) e os envia no pacote; o PC filtra o WAV por FFT (`_eq_wav_bytes`) e toca uma
  prévia de 10 s em loop enquanto o jogador ajusta.
- **Dashboard de fim de sessão (DASHBOARD_SESSAO)**: ao fim da música (WIN/LOSE) o PC mostra
  taxa de acerto, assincronia média e desvio-padrão (ms), maior combo, acertos por trilha e por
  mão (D+F, J+K) e a variação em relação à sessão anterior do mesmo pseudônimo. Gesto e
  metrônomo aparecem como "ainda não disponível". A tela é estática (sem piscar).
  - **Métricas na FPGA** (`metricas.v`, dentro do `fluxo_dados`): a cada acerto,
    `e = age − 130` quadros (negativo = adiantado); acumula N, Σe, Σ|e|, Σe², notas julgadas e
    acertos por trilha (12 bits) e o maior combo. Zera em COUNTDOWN; congela em PAUSE/END/IDLE.
    O PC calcula média = Σe/N e desvio = √(Σe²/N − média²), × 16,7 ms.
  - **Taxa nos displays (TAXA_HEX)**: no END, HEX2–HEX0 mostram ⌊100·N/notas⌋ (divisor
    sequencial `taxa_acerto.v`), sem zeros à esquerda; fora do END, HEX0 = estado,
    HEX1 = jogada, HEX2 apagado. Porta nova `db_hex2` no top (pinos Y19…AB21, mesma ordem
    de bits do HEX0/HEX1 — a ordem dos pinos HEX ainda é item de bancada).
  - **Histórico local**: `--pseudonimo NOME` (só letras, dígitos, `_`, `-`; nunca o nome da
    criança). Cada música terminada vira uma linha em `sessoes/sessoes.csv` (`--historico` muda
    o caminho; a pasta está no `.gitignore`). Falha de disco só gera aviso. É a base do MQTT
    da Semana 4 (mesmos campos, `session_row`). Se a interface abrir com a música já no fim,
    a sessão não é gravada.

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

**Algoritmo de geração de chart** (`_midi_to_chart`, regerar com `uv run python pc/gerar_hex.py`):
- **Foco na melodia:** cada música declara em `SONGS` os canais MIDI da melodia (`"melody"`).
  Em acordes, fica só a nota mais aguda (skyline). Quando a melodia some por mais de 2,5 s, a
  voz mais aguda do acompanhamento preenche a lacuna. A bateria (canal 9) nunca entra.
- **Trilha pela altura:** quartis das alturas que levam empates em conta (a melodia "sobe" para a
  direita). Se a trilha preferida estiver ocupada, a nota vai para a vizinha livre mais próxima.
- **Quantidade:** `CHART_NOTE_FACTOR` (0,65) × `ref_notes` (notas do chart antigo). As notas
  entram por prioridade: cabeça de compasso > tempo > colcheia; melodia antes de preenchimento;
  notas longas antes das curtas.
- **Restrições:** espaçamento global e por trilha do preset de dificuldade, e 3 slots por trilha
  (quaisquer 4 notas seguidas na mesma trilha ocupam ≥ 152 frames).
- Quando a melodia não tem notas jogáveis suficientes no espaçamento do preset, o chart fica abaixo
  do alvo (Hobbits e Pokémon no "fácil").
- `_DIFF_PRESETS["facil"] = (60, 30, 1)`: 60 frames (1 s) entre notas da mesma trilha e 30
  (0,5 s) entre quaisquer notas; max_sim não se aplica (a melodia é monofônica).
- **Toda nota é julgada antes do fim:** só entram notas com `frame + 153 < total_frames`, para
  acertar ou escapar antes do END (senão ficariam fora das métricas). Contagens atuais:
  74 / 57 / 86 / 67.
- `HIT_AGE = 130` (centro da janela, igual ao `HIT_CENTER` do `note_track.v`). Antes era
  `HIT_Y // NOTE_SPEED` = 129.0 por erro de ponto flutuante, o que deslocava os charts 1 quadro
  e enviesaria a assincronia em −17 ms.

**Sincronia chart × áudio:**
- Os WAVs são renderizações dos MIDIs: alinhados, sem deriva (medido por correlação de onsets).
- No `--sim`, a simulação avança pelo relógio de parede (`time.perf_counter`), 1 passo a cada
  1/60 s, como o `frame_tick` da FPGA. Não conta frames renderizados: `clock.tick(60)` roda a
  ~59 fps e as notas atrasavam ~1 s ao fim da música.
- `--audio-offset MS` compensa latência extra da saída de áudio (ex.: 200 para fone Bluetooth;
  negativo se as notas chegam depois do som). O buffer do mixer já é compensado no chart por
  `AUDIO_LATENCY_FRAMES`.

---

## `interface.py` — Estrutura principal

```
linhas 28-35   : _AUDIO_BUFFER=16384 + pygame.mixer.pre_init(44100,-16,2,_AUDIO_BUFFER) + pygame.init()
                 pre_init ANTES de pygame.init() — evita double-init
linhas 52-55   : FPS=60, AUDIO_LATENCY_FRAMES=round(_AUDIO_BUFFER/44100*FPS) ≈ 22
                 Compensa latencia do buffer: notas spawnam AUDIO_LATENCY_FRAMES mais tarde
linhas 56-74   : HIT_AGE = round(HIT_Y / NOTE_SPEED) = 130 (int)
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

Pacote de 46 bytes a 60fps (~4 ms de transmissão por quadro de 16,7 ms):
```
[0]  = 0x55 (header)
[1]  = {eq[3:0], state}: state nos 4 bits baixos (0=idle,1=countdown,2=play,3=pause,
       4=win,5=lose,6=select,7=settings); bits 5:4 = graves, 7:6 = médios
[2]  = {eq[7:4], countdown_sec[3:0]}: countdown 3,2,1,0; bits 5:4 = agudos, 7:6 = brilho
[3..14] = ages das notas: 4 trilhas × 3 slots × 1 byte (0xFF = slot vazio)
[15..16] = score (16 bits big-endian)
[17] = misses
[18] = combo
[19..20] = song_id ([19] bits 7-6) + frame_counter (14 bits: [19]bits13-8, [20]bits7-0)
--- métricas da sessão (metricas.v) ---
[21] = maior combo
[22..23] = N, acertos em botão (16 bits)
[24..26] = Σe (24 bits, complemento de 2)
[27..29] = Σ|e| (24 bits)
[30..32] = Σe² (24 bits)
[33..44] = por trilha t, 3 bytes a partir de 33+3t: {notas[t] 12 bits, acertos[t] 12 bits}
[45] = 0xAA (footer)
```

---

## Como rodar

```bash
uv run pc/interface.py --sim          # modo simulação (sem FPGA)
uv run pc/interface.py --sim --pseudonimo ana01   # grava o histórico e compara sessões
uv run --extra fpga pc/interface.py --port /dev/cu.usbserial-XXXX  # modo FPGA (macOS)
python pc/interface.py --port COM3    # modo FPGA (Windows)
python pc/interface.py --port /dev/ttyUSB0  # modo FPGA (Linux)
```

---

## Dependências Python

`pygame`, `mido`, `numpy` (atenuador), `pyserial` (opcional, apenas modo FPGA)

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
| `unidade_controle.v` | FSM (idle→select→countdown→play→pause/win/lose; idle↔settings) |
| `eq_config.v` | Níveis do atenuador de frequências (2 bits por faixa) |
| `fluxo_dados.v` | Lógica do jogo: ROMs, frame counter, note tracks, score |
| `packet_sender.v` | Serializa estado em pacote UART (46 bytes) |
| `note_track.v` | 1 trilha com 3 slots de nota; `hit_age` = idade da nota acertada |
| `metricas.v` | Acumuladores da sessão: N, Σe, Σ\|e\|, Σe², notas/acertos por trilha, maior combo |
| `taxa_acerto.v` | Divisor sequencial: taxa de acerto em % e dígitos para o HEX |
| `debounce_pulse.v` | Debounce + detecção de borda |
| `contador_m.v` | Contador parametrizável |

Testbenches (`tb/`): `tb_metricas.v` (cenários 2 e 3 do relatório, com 4 `note_track` reais),
`tb_taxa_acerto.v`, `tb_hex_taxa.v` (mux dos displays no top), `tb_packet_sender.v` (46 bytes),
`tb_note_track.v`, `tb_fd.v`, `tb_uc_*.v`, etc. `tb_countdown_transition.v` falha desde antes
(segura o reset ativo e referencia um `u_game_timer` que não existe).

Rodar sem o script `check`: `iverilog -g2012 -o .sim/X.out -s X rtl/*.v tb/X.v && vvp -n .sim/X.out`.
O `make yosys` não funciona no macOS (o `sed` do BSD não aceita `\|`); rodar o yosys à mão
com a lista de fontes do `.qsf`.
