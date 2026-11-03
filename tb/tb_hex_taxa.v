/*-----------------------------------------------------------------------
 * Testbench : tb_hex_taxa.v
 * Modulo    : beat_by_bit (top), mux dos displays HEX2-HEX0
 *-----------------------------------------------------------------------
 * Forca o estado da FSM e os contadores de metricas no top real e confere
 * os segmentos (ativos em 0, codigos do hexa7seg):
 *   1. Fora do END: HEX0 = estado, HEX1 = jogada, HEX2 apagado
 *   2. END com 100%: HEX2-HEX0 = 1 0 0
 *   3. END com 83%:  HEX2 apagado, HEX1-HEX0 = 8 3
 *   4. END com 5%:   HEX2 e HEX1 apagados, HEX0 = 5
 *   5. END_LOSE tambem mostra a taxa
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_hex_taxa;

    reg        clock  = 1'b0;
    reg        reset  = 1'b0;   // pino ativo em 0: 0 = em reset
    reg [4:0]  botoes = 5'b0;

    wire [6:0] db_estado, db_jogada, db_hex2;
    wire [4:0] leds_pulsos, db_botoes_raw;
    wire       uart_tx_out;

    beat_by_bit dut (
        .clock        (clock),
        .reset        (reset),
        .botoes       (botoes),
        .db_estado    (db_estado),
        .db_jogada    (db_jogada),
        .db_hex2      (db_hex2),
        .leds_pulsos  (leds_pulsos),
        .db_botoes_raw(db_botoes_raw),
        .uart_tx_out  (uart_tx_out)
    );

    always #5 clock = ~clock;

    localparam [6:0] SEG_0 = 7'b1000000, SEG_1 = 7'b1111001, SEG_2 = 7'b0100100,
                     SEG_3 = 7'b0110000,
                     SEG_5 = 7'b0010010, SEG_8 = 7'b0000000, APAGADO = 7'b1111111;

    integer erros = 0;

    task confere;
        input [6:0]      h2, h1, h0;
        input [8*40-1:0] nome;
        begin
            if (db_hex2 === h2 && db_jogada === h1 && db_estado === h0)
                $display("[OK]   %0s", nome);
            else begin
                $display("[FAIL] %0s: HEX2=%b HEX1=%b HEX0=%b (esperado %b %b %b)",
                         nome, db_hex2, db_jogada, db_estado, h2, h1, h0);
                erros = erros + 1;
            end
        end
    endtask

    // acertos e notas (todas na trilha 0); espera o divisor recalcular
    task metricas;
        input [15:0] n;
        input [11:0] notas;
        begin
            force dut.s_met_n     = n;
            force dut.s_met_notas = {36'd0, notas};
            repeat (60) @(posedge clock);
            #1;
        end
    endtask

    initial begin
        $dumpfile("tb_hex_taxa.vcd");
        $dumpvars(0, tb_hex_taxa);
        $display("=== TB HEX da taxa de acerto ===");

        repeat (3) @(posedge clock);
        reset = 1'b1;   // solta o reset
        metricas(16'd57, 12'd57);

        force dut.s_estado = 4'b0010;   // PLAY
        #1;
        confere(APAGADO, dut.seg_jogada, SEG_2,
                "1. PLAY: HEX0=estado 2, HEX2 apagado");

        force dut.s_estado = 4'b0100;   // END_WIN
        #1;
        confere(SEG_1, SEG_0, SEG_0, "2. END 57/57 = 100");

        metricas(16'd5, 12'd6);
        confere(APAGADO, SEG_8, SEG_3, "3. END 5/6 = 83");

        metricas(16'd1, 12'd20);
        confere(APAGADO, APAGADO, SEG_5, "4. END 1/20 = 5");

        force dut.s_estado = 4'b0101;   // END_LOSE
        #1;
        confere(APAGADO, APAGADO, SEG_5, "5. END_LOSE tambem mostra");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
