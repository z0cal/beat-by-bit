# Beat by Bit 2.0 — Design de alto nível

Status: rascunho para revisão. Cobre a visão e a decomposição; cada subprojeto
(A–F) terá seu próprio spec detalhado, plano e implementação.

## 1. Objetivo e contexto

Demo de 3 a 5 minutos para avaliadores da disciplina de laboratório digital,
em ~3 meses. O jogo roda na FPGA (DE0-CV). O jogador envia qualquer música pelo
celular, joga com notas longas (hold) e um sensor ultrassônico, e o recorde
aparece numa tela e-ink.

Ponto de partida (`projeto_sem2/`): FSM + fluxo de dados em Verilog, 4 charts em
ROM carregados por `$readmemh`, pacote UART de 22 bytes a 60 fps, renderizador
Pygame no PC que também toca o áudio.

Critério de sucesso: a demo completa roda de ponta a ponta sem depender de
internet, e cada etapa tem um plano B.

## 2. Fora de escopo

- Músicas de streaming (DRM); só arquivos locais (MP3, M4A, WAV, OGG).
- Modo multijogador.
- Acionar a e-ink pela FPGA via SPI (o ESP8266 faz isso).
- iOS.

## 3. Arquitetura

```
Celular Android ──HTTP (Tailscale)──► PC (Python)
(app nativo)                          ├─ converte p/ WAV, analisa, corta, gera o chart
                                      ├─ toca o áudio cortado e desenha o jogo
                                      ├─ guarda os recordes (JSON)
                                      ├─ UART ──► FPGA: RAM do chart + lógica do jogo
                                      │              ▲ sensor HC-SR04
                                      └─ USB serial ──► ESP8266 ──► e-ink 2,13" 4 cores
```

Princípio: a FPGA continua sendo o juiz do jogo (FSM, notas, acertos, score,
vida). O PC é interface e orquestrador. O áudio nunca vai para a FPGA; só o
chart (~240 bits/s, ~9 KB para 5 min).

Rede: celular e PC no mesmo tailnet (MagicDNS). O PC usa o hotspot do celular. O
servidor HTTP escuta só na interface do Tailscale.

## 4. Subprojetos

### A — RAM + upload em runtime (Verilog)
- Troca as ROMs por uma RAM com porta de escrita; remove `$readmemh` e as
  profundidades fixas de `fluxo_dados.v`.
- Novo `uart_rx.v` e estado `LOAD` na UC.
- Entrada do chart: 1 byte por frame. Bits `[3:0]` = spawn por trilha; bits
  `[7:4]` = corpo da nota longa por trilha.
- Protocolo serial: magic, nº de frames, flags (inclui perfil competitivo),
  dados, CRC8; a FPGA responde ACK/NACK.
- Seleção de música sai dos 4 botões fixos e passa a ser comandada pelo PC.
- Decide aqui: largura do `frame_counter` (14 bits cobre ~273 s).

### B — Hold notes + ultrassônico
- `note_track` ganha estado "segurando" e precisa do **nível** do botão
  sincronizado (hoje só há `btn_pulse`).
- FSM do HC-SR04 (trigger/echo + contador de largura de pulso), amostragem a
  ~15–20 Hz, filtro (mediana de 3 + zona morta).
- Bônus de pontos pela **taxa de variação** da distância durante o hold (não a
  distância absoluta).
- Valor do sensor vai num byte novo do pacote UART.
- Hardware: ECHO sai em 5 V; usar divisor resistivo para 3,3 V.

### C — Pipeline no PC
- Servidor HTTP: recebe o arquivo inteiro; converte para WAV com ffmpeg uma vez;
  analisa a música toda (forma de onda, tempos de batida, onsets, trecho
  sugerido); devolve JSON leve; recebe `{início, fim, dificuldade, nome,
  perfil}`; fatia os onsets e gera o chart; sobe para a FPGA; toca o áudio
  cortado (fade curto).
- Reaproveita a lógica de `_midi_to_chart` e as ideias de `beat_mapper.py`
  (librosa).
- O mesmo WAV serve para análise e reprodução, o que garante sincronia.
- Biblioteca pré-carregada no PC como atalho e plano B.
- Cliente provisório e plano B: script `curl` no Termux.

### D — App Android
- Escolhe o arquivo, envia o arquivo inteiro em segundo plano e já toca a prévia
  localmente (MediaPlayer/ExoPlayer) enquanto o PC analisa.
- Tela de corte: forma de onda (dados do PC), densidade de notas, alças com snap
  nas batidas, "ouvir trecho".
- Escolhe dificuldade, perfil (Calmo/Competitivo) e nome; mostra progresso real.
- O app não decodifica nem corta áudio.

### E — Recorde na e-ink
- Painel GDEY0213F51 (122×250, preto/branco/vermelho/amarelo), NodeMCU ESP8266.
- O PC desenha o bitmap (Pillow, fonte do jogo, logo) e envia os planos pela
  serial; o ESP8266 só exibe. Reaproveita `tools/img2epd.py`.
- Refresh de 15–35 s sem parcial: atualiza só ao fim de uma partida
  **competitiva** que bateu recorde. Mostra o top 3 da música. Não há bloco
  para o perfil Calmo.
- Leaderboard por música (`song_id`), um só, apenas do Competitivo, em JSON no
  PC. Partidas do Calmo são gravadas à parte e não participam do ranking.

### F — Experiência premium
Perfis (escolhidos no app, trocáveis no PC):

