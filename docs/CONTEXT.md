# Beat by Bit — Contextualização do Projeto

Projeto de laboratório digital (USP, ELE3323). Jogo de ritmo estilo Guitar Hero rodando em FPGA DE0-CV + interface Python.

Layout: `rtl/` (Verilog + Quartus + charts `.hex`), `tb/` (testbenches), `pc/` (Python + assets), `songs/` (MIDI + WAV), `docs/`.

---

## Arquitetura

```
FPGA (Verilog) ──UART 921600───► Python (Pygame)
  projeto.v           (56 bytes/frame)    interface.py
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
  mão (D+F, J+K) e a variação em relação à sessão anterior do mesmo perfil. Gesto e
  metrônomo aparecem como "ainda não disponível". A tela é estática (sem piscar).
  - **Métricas na FPGA** (`metricas.v`, dentro do `fluxo_dados`): a cada acerto,
    `e = age − 130` quadros (negativo = adiantado); acumula N, Σe, Σ|e|, Σe², notas julgadas e
    acertos por trilha (12 bits) e o maior combo. Zera em COUNTDOWN; congela em PAUSE/END/IDLE.
    O PC calcula média = Σe/N e desvio = √(Σe²/N − média²), × 16,7 ms.
  - **Taxa nos displays (TAXA_HEX)**: no END, HEX2–HEX0 mostram ⌊100·N/notas⌋ (divisor
    sequencial `taxa_acerto.v`), sem zeros à esquerda; fora do END, HEX0 = estado,
    HEX1 = jogada, HEX2 apagado. Porta nova `db_hex2` no top (pinos Y19…AB21, mesma ordem
    de bits do HEX0/HEX1: `[0]` = segmento `a`).
  - **Perfil estilo arcade** (`nome_perfil.v`, dentro do `fluxo_dados`): no dashboard a
    criança escolhe 3 caracteres (`-`, A–Z, 0–9) com os botões: D/F = caractere
    anterior/próximo (37 valores, dá a volta), J/K = cursor para esquerda/direita (para nas
    pontas). START salva e volta à tela inicial. O nome fica guardado até o reset da FPGA
    (a próxima sessão já começa com ele); `---` = sem perfil. O PC só desenha: a caixa do
    cursor fica com borda laranja e um triângulo embaixo. Ao trocar o nome, a comparação
    com a sessão anterior é refeita na hora.
  - **Histórico local**: a sessão é gravada **ao apertar START no dashboard** (saída de
    WIN/LOSE), com o nome escolhido como pseudônimo (`---` grava sem pseudônimo e sem
    comparação), em `sessoes/sessoes.csv` (`--historico` muda o caminho; a pasta está no
    `.gitignore`). Fechar a interface no dashboard perde a sessão. Um aviso aparece na tela
    inicial por 6 s. Falha de disco só gera aviso. É a base do MQTT da Semana 4 (mesmos
    campos, `session_row`). Se a interface abrir com a música já no fim, a sessão não é
    gravada.

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

Pacote de 56 bytes a 60fps, 921600 baud 8N1 (~0,53 ms de transmissão por quadro de 16,7 ms).
A velocidade é o parâmetro `BAUD` do `projeto.v` (50 MHz / 921600 = 54 ciclos/bit → 925926 baud,
+0,5%) e o `--baud` do PC (padrão 921600); os dois precisam bater. Para voltar a 115200:
`BAUD = 115_200` e `--baud 115200`. **Não usar 1 Mbaud**: o CP2102 original só tem até 921600 na
tabela padrão, então o pedido de 1000000 vira ~921600 no conversor (8% de erro) e nenhum pacote
chega inteiro (a tela fica parada na tela inicial e o LEDR3 apagado). A folga sobra para os
dados do sensor e do servo-metrônomo. Diagnóstico na bancada: `uv run
pc/diag_serial.py --port <porta>` (conta bytes, pacotes válidos e estados vistos).
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
--- nome do perfil (nome_perfil.v) ---
[45] = {cursor[1:0], c0[5:0]}   c0 = caractere da esquerda
[46] = {2'b00, c1[5:0]}
[47] = {2'b00, c2[5:0]}         código: 0='-', 1..26='A'..'Z', 27..36='0'..'9'
--- semana 3: sensor, gesto, configuração e metrônomo ---
[48] = distância do HC-SR04 em cm (satura em 255; 0 = sem eco/timeout)
[49] = idade da nota de gesto (5ª trilha, slot 0; 0xFF = nenhuma)
[50] = gestos apresentados (julgados: acerto + escape)
[51] = gestos acertados
[52] = {progresso[2:0], pagina, sem_som, sensorial, modo_met[1:0]}
       modo_met 0 = desligado, 1 = fixo (2 = adaptativo, S4); pagina 0 = Som, 1 = Jogo;
       progresso = gesto de confirmação 0..7
[53] = {5'b0, lado, nivel[1:0]}  nivel 0 = 60°, 1 = 30°, 2 = 15°, 3 = parado
[54] = 0x00 (reservado)
[55] = 0xAA (footer)
```

