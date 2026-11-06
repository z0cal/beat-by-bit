// Gerado por pc/gerar_hex.py (pc/interface.py: bpm_params). NAO EDITAR A MAO.
//
// Parametros do metronomo por musica (slot = indice em SONGS / song_id_reg:
// 0 condado, 1 ocarina, 2 power_rangers, 3 pokemon). Tudo em ciclos de 50 MHz.
//
// PERIODO_n = duracao de um tempo = (microssegundos por tempo do primeiro
//             set_tempo do MIDI) * 50, inteiro exato. Sem set_tempo: 120 BPM.
//             Os tempos 0..NTROCA_n-1 usam PERIODO_n.
// PERIODO2_n / NTROCA_n = segundo andamento do MIDI (2o set_tempo): PERIODO2_n e o
//             tempo depois da troca e NTROCA_n o indice k do primeiro tempo que ja usa
//             PERIODO2_n (round(tick_da_troca / ticks_por_tempo); AVISO se a troca nao
//             cai exatamente num tempo). Um so andamento: PERIODO2_n = PERIODO_n e
//             NTROCA_n = 32'hFFFFFFFF. Mais de 2 andamentos: so os 2 primeiros (AVISO).
//             Tempo k chega em FASE + k*PERIODO (k < NTROCA) ou
//             FASE + NTROCA*PERIODO + (k-NTROCA)*PERIODO2 (k >= NTROCA).
// FASE_n    = ciclos desde frame_counter = 0 (inicio do PLAY) ate o primeiro
//             tempo CHEGAR a hit zone. O primeiro tempo e o tick 0 do MIDI (t = 0
//             s do audio); a nota correspondente seria criada HIT_AGE (130)
//             quadros antes e atinge o alvo em t*60 + AUDIO_LATENCY_FRAMES
//             quadros (= 6 quadros aqui), igual ao deslocamento de
//             _midi_to_chart. Quadro = 833333 ciclos (M do frame_tick em
//             fluxo_dados.v). FASE_n = AUDIO_LATENCY_FRAMES * 833333, mod PERIODO_n.
// O tempo k chega ao alvo em FASE_n + k * PERIODO_n ciclos apos o inicio do PLAY.
//
// slot 0: Concerning Hobbits, 105.000 BPM
// slot 1: Song of Storms, 90.000 BPM
// slot 2: Super Patrulha Delta, 180.000 BPM
// slot 3: Opening Theme, 192.000 BPM  troca para 136.000 BPM no tempo 40

`define PERIODO_0 32'd28571400
`define FASE_0    32'd4999998
`define PERIODO2_0 32'd28571400
`define NTROCA_0  32'hFFFFFFFF
`define PERIODO_1 32'd33333350
`define FASE_1    32'd4999998
`define PERIODO2_1 32'd33333350
`define NTROCA_1  32'hFFFFFFFF
`define PERIODO_2 32'd16666650
`define FASE_2    32'd4999998
`define PERIODO2_2 32'd16666650
`define NTROCA_2  32'hFFFFFFFF
`define PERIODO_3 32'd15625000
`define FASE_3    32'd4999998
`define PERIODO2_3 32'd22058800
`define NTROCA_3  32'd40