| | Competitivo | Calmo |
|---|---|---|
| Janela de acerto e classificação Perfect/Great/OK | igual | igual |
| Score e bônus do ultrassônico | calculados | calculados (idem) |
| Score, combo, erros e julgamento **durante a partida** | visíveis | ocultos |
| Barra de vida e derrota | sim | não (sem game over) |
| Tela final | score, erros, estrelas | só o score final (sem erros) |
| Entra no leaderboard e na e-ink | sim | não |
| Efeitos de acerto (partículas, flash) | sim | mínimo: brilho sutil no botão |
| Animação de fundo, scanlines/vignette | sim | desligadas ou estáticas |
| Cauda do hold e reação ao ultrassônico | sim | sim, suave |

A mecânica e a pontuação são idênticas nos dois perfis. O perfil muda o que é
exibido e se a partida pode terminar em derrota. A FPGA calcula tudo sempre; a
flag de perfil (no cabeçalho do upload) só liga a transição para a derrota
(`perdeu`, hoje fixo em 0). O PC decide o que mostrar.

Como o Calmo não tem derrota, seus scores não são comparáveis aos do
Competitivo. Por isso existe **um único leaderboard, só do Competitivo**. As
partidas do Calmo têm o score final mostrado e gravado no JSON local, mas não
entram no ranking nem na e-ink.

Itens de prioridade P1:
- Janela de acerto em camadas (hoje ±11 frames = ±183 ms) com Perfect/Great/OK
  classificados pela FPGA.
- Eventos de acerto/erro/botão-sem-nota por trilha **no pacote UART**, em vez de
  inferidos no PC pelo desaparecimento da nota.
- Barra de vida na FPGA (modo competitivo).
- Debounce curto (alguns ms) para o pad; o atual de 180 ms só serve a botão
  mecânico.
- `frame_counter` como relógio mestre do áudio + calibração de latência em
  runtime (hoje `AUDIO_LATENCY_FRAMES` é gravado nos `.hex`).
- Renderização numa superfície lógica fixa, escalada para a janela, com tela
  cheia (hoje fixo em 1800×1080).
- Painel "FPGA por dentro" (estado da FSM, `frame_counter`, progresso do `LOAD`,
  distância do sensor, taxa de pacotes), ligável por tecla.
- Tela de verificação ao iniciar (FPGA, e-ink, rede) e reconexão serial
  automática.

P2: sons de acerto e erro (só no competitivo), top 3 na e-ink, modelo de
referência da simulação. P3: vibração e resultado no celular.

Acessibilidade: o perfil Calmo também serve a quem se incomoda com muitos
estímulos visuais; evitar flashes rápidos.

## 5. Mudança no protocolo UART (FPGA → PC)

Hoje: 22 bytes (`0x55`, estado, countdown, 12 ages, score, misses, combo,
`{song_id, frame_counter}`, `0xAA`). O pacote novo precisa de: bits de evento por
trilha e qualidade do acerto, vida, valor do sensor. Projetar **uma única vez**
junto com o formato do chart (A), pois B e F dependem dele.

## 6. Decisões já tomadas

- Runtime com RAM; o chart, e não o áudio, vai para a FPGA.
- O PC corta, analisa e toca o áudio; o celular só envia e toca a prévia.
- App envia o arquivo inteiro e depois o intervalo.
- Rede por Tailscale, servidor só no tailnet.
- Bitmap da e-ink desenhado no PC; ESP8266 só exibe.
- Perfil Competitivo opcional; Calmo é o modo de poucos estímulos visuais. O
  Calmo mantém a janela de acerto, a pontuação (incluindo o bônus do
  ultrassônico); só oculta as métricas durante a partida, não tem derrota e
  não entra no leaderboard. O leaderboard é único e só do Competitivo.

## 7. Riscos e pontos em aberto

- Tempo de análise da música inteira no PC (alguns a ~15 s): medir. Atenuado
  porque o usuário ouve a prévia enquanto o PC analisa.
- Desempenho do renderizador: suspeita de alocação de superfícies por frame
  (`_draw_tracks`, `_draw_pause`, `_draw_endscreen`) e de blits de tela cheia com
  alfa; medir o FPS antes de otimizar.
- Dois dispositivos USB serial (FPGA e ESP8266): identificar por
  `/dev/serial/by-id/`.
- `SimulationEngine` já diverge da FPGA (pausa, escape de nota, ESC); comparar
  pacotes via iverilog para evitar regressão.
- `run_tb.sh` cobre só 5 dos ~11 testbenches.
- Documentação defasada (`CONTEXT.md`, `CHANGELOG.md`, `documentacao.tex`);
  atualizar durante o trabalho.

**A confirmar:**
- Interpretação adotada: o Calmo mostra e grava o score final, mas só o
  Competitivo entra no leaderboard e na e-ink. Se a intenção era o Calmo ter
  também um recorde próprio exibido em algum lugar, ajustar.

Decidido: o Calmo usa a mesma janela de acerto, o ultrassônico dá bônus
numérico em ambos os perfis, a tela final do Calmo não mostra erros, e há um
único leaderboard só do Competitivo (e-ink idem).

## 8. Cronograma indicativo (12 semanas)

| Semanas | Entrega |
|---|---|
| 1–3 | A, com testbenches; uploader Python mínimo |
| 3–6 | B; pacote UART novo; itens P1 de F ligados à FPGA |
| 5–7 | C completo; E |
| 7–11 | D, com o plano B de Termux já funcionando; resto de F |
| 11–12 | Congelar funcionalidades, calibração, telas de falha, ensaios, vídeo de backup |

As duas últimas semanas não recebem funcionalidade nova.