### Gesto, configurações e metrônomo (Semana 3)

- **ROM de 5 bits**: bits [3:0] = trilhas, bit 4 = nota de gesto (sozinha no quadro, ≥ 600 quadros
  entre gestos, 60 quadros livres em volta; 4 por música). Checar com `uv run python pc/verifica_charts.py`.
- **Nota de gesto** (`fluxo_dados.v`): 5ª `note_track` com `btn_press = gesto_pulso` (uma leitura
  do HC-SR04 com a mão entre 5 e 25 cm). Acerto = +100 + combo; escape = miss; mão fora da janela é
  ignorada (`bad_press` descartado). Não entra em `metricas.v` (assincronia/trilhas).
- **HC-SR04** (`hcsr04.v`): TRIG de 10 µs a cada 60 ms, ECHO sincronizado, cm = µs/58, timeout 25 ms.
- **Gesto de confirmação** (`gesto_detector.v`, só em SETTINGS): 16 leituras seguidas na faixa
  (~1 s) geram um pulso que troca a página; só rearma após 4 leituras fora. Evita disparo acidental.
- **Configurações em 2 páginas** (`cfg_jogo.v` + `eq_config.v`): página Som = atenuador (D/F/J/K);
  página Jogo = D metrônomo desligado/fixo, F perfil sensorial, J sem som (K reservado). Sem chaves SW.
- **Metrônomo** (`metronomo.v` + `servo_pwm.v`): período e fase em ciclos de 50 MHz vindos de
  `rtl/bpm_params.vh` (gerado por `gerar_hex.py`; suporta uma troca de andamento: Pokémon 192→136 BPM
  no tempo 40). Braço ±30° no modo fixo, centro fora do PLAY/pausa/desligado. PWM 50 Hz, 1–2 ms.
- **PC**: perfil sensorial = música a 50% e efeitos intensos desligados (fica um anel fino);
  sem som = volume 0 (a música segue, o sync não muda). No `--sim`, **G** = mão no sensor.

---

## Decisões da Semana 3 (05/11/2026) — não reverter sem motivo

Registro do *porquê* de cada escolha; o *o quê* está nas seções acima.

