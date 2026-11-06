/*-----------------------------------------------------------------------
 * Arquivo   : projeto.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Modulo top-level. Instancia UC, FD, packet_sender e
 *             displays de debug. Adiciona saida UART para PC.
 *
 * Displays HEX:
 *   Fora do fim de musica: HEX0 = estado da FSM, HEX1 = ultima jogada,
 *   HEX2 apagado.
 *   No fim de musica (END_WIN/END_LOSE): HEX2-HEX0 = taxa de acerto em
 *   porcentagem (TAXA_HEX), sem zeros a esquerda. Ex.: 100, 87, 5.
 *
 * Serial (8N1): BAUD define a velocidade de TX (pacotes) e RX (ACK).
 *   O PC usa o mesmo valor (--baud, padrao 921600). 921600 e o maximo
 *   da tabela padrao do CP2102 original: pedir 1000000 cai em ~921600
 *   no conversor e corrompe todos os bytes. Para voltar a 115200,
 *   troque BAUD aqui e rode o PC com --baud 115200.
 *-----------------------------------------------------------------------
 */

module beat_by_bit #(
    parameter CLK_HZ = 50_000_000,
    parameter BAUD   = 921_600,      // 50 MHz / 921600 = 54 ciclos/bit (925926 baud, +0,5%)
    parameter HC_CICLO_MS   = 60,    // HC-SR04: periodo de medida (os TBs encurtam)
    parameter HC_TIMEOUT_US = 25_000
) (
    input  wire        clock,
    input  wire        reset,
    input  wire [4:0]  botoes,
    output wire [6:0]  db_estado,    // HEX0: Estado da FSM
    output wire [6:0]  db_jogada,    // HEX1: Ultimo botao pressionado
    output wire [6:0]  db_hex2,      // HEX2: centena da taxa de acerto (no END)
    output wire [4:0]  leds_pulsos,  // LEDs de debug
    output wire [4:0]  db_botoes_raw,// Debug: botoes crus (LEDR 5-9)
    output wire        uart_tx_out,  // TX serial para o PC (BAUD)
    input  wire        uart_rx_in,   // RX serial: ACK do PC (TXD do CP2102)
    output wire        hcsr04_trig,  // GPIO_0_D3: TRIG do HC-SR04
    input  wire        hcsr04_echo,  // GPIO_0_D5: ECHO do HC-SR04 (5 V -> divisor de tensao!)
    output wire        servo_pwm     // GPIO_0_D7: sinal do servo do metronomo
);

    // Reset invertido: o pino GPIO tem pull-up (1 quando solto),
    // mas o circuito usa reset ativo em 1, entao invertemos.
    wire reset_i = ~reset;

    localparam CLKS_PER_BIT = CLK_HZ / BAUD;

    // Fios entre UC e FD
    wire s_registraR, s_limpaR;
    wire s_jogada_feita, s_start_pulso, s_song_ok;
    wire s_fim_contagem, s_fim_tempo, s_perdeu;
    wire s_zera_timer, s_conta_timer, s_game_active;
    wire [3:0] s_estado, s_valor_jogada;

    // Fios de estado do jogo (FD -> packet_sender)
    wire [7:0] s_countdown_sec;
    wire [7:0] s_t0n0, s_t0n1, s_t0n2;
    wire [7:0] s_t1n0, s_t1n1, s_t1n2;
    wire [7:0] s_t2n0, s_t2n1, s_t2n2;
    wire [7:0] s_t3n0, s_t3n1, s_t3n2;
    wire [15:0] s_score;
    wire [7:0]  s_misses, s_combo;
    wire        s_frame_tick;
    wire [13:0] s_frame_counter;
    wire [1:0]  s_song_id;
    wire        s_link_ok;
    wire [7:0]  s_eq;

    // Metricas da sessao (FD -> packet_sender e taxa de acerto)
    wire [15:0] s_met_n;
    wire [20:0] s_met_soma_e;
    wire [19:0] s_met_soma_abs;
    wire [22:0] s_met_soma_e2;
    wire [47:0] s_met_notas, s_met_acertos;
    wire [7:0]  s_met_max_combo;

    // Nome do perfil (FD -> packet_sender)
    wire [17:0] s_nome;
    wire [1:0]  s_nome_cursor;

    // Gesto, configuracao "Jogo" e metronomo (FD -> packet_sender)
    wire [7:0]  s_dist_cm, s_gesto_age, s_gestos_apres, s_gestos_acertos;
    wire [2:0]  s_progresso;
    wire        s_pagina, s_sem_som, s_sensorial, s_met_lado;
    wire [1:0]  s_modo_met, s_met_nivel;

    // LEDR3 = link serial ok (PC recebendo pacotes); perdeu e fixo em 0
    assign leds_pulsos = {s_start_pulso, s_fim_contagem, s_fim_tempo, s_link_ok, s_jogada_feita};

    // Debug: sinais crus dos botoes (antes do debounce) nos LEDR 5-9
    assign db_botoes_raw = botoes;

    // ----------------------------------------------------------------
    // Unidade de Controle (FSM)
    // ----------------------------------------------------------------
    unidade_controle u_uc (
        .clock       (clock),
        .reset       (reset_i),
        .start       (s_start_pulso),
        .song_ok     (s_song_ok),
        .jogada      (s_jogada_feita),
        .fim_contagem(s_fim_contagem),
        .fim_tempo   (s_fim_tempo),
        .perdeu      (s_perdeu),
        .registraR   (s_registraR),
        .limpaR      (s_limpaR),
        .zera_timer  (s_zera_timer),
        .conta_timer (s_conta_timer),
        .game_active (s_game_active),
        .db_estado   (s_estado)
    );

    // ----------------------------------------------------------------
    // Fluxo de Dados (toda logica do jogo)
    // ----------------------------------------------------------------
    fluxo_dados #(.HC_CICLO_MS(HC_CICLO_MS), .HC_TIMEOUT_US(HC_TIMEOUT_US)) u_fd (
        .clock        (clock),
        .reset        (reset_i),
        .limpaR       (s_limpaR),
        .registraR    (s_registraR),
        .zera_timer   (s_zera_timer),
        .conta_timer  (s_conta_timer),
        .game_active  (s_game_active),
        .db_estado    (s_estado),
        .botoes_raw   (botoes),
        .jogada_feita (s_jogada_feita),
        .start_pulso  (s_start_pulso),
        .song_ok_pulso(s_song_ok),
        .fim_contagem (s_fim_contagem),
        .fim_tempo    (s_fim_tempo),
        .perdeu       (s_perdeu),
        .s_jogada     (s_valor_jogada),
        .countdown_sec(s_countdown_sec),
        .t0n0(s_t0n0), .t0n1(s_t0n1), .t0n2(s_t0n2),
        .t1n0(s_t1n0), .t1n1(s_t1n1), .t1n2(s_t1n2),
        .t2n0(s_t2n0), .t2n1(s_t2n1), .t2n2(s_t2n2),
        .t3n0(s_t3n0), .t3n1(s_t3n1), .t3n2(s_t3n2),
        .score         (s_score),
        .misses        (s_misses),
        .combo         (s_combo),
        .frame_tick_out    (s_frame_tick),
        .frame_counter_out (s_frame_counter),
        .song_id_out       (s_song_id),
        .eq_out            (s_eq),
        .met_n             (s_met_n),
        .met_soma_e        (s_met_soma_e),
        .met_soma_abs      (s_met_soma_abs),
        .met_soma_e2       (s_met_soma_e2),
        .met_notas         (s_met_notas),
        .met_acertos       (s_met_acertos),
        .met_max_combo     (s_met_max_combo),
        .nome_out          (s_nome),
        .nome_cursor       (s_nome_cursor),
        .hcsr04_echo       (hcsr04_echo),
        .hcsr04_trig       (hcsr04_trig),
        .servo_pwm         (servo_pwm),
        .dist_cm_out       (s_dist_cm),
        .gesto_age         (s_gesto_age),
        .gestos_apres      (s_gestos_apres),
        .gestos_acertos    (s_gestos_acertos),
        .progresso_out     (s_progresso),
        .pagina_out        (s_pagina),
        .sem_som_out       (s_sem_som),
        .sensorial_out     (s_sensorial),
        .modo_met_out      (s_modo_met),
        .met_lado          (s_met_lado),
        .met_nivel         (s_met_nivel)
    );

    // ----------------------------------------------------------------
    // Packet Sender (monta e envia pacote UART a cada frame)
    // ----------------------------------------------------------------
    packet_sender #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_pkt (
        .clock        (clock),
        .reset        (reset_i),
        .frame_tick   (s_frame_tick),
        .game_state   (s_estado),
        .countdown_sec(s_countdown_sec),
        .t0n0(s_t0n0), .t0n1(s_t0n1), .t0n2(s_t0n2),
        .t1n0(s_t1n0), .t1n1(s_t1n1), .t1n2(s_t1n2),
        .t2n0(s_t2n0), .t2n1(s_t2n1), .t2n2(s_t2n2),
        .t3n0(s_t3n0), .t3n1(s_t3n1), .t3n2(s_t3n2),
        .score         (s_score),
        .misses        (s_misses),
        .combo         (s_combo),
        .frame_counter (s_frame_counter),
        .song_id       (s_song_id),
        .eq            (s_eq),
        .met_max_combo (s_met_max_combo),
        .met_n         (s_met_n),
        .met_soma_e    (s_met_soma_e),
        .met_soma_abs  (s_met_soma_abs),
        .met_soma_e2   (s_met_soma_e2),
        .met_notas     (s_met_notas),
        .met_acertos   (s_met_acertos),
        .nome          (s_nome),
        .nome_cursor   (s_nome_cursor),
        .dist_cm       (s_dist_cm),
        .gesto_age     (s_gesto_age),
        .gestos_apres  (s_gestos_apres),
        .gestos_acertos(s_gestos_acertos),
        .progresso     (s_progresso),
        .pagina        (s_pagina),
        .sem_som       (s_sem_som),
        .sensorial     (s_sensorial),
        .modo_met      (s_modo_met),
        .met_lado      (s_met_lado),
        .met_nivel     (s_met_nivel),
        .uart_tx_out   (uart_tx_out)
    );

    // ----------------------------------------------------------------
    // Link serial: acende LEDR3 enquanto o PC devolve ACKs
    // ----------------------------------------------------------------
    uart_link #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_link (
        .clock    (clock),
        .reset    (reset_i),
        .rx_serial(uart_rx_in),
        .link_ok  (s_link_ok)
    );

    // ----------------------------------------------------------------
    // Taxa de acerto (divisor sequencial) para os displays
    // ----------------------------------------------------------------
    wire [13:0] s_notas_total = {2'b00, s_met_notas[11:0]}  + {2'b00, s_met_notas[23:12]}
                              + {2'b00, s_met_notas[35:24]} + {2'b00, s_met_notas[47:36]};
    wire [6:0]  s_taxa;
    wire [3:0]  s_taxa_c, s_taxa_d, s_taxa_u;

    taxa_acerto u_taxa (
        .clock  (clock),
        .reset  (reset_i),
        .acertos(s_met_n),
        .notas  (s_notas_total),
        .taxa   (s_taxa),
        .centena(s_taxa_c),
        .dezena (s_taxa_d),
        .unidade(s_taxa_u)
    );

    // ----------------------------------------------------------------
    // Displays de debug
    // ----------------------------------------------------------------
    wire [6:0] seg_estado, seg_jogada, seg_c, seg_d, seg_u;

    hexa7seg u_hex_est (
        .hex(s_estado),
        .seg(seg_estado)
    );

    hexa7seg u_hex_jog (
        .hex(s_valor_jogada),
        .seg(seg_jogada)
    );

    hexa7seg u_hex_c (.hex(s_taxa_c), .seg(seg_c));
    hexa7seg u_hex_d (.hex(s_taxa_d), .seg(seg_d));
    hexa7seg u_hex_u (.hex(s_taxa_u), .seg(seg_u));

    // Fim de musica: 0100 = END_WIN, 0101 = END_LOSE
    wire       fim_musica = (s_estado == 4'b0100) || (s_estado == 4'b0101);
    localparam [6:0] APAGADO = 7'b1111111;   // segmentos ativos em 0

    assign db_estado = fim_musica ? seg_u : seg_estado;
    assign db_jogada = fim_musica ? ((s_taxa_c != 4'd0 || s_taxa_d != 4'd0) ? seg_d : APAGADO)
                                  : seg_jogada;
    assign db_hex2   = (fim_musica && s_taxa_c != 4'd0) ? seg_c : APAGADO;

endmodule
