# Changelog — Beat by Bit (FPGA)

Comparação entre `/labdig_files/projeto_sem2` (defasado) e `/labdig_files_midiAudio/projeto_sem2` (atual).

---

## Resumo

As mudanças desta versão têm três focos principais:
1. **Expansão do chart ROM** para suportar músicas de até ~164 segundos
2. **Telemetria de posição** via UART: o frame counter agora é transmitido ao host em cada pacote
3. **Melhoria no fluxo de estados**: possibilidade de sair do PAUSE direto para IDLE via botão 0

---

## Arquivos modificados

### `fluxo_dados.v` — Lógica principal do jogo

**Expansão da ROM de chart**
- `ROM_DEPTH` aumentado de `4428` para `9861` frames
  - Capacidade: `9861 / 60fps ≈ 164 segundos` (era ~73s)
- Largura dos entries da ROM expandida de 4 bits para 8 bits
  - `reg [3:0] chart_rom` → `reg [7:0] chart_rom`
  - Os bits `[3:0]` continuam codificando as 4 tracks; bits `[7:4]` reservados para uso futuro

**Expansão do frame counter**
- `frame_counter` ampliado de 13 bits para 14 bits
  - `reg [12:0]` → `reg [13:0]` (necessário: 2¹³ = 8192 < 9861 ≤ 16383 = 2¹⁴)

**Nova saída: `frame_counter_out`**
- Adicionada porta de saída `output wire [13:0] frame_counter_out`
- Expõe o valor interno do frame counter para o módulo de topo

**Correção no debounce do botão 0**
- Removida dependência de `limpaR` no reset do debounce de `song_ok`
- Antes: `.reset(reset | limpaR)` → Agora: `.reset(reset)`
- Permite que a detecção do botão 0 persista durante transições de estado (necessário para sair do PAUSE)

---

### `unidade_controle.v` — Máquina de estados

**Nova transição: PAUSE → IDLE**
- Antes: no estado `pause`, apenas `start` retornava ao `play`
- Agora:
  ```
  pause:  song_ok → idle
          start   → play
          (else)  → pause
  ```
- O jogador pode abandonar uma música pausada e retornar à seleção de música sem dar reset

---

### `packet_sender.v` — Formatador de pacotes UART

**Nova entrada: `frame_counter`**
- Adicionada porta `input wire [13:0] frame_counter`

**Expansão do pacote UART**
- Tamanho do buffer: `pkt[0:19]` (20 bytes) → `pkt[0:21]` (22 bytes)
- Novos bytes incluídos antes do marcador de fim:
  - `pkt[19]` ← `{2'b00, frame_counter[13:8]}` — 6 bits superiores do frame counter
  - `pkt[20]` ← `frame_counter[7:0]` — 8 bits inferiores
  - `pkt[21]` ← `8'hAA` — marcador de fim (deslocado de `[19]` para `[21]`)
- Condição de fim de transmissão atualizada: `byte_idx == 19` → `byte_idx == 21`

**Impacto**: o host/UI pode agora rastrear a posição exata de reprodução do chart em tempo real, habilitando sincronização de áudio

---

### `proyecto.v` — Módulo de topo

**Novo fio intermediário**
- `wire [13:0] s_frame_counter` — conecta `frame_counter_out` do `fluxo_dados` ao `frame_counter` do `packet_sender`

**Instâncias atualizadas**
- `fluxo_dados`: nova porta `.frame_counter_out(s_frame_counter)` conectada
- `packet_sender`: nova porta `.frame_counter(s_frame_counter)` conectada

---

### `beat_by_bit.sdc` — Constraints de timing

- Referência do clock ajustada: `get_ports {CLOCK_50}` → `get_ports {clock}`
- Alinha a constraint ao nome interno usado no wrapper da placa DE0-CV

---

## Impacto funcional consolidado

| Aspecto | Antes | Depois |
|---|---|---|
| Duração máxima do chart | ~73 segundos (4.428 frames) | ~164 segundos (9.861 frames) |
| Largura dos entries da ROM | 4 bits | 8 bits |
| Frame counter | 13 bits | 14 bits |
| Pacote UART | 20 bytes | 22 bytes |
| Saída do frame counter | Não exposta | Transmitida via UART (bytes 19–20) |
| Saída do PAUSE | Apenas via `start` → `play` | `start` → `play` ou `song_ok` → `idle` |

---

## Módulos sem alterações

`uart_tx.v`, `edge_detector.v`, `debounce_pulse.v`, `hexa7seg.v`, `lfsr8.v`, `registrador_4.v`, `note_track.v`, `contador_m.v`, `beat_by_bit_de0_cv.v` e todos os testbenches permaneceram idênticos.