| Decisão | Motivo |
|---------|--------|
| **Sem chaves SW.** Metrônomo, perfil sensorial e sem som são escolhidos na página "Jogo" da tela de configurações | As chaves são de difícil acesso na maquete; a S2 já tinha migrado a configuração para telas com os botões das trilhas. Substitui o SW0–SW3 da especificação (registrar como desvio) |
| **HC-SR04 como entrada da tela de configurações**: segurar a mão entre 5 e 25 cm troca a página | Dá um "botão" extra sem fiação nova |
| Gesto de confirmação exige **16 leituras seguidas na faixa (~1 s)**; uma leitura fora zera; rearma só após **4 leituras fora** | O sensor dispara fácil por acidente (mão passando, corpo da criança); segurar 1 s e ter de tirar a mão evita trocas repetidas |
| Gesto de confirmação **só em SETTINGS**; em PLAY vale só a nota de gesto | Evita trocar configuração no meio da música |
| Gesto troca **página**, não confirma valor; START continua sendo "salvar e voltar" | Mantém o modelo da S2; um disparo acidental só muda a aba exibida, nunca um ajuste |
| Página "Jogo": D = metrônomo desligado↔fixo (adaptativo só na S4), F = sensorial, J = sem som, K reservado | Uma tecla por opção, sem menus; K livre para a janela configurável (opcional) |
| Registradores de configuração **zeram só no reset** (como `eq_config`) | A configuração vale para várias crianças/sessões seguidas |
| Nota de gesto = **5ª `note_track`** com a mesma física (idade, janela ±11, escape em 151) | Reuso do módulo testado; o aviso de 2,2 s sai de graça (130 quadros de queda) |
| Gesto: acerto **+100 + combo** e conta no combo/maior combo; escape = **miss**; mão fora da janela é **ignorada** | Gesto é parte da música, mas o sensor não pode punir a criança por mexer a mão (`bad_press` descartado) |
| Gesto **fora de `metricas.v`** (assincronia, notas/acertos por trilha) | A resolução do sensor (~60 ms por leitura) não serve para medir assincronia; o gesto tem contadores próprios no pacote |
| **"Gestos apresentados" = julgados** (acerto + escape), não spawnados | A nota só "conta" quando sai; ao fim da música é igual ao total. O `--sim` segue a mesma regra |
| Até **4 gestos por música**, ≥ 10 s entre gestos, 1 s sem notas de botão em volta, nenhum nos primeiros 10 s | ESPACAMENTO_GESTO; poucos gestos pela alternância botão↔mão (risco na especificação). Algumas notas de botão foram removidas (Condado 74→68, Pokémon 67→60) |
| Bit 4 da ROM = gesto, **sozinho no quadro** | Simplifica o desenho (barra cobrindo as 4 trilhas) e o julgamento |
| Andamento por música em `bpm_params.vh` com **um segundo segmento** (`PERIODO2`, `NTROCA`) | O MIDI do Pokémon muda de 192 para 136 BPM no tempo 40; com um só período o metrônomo saía ~20 s de fase. Mais de 2 andamentos: só os 2 primeiros (com aviso) |
| `FASE` = instante em que o tempo **chega ao alvo**, não o spawn | O metrônomo tem de bater junto com a nota na hit zone |
| Metrônomo **congela em PAUSE** e reinicia em IDLE/SELECT/COUNTDOWN | Não perde a fase com a pausa. Jitter de até 1 quadro na entrada do PLAY (não alinhado ao `frame_tick`), aceito |
| Modo fixo = nível 0 = **±30°** (60° de excursão total) | Interpretação do "nível de 60°" do RETIRADA_METRONOMO; 30°/15° serão ±15°/±7,5° na S4 |
| `bpm_params.vh` incluído com **`ifdef QUARTUS`** (`../../rtl/` vs `rtl/`) | Mesmo truque dos `$readmemh`; o `check` do iverilog não aceita `-I` |
| HC-SR04 **alimentado a 3,3 V**, ECHO direto no GPIO, **sem divisor** | Ensaio da S1: com 3,3 V o eco não passa de 3,3 V (COMPATIBILIDADE_ELETRICA). Não ligar a 5 V |
| Pinos: TRIG C16 (JP1-4), ECHO K20 (JP1-6), servo K22 (JP1-8) | Os da S1 (GPIO_0_D3/D5/D7). **Conferir no Pin Planner** antes de ligar |
| Perfil sensorial = música a **50% (0,35)** + sem bursts/partículas/flash/pulsos; fica **um anel fino** no acerto | PERFIL_SENSORIAL sem violar LATENCIA_FEEDBACK (o retorno visual não pode sumir) |
| Sem som = **volume 0**, a música continua tocando | Parar o mixer quebraria a sincronia chart × áudio e a prévia de 10 s |
| Pacote **56 bytes** (≤ 64 do PACOTE_METRICAS), byte 54 reservado | Folga para o nível adaptativo/tempo por nível da S4 |

