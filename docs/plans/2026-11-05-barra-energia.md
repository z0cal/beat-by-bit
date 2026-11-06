# Barra de energia pelo sensor (substitui a nota de gesto)

## Contexto
O usuário não gostou da nota de gesto (uma barra solta que exige a mão numa hora marcada). O design novo:
- A música não tem **nenhuma** nota de sensor.
- O HC-SR04 enche uma **barra de energia**, mas só enquanto a mão está sobre o sensor. A barra enche **mais rápido quando a mão se movimenta** (aproximar e afastar).
- Barra cheia ativa o **x2 automaticamente**, por um tempo fixo.
- Ideia validada contra a Star Power do Guitar Hero e as notas AIR do Chunithm. Sem punição, coerente com o público TEA.

## Contrato

### Regras (na FPGA, módulo novo `rtl/energia.v`; o PC só desenha)
- Só conta em PLAY (`game_active`). Congela em PAUSE. Zera no COUNTDOWN (como `metricas.v`).
- **Leitura válida com mão:** `leitura_ok` com `DIST_MIN ≤ dist_cm ≤ DIST_MAX`. Parâmetros: `DIST_MIN=5`, `DIST_MAX=30`. Leituras 0 (sem eco) ou fora da faixa não contam e não são "movimento".
- **Enchimento:** a cada leitura válida, `energia += GANHO_PRESENCA` (1). Se a leitura válida **anterior** também foi válida e `|dist − dist_anterior| ≥ LIMIAR_MOV` (3 cm), soma também `GANHO_MOV` (2) e conta 1 **movimento**.
  - Com a mão parada, a barra enche em ~15 s. Mexendo, em ~5 s.
  - Os valores são parâmetros, a ajustar no PARECER_TEA.
- `energia` vai de 0 a `ENERGIA_MAX` = 255, com saturação.
- **x2:** quando `energia` chega a 255, `x2_ativo` = 1 por `DURACAO_X2` = 480 quadros (8 s).
  - Durante o x2, a barra **esvazia linearmente** (energia exibida = tempo restante escalado para 0..255) e **não enche**.
  - No fim, `energia` = 0 e volta a encher.
  - `ativacoes` conta quantas vezes o x2 ligou.
- **Pontuação:** com `x2_ativo`, cada acerto de botão soma `2 × (100 + combo)`. `metricas.v`, a assincronia, o combo e os erros não mudam.
- O x2 **não tem punição** e nunca gera erro.

### Remoções
- Sai a nota de gesto: a 5ª `note_track`, os contadores gestos apresentados/acertados e o bit 4 da ROM.
- A ROM volta a `[3:0]`, e os charts são regenerados sem gestos. As notas de botão removidas para abrir espaço voltam.
- O **gesto de confirmação na tela de configurações** (troca de aba) **fica**, assim como `hcsr04.v`, `gesto_detector.v` (usado só para o `confirma`), `cfg_jogo.v` e o metrônomo.

### Pacote UART (56 bytes; 0–47 iguais, 52/53/55 iguais)
| Byte | Conteúdo |
|---|---|
| 48 | distância em cm (igual) |
| 49 | energia (0..255; durante o x2 = tempo restante escalado) |
| 50 | `{x2_ativo, ativacoes[6:0]}` (ativações saturam em 127) |
| 51 | movimentos[15:8] |
| 52 | config (igual) |
| 53 | metrônomo (igual) |
| 54 | movimentos[7:0] |
| 55 | 0xAA |

### PC
- **Barra de energia** vertical ao lado da pista e **medidor da mão** (distância ao vivo; acende quando a mão está na faixa 5–30 cm).
- Enchendo, brilha mais quando há movimento.
- No x2: trilhas/bordas douradas e um selo "x2" com o tempo restante.
- No perfil sensorial: sem piscar nem partículas, só a cor e a barra.
- Sem a barra de gesto.
- **`--sim`:**
  - **G** segurada = mão parada a 15 cm.
  - **H** segurada = mão em vai-e-vem: alterna 10 ↔ 20 cm a cada leitura de ~60 ms.
  - Soltar = sem eco.
  - O `SimulationEngine` replica `energia.v` bit a bit (mesmos parâmetros).
- **Dashboard:** "Movimentos N · x2 ativado M vezes" no lugar de "Gestos X/Y".
  - CSV: troca `gestos_apresentados`/`gestos_acertados` por `movimentos`/`ativacoes_x2`.
  - CSVs antigos continuam legíveis: a migração é aditiva e não perde colunas antigas.

### Sincronia (prioridade 1)
Os charts mudam: os gestos saem e as notas de botão voltam.
- Rodar `pc/medir_sync.py` antes e depois: mediana ~0 e p90 ≤ 8,3 ms, como hoje.
- O tempo de quadro no `--sim` não pode piorar.
- Nada muda em `_play_music`, na âncora ou em `CHART_LEAD_FRAMES`.

## Verificação
`make check-all`, `make yosys`, `pc/verifica_charts.py` (sem gestos), `pc/medir_sync.py` e o teste headless do PC (pacote de 56 bytes no layout novo e o sim enchendo e ativando o x2 com G e com H).
