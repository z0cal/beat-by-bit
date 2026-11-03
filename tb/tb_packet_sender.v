/*-----------------------------------------------------------------------
 * Testbench : tb_packet_sender.v
 * Modulo    : packet_sender (inclui uart_tx internamente)
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Ao receber frame_tick, transmite os 49 bytes do pacote
 *   2. Sequencia correta dos bytes: 0x55, {eq, state}, {eq, countdown},
 *      notas, score_h, score_l, misses, combo, {song_id, frame_h},
 *      frame_l, metricas, nome do perfil, 0xAA
 *   3. Niveis do atenuador nos nibbles altos dos bytes 1 e 2
 *   4. Metricas: soma_e negativa sai estendida em 24 bits; contadores
 *      de 12 bits empacotados em 3 bytes por trilha
 *   4b. Nome do perfil "AN0" com cursor 2 nos bytes 45-47
 *   5. Um segundo frame_tick gera outro pacote
 *
 * Usa o packet_sender real com CLKS_PER_BIT=4 (uart_tx rapido).
 * Um receptor UART simples decodifica os bytes enviados.
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_packet_sender;

    localparam CLKS_PER_BIT = 4;
    localparam CLK_PERIOD   = 10;
    localparam PACKET_SIZE  = 49;

    // DUT
    reg  clock      = 0;
    reg  reset      = 1;
    reg  frame_tick = 0;

    reg [3:0]  game_state    = 4'd7;  // SETTINGS
    reg [7:0]  countdown_sec = 8'd0;
    reg [7:0]  t0n0 = 8'd10,  t0n1 = 8'd50,  t0n2 = 8'hFF;
    reg [7:0]  t1n0 = 8'd130, t1n1 = 8'hFF,  t1n2 = 8'hFF;
    reg [7:0]  t2n0 = 8'hFF,  t2n1 = 8'hFF,  t2n2 = 8'hFF;
    reg [7:0]  t3n0 = 8'd80,  t3n1 = 8'd140, t3n2 = 8'hFF;
    reg [15:0] score  = 16'd1500;
    reg [7:0]  misses = 8'd3;
    reg [7:0]  combo  = 8'd7;
    reg [13:0] frame_counter = 14'h2A5C;
    reg [1:0]  song_id = 2'd2;
    // GRAVES=3, MEDIOS=0, AGUDOS=1, BRILHO=2
    reg [7:0]  eq = {2'd2, 2'd1, 2'd0, 2'd3};
    // Metricas: soma_e = -2 (sinal), contadores por trilha distintos
    reg [7:0]  met_max_combo = 8'd42;
    reg [15:0] met_n         = 16'd1234;
    reg [20:0] met_soma_e    = -21'sd2;
    reg [19:0] met_soma_abs  = 20'hABCDE;
    reg [22:0] met_soma_e2   = 23'h712345;
    // notas[t] = 0x100+t, acertos[t] = 0x0F0+t  (12 bits cada)
    reg [47:0] met_notas   = {12'h103, 12'h102, 12'h101, 12'h100};
    reg [47:0] met_acertos = {12'h0F3, 12'h0F2, 12'h0F1, 12'h0F0};
    // Nome "AN0" (A=1, N=14, 0=27), cursor na posicao 2
    reg [17:0] nome        = {6'd27, 6'd14, 6'd1};
    reg [1:0]  nome_cursor = 2'd2;

    wire uart_tx_out;

    packet_sender #(.CLKS_PER_BIT(CLKS_PER_BIT)) dut (
        .clock        (clock),
        .reset        (reset),
        .frame_tick   (frame_tick),
        .game_state   (game_state),
        .countdown_sec(countdown_sec),
        .t0n0(t0n0), .t0n1(t0n1), .t0n2(t0n2),
        .t1n0(t1n0), .t1n1(t1n1), .t1n2(t1n2),
        .t2n0(t2n0), .t2n1(t2n1), .t2n2(t2n2),
        .t3n0(t3n0), .t3n1(t3n1), .t3n2(t3n2),
        .score        (score),
        .misses       (misses),
        .combo        (combo),
        .frame_counter(frame_counter),
        .song_id      (song_id),
        .eq           (eq),
        .met_max_combo(met_max_combo),
        .met_n        (met_n),
        .met_soma_e   (met_soma_e),
        .met_soma_abs (met_soma_abs),
        .met_soma_e2  (met_soma_e2),
        .met_notas    (met_notas),
        .met_acertos  (met_acertos),
        .nome         (nome),
        .nome_cursor  (nome_cursor),
        .uart_tx_out  (uart_tx_out)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    // ── Receptor UART simples ─────────────────────────────────────────────────
    // Tarefa: recebe 1 byte do uart_tx_out
    task rx_byte;
        output [7:0] data;
        integer b;
        begin
            // Espera start bit (borda de descida)
            @(negedge uart_tx_out);
            // Pula para o meio do start bit
            repeat(CLKS_PER_BIT/2) @(posedge clock);
            // Amostra 8 bits de dados
            for (b = 0; b < 8; b = b+1) begin
                repeat(CLKS_PER_BIT) @(posedge clock);
                data[b] = uart_tx_out;
            end
            // Pula stop bit
            repeat(CLKS_PER_BIT) @(posedge clock);
        end
    endtask

    // Dispara 1 frame_tick
    task tick;
        begin
            @(posedge clock); #1;
            frame_tick = 1;
            @(posedge clock); #1;
            frame_tick = 0;
        end
    endtask

    integer erros = 0;
    reg [7:0] expected [0:PACKET_SIZE-1];
    reg [7:0] rx;
    integer   k;

    // Recebe um pacote inteiro e compara com expected[]
    task check_packet;
        input [8*24-1:0] nome;
        begin
            for (k = 0; k < PACKET_SIZE; k = k+1) begin
                rx_byte(rx);
                if (rx !== expected[k]) begin
                    $display("[FAIL] %0s Byte[%02d]: esperado 0x%02X, recebido 0x%02X",
                             nome, k, expected[k], rx);
                    erros = erros + 1;
                end
            end
            $display("[INFO] %0s recebido", nome);
        end
    endtask

    task monta_esperado;
        begin
            expected[0]  = 8'h55;
            expected[1]  = {eq[3:0], game_state};
            expected[2]  = {eq[7:4], countdown_sec[3:0]};
            expected[3]  = t0n0; expected[4]  = t0n1; expected[5]  = t0n2;
            expected[6]  = t1n0; expected[7]  = t1n1; expected[8]  = t1n2;
            expected[9]  = t2n0; expected[10] = t2n1; expected[11] = t2n2;
            expected[12] = t3n0; expected[13] = t3n1; expected[14] = t3n2;
            expected[15] = score[15:8];
            expected[16] = score[7:0];
            expected[17] = misses;
            expected[18] = combo;
            expected[19] = {song_id, frame_counter[13:8]};
            expected[20] = frame_counter[7:0];
            expected[21] = met_max_combo;
            expected[22] = met_n[15:8];
            expected[23] = met_n[7:0];
            expected[24] = {{3{met_soma_e[20]}}, met_soma_e[20:16]};
            expected[25] = met_soma_e[15:8];
            expected[26] = met_soma_e[7:0];
            expected[27] = {4'd0, met_soma_abs[19:16]};
            expected[28] = met_soma_abs[15:8];
            expected[29] = met_soma_abs[7:0];
            expected[30] = {1'b0, met_soma_e2[22:16]};
            expected[31] = met_soma_e2[15:8];
            expected[32] = met_soma_e2[7:0];
            for (k = 0; k < 4; k = k + 1) begin
                expected[33 + 3*k] = met_notas[12*k + 4 +: 8];
                expected[34 + 3*k] = {met_notas[12*k +: 4], met_acertos[12*k + 8 +: 4]};
                expected[35 + 3*k] = met_acertos[12*k +: 8];
            end
            expected[45] = {nome_cursor, nome[5:0]};
            expected[46] = {2'b00, nome[11:6]};
            expected[47] = {2'b00, nome[17:12]};
            expected[48] = 8'hAA;
        end
    endtask

    initial begin
        $dumpfile("tb_packet_sender.vcd");
        $dumpvars(0, tb_packet_sender);

        // Reset
        reset = 1;
        repeat(4) @(posedge clock);
        reset = 0;
        @(posedge clock);

        $display("=== TB packet_sender ===");

        // --- Pacote 1: SETTINGS com eq misto ---
        monta_esperado;
        // Confere o empacotamento do eq com valores literais
        if (expected[1] !== 8'h37) begin
            $display("[FAIL] byte 1 esperado 0x37 (MEDIOS=0, GRAVES=3, SETTINGS)");
            erros = erros + 1;
        end
        if (expected[2] !== 8'h90) begin
            $display("[FAIL] byte 2 esperado 0x90 (BRILHO=2, AGUDOS=1, countdown 0)");
            erros = erros + 1;
        end
        // Literais: soma_e = -2 em 24 bits e a trilha 1 empacotada
        if (expected[24] !== 8'hFF || expected[25] !== 8'hFF || expected[26] !== 8'hFE) begin
            $display("[FAIL] soma_e=-2 deveria sair como FF FF FE");
            erros = erros + 1;
        end
        if (expected[36] !== 8'h10 || expected[37] !== 8'h10 || expected[38] !== 8'hF1) begin
            $display("[FAIL] trilha 1 (notas 0x101, acertos 0x0F1) deveria sair 10 10 F1");
            erros = erros + 1;
        end
        if (expected[45] !== 8'h81 || expected[46] !== 8'h0E || expected[47] !== 8'h1B) begin
            $display("[FAIL] nome AN0 com cursor 2 deveria sair 81 0E 1B");
            erros = erros + 1;
        end
        tick;
        check_packet("Pacote 1");

        // --- Pacote 2: COUNTDOWN com eq zerado ---
        repeat(20) @(posedge clock);
        game_state    = 4'd1;
        countdown_sec = 8'd3;
        eq            = 8'h00;
        met_soma_e    = 21'sd300;
        met_n         = 16'd7;
        nome          = 18'd0;
        nome_cursor   = 2'd0;
        monta_esperado;
        tick;
        check_packet("Pacote 2");

        // --- Resultado ---
        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    initial begin
        #2000000;
        $fatal(1, "[TIMEOUT]");
    end

endmodule