Pendências que ficaram de fora: tempo por nível do metrônomo e modo adaptativo (S4), confirmação
de pinos e síntese no Quartus, ensaio na placa (sensor a 5/15/25 cm, servo no tempo, falsos
disparos do gesto de confirmação).

---

## Como rodar

```bash
uv run pc/interface.py --sim          # modo simulação (sem FPGA)
uv run pc/interface.py --port /dev/cu.usbserial-XXXX  # modo FPGA (macOS)
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
| `packet_sender.v` | Serializa estado em pacote UART (56 bytes) |
| `hcsr04.v` | Medidor de distância (TRIG/ECHO → cm) |
| `gesto_detector.v` | Mão na faixa, pulso de gesto e gesto de confirmação |
| `cfg_jogo.v` | Página de configurações, modo do metrônomo, perfil sensorial, sem som |
| `metronomo.v` / `servo_pwm.v` | Tempo da música e PWM do servo SG90 |
| `bpm_params.vh` | Período/fase/troca de andamento por música (gerado) |
| `note_track.v` | 1 trilha com 3 slots de nota; `hit_age` = idade da nota acertada |
| `metricas.v` | Acumuladores da sessão: N, Σe, Σ\|e\|, Σe², notas/acertos por trilha, maior combo |
| `taxa_acerto.v` | Divisor sequencial: taxa de acerto em % e dígitos para o HEX |
| `nome_perfil.v` | Nome do perfil em 3 caracteres, editado com D/F/J/K no fim da música |
| `debounce_pulse.v` | Debounce + detecção de borda |
| `contador_m.v` | Contador parametrizável |

Testbenches (`tb/`): `tb_metricas.v` (cenários 2 e 3 do relatório, com 4 `note_track` reais),
`tb_taxa_acerto.v`, `tb_hex_taxa.v` (mux dos displays no top), `tb_packet_sender.v` (56 bytes),
`tb_nome_perfil.v`, `tb_nome_top.v` (nome com os botões reais pelo debounce),
`tb_baud_top.v` (top a 921600: pacote no pino TX, ACK no RX a 921600, 923077 e ±2%),
`tb_note_track.v`, `tb_fd.v` (inclui gesto e páginas com modelo de eco), `tb_hcsr04.v`,
`tb_gesto_detector.v`, `tb_cfg_jogo.v`, `tb_metronomo.v`, `tb_servo_pwm.v`, `tb_countdown_transition.v`
(corrigido na S3), `tb_uc_*.v`, etc.

Rodar sem o script `check`: `iverilog -g2012 -o .sim/X.out -s X rtl/*.v tb/X.v && vvp -n .sim/X.out`.
O `make yosys` não funciona no macOS (o `sed` do BSD não aceita `\|`); rodar o yosys à mão
com a lista de fontes do `.qsf`.

---

## Hardware e pinagem (DE0-CV)

Cola de montagem com desenho do conector: `docs/cola/cola_beat_by_bit.pdf` (fonte `.tex` ao lado).

Tudo no **JP1 (GPIO_0)**; JP2 livre. Cabeçalho de 40 pinos: pinos 1-10 = GPIO[0..9], 11 = 5 V,
12 = GND, 13-28 = GPIO[10..25], 29 = 3,3 V, 30 = GND, 31-40 = GPIO[26..35].

| Sinal | Pino JP1 | Pino FPGA | Liga em |
|-------|----------|-----------|---------|
| `uart_tx_out` | 1 | N16 | RXD do CP2102 (GND do CP2102 no pino 12) |
| `uart_rx_in` | 2 | B16 | TXD do CP2102 (ACK do PC) |
| `reset` | 16 | T22 | opcional: botão → GND (pull-up interno ligado; apertado = reset) |
| `botoes[4]` START/PAUSE | 36 | T20 | botão → GND + pull-up 10 kΩ |
| `botoes[1]` | 37 | T19 | idem |
| `botoes[3]` | 38 | T18 | idem |
| `botoes[0]` | 39 | T17 | idem |
| `botoes[2]` | 40 | T15 | idem |
| `hcsr04_trig` | 4 | C16 | TRIG do HC-SR04 (sensor alimentado pelos 3,3 V do pino 29) |
| `hcsr04_echo` | 6 | K20 | ECHO do HC-SR04 (≤ 3,3 V com o sensor a 3,3 V) |
| `servo_pwm` | 8 | K22 | sinal do SG90 (servo na fonte de 5 V externa, GND comum) |
| 3,3 V / GND | 29 / 30 | — | trilhos + / − dos pull-ups e botões |

- Pull-ups dos botões são **externos**: solto = 1, apertado = 0. O `reset` usa o pull-up interno
  (`WEAK_PULL_UP_RESISTOR` no `.qsf`): pode ficar sem nada ligado no pino 16.
- `clock` = CLOCK_50 (M9). Fora do END: `HEX0` = estado da FSM, `HEX1` = `botoes[3:0]` registrados na
  jogada, `HEX2` apagado. No END: `HEX2..0` = taxa de acerto em %.
- LEDR0..4 = `leds_pulsos` = {start, fim_contagem, fim_tempo, link_ok, jogada_feita} na ordem
  LEDR0 = start ... LEDR4 = jogada_feita. LEDR5..9 = `botoes[0..4]` crus (aceso = solto).
- **LEDR3 = link serial ok** (`uart_link.v`): o Python manda `0xA5` a cada 0,25 s enquanto recebe
  pacotes válidos; a FPGA (`uart_rx`) mantém o LED aceso até 1 s após o último ACK. Aceso = caminho
  completo FPGA → RXD → Python → TXD → FPGA ok. Apagado com o jogo rodando = pino 1 no TXD (invertido),
  falta o fio do pino 2, GND solto ou Python sem a porta aberta.

### Pegadinhas conhecidas (verificado em 27/10/2026)

- **Segmentos dos displays** (corrigido em 27/10/2026): o `.qsf` tinha `db_estado`/`db_jogada`
  com índices `[6:0]` invertidos. Agora `[0]` = segmento `a` (U21 em HEX0, AA20 em HEX1, Y19 em HEX2),
  que é a ordem do `hexa7seg` (`0 = 7'b1000000`, bit 6 = `g`).
- **Jogada conta ao soltar**: `debounce_pulse` dispara na subida do sinal; com pull-up, a subida
  é quando o botão é solto. Lockout de 9 M ciclos (180 ms).
- **LEDR0, 2, 4 nunca acendem visivelmente** (pulsos de 1 ciclo). `perdeu = 1'b0` (sem limite
  de erros, então END_LOSE nunca ocorre); por isso LEDR3 foi reaproveitado para o link serial. **LEDR1** fica aceso em
  PLAY/PAUSE (o contador do countdown para em 179).
- Na **PAUSE**, qualquer botão 0..3 (`song_ok`) volta para IDLE, não só o botão 0.
- **`tb_countdown_transition` falha** desde antes de 27/10/2026 (estado fica em 0); as demais passam.
- **Serial muda (0 bytes) com a FPGA gravada = olhar primeiro o pino `reset`** (verificado em 03/11/2026,
  corrigido com o pull-up interno). `reset_i = ~reset`: sem o pull-up, com o pino 16 do JP1 (T22) solto, ele oscila,
  zera o `u_frame_counter` e o `frame_tick` nunca sai, logo nenhum pacote é enviado. O caminho pino 1 → RXD
  estava bom. Medido: sem pull-up, `reset`=0 em 125 de 143 leituras; com `WEAK_PULL_UP_RESISTOR` em
  `reset`, 192 de 192 em 1 e 60 pacotes/s. Sintoma na placa: HEX0 preso em 0 e LEDR3 apagado.

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
