/*-----------------------------------------------------------------------
 * Testbench : tb_nome_top.v
 * Modulo    : beat_by_bit (top), nome do perfil com botoes reais
 *-----------------------------------------------------------------------
 * Os botoes passam pelo debounce real (encurtado por defparam) e o estado
 * da FSM e forcado:
 *   1. Em PLAY os botoes nao mexem no nome
 *   2. No fim de musica: F, F, K, F monta "BA-" -> c0=2, c1=1
 *   3. Cursor chega ao packet_sender
 *   4. Voltar a IDLE zera o cursor e mantem o nome
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_nome_top;

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
        .uart_tx_out  (uart_tx_out),
        .uart_rx_in   (1'b1)
    );

    defparam dut.u_fd.u_db_btn0.DEBOUNCE_CYCLES = 8;
    defparam dut.u_fd.u_db_btn1.DEBOUNCE_CYCLES = 8;
    defparam dut.u_fd.u_db_btn2.DEBOUNCE_CYCLES = 8;
    defparam dut.u_fd.u_db_btn3.DEBOUNCE_CYCLES = 8;

    always #5 clock = ~clock;

    integer erros = 0;

    // Aperta e solta a trilha t, esperando o fim do bloqueio do debounce
    task aperta;
        input integer t;
        begin
            @(negedge clock); botoes[t] = 1'b1;
            repeat (6) @(posedge clock);
            @(negedge clock); botoes[t] = 1'b0;
            repeat (14) @(posedge clock);
            #1;
        end
    endtask

    task confere;
        input [17:0]     nome;
        input [1:0]      cur;
        input [8*44-1:0] nome_teste;
        begin
            if (dut.s_nome === nome && dut.s_nome_cursor === cur &&
                dut.u_pkt.nome === nome && dut.u_pkt.nome_cursor === cur)
                $display("[OK]   %0s", nome_teste);
            else begin
                $display("[FAIL] %0s: nome=%h cursor=%0d (esperado %h, %0d)",
                         nome_teste, dut.s_nome, dut.s_nome_cursor, nome, cur);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("tb_nome_top.vcd");
        $dumpvars(0, tb_nome_top);
        $display("=== TB nome do perfil no top ===");

        repeat (3) @(posedge clock);
        reset = 1'b1;   // solta o reset
        repeat (5) @(posedge clock);

        force dut.s_estado = 4'b0010;   // PLAY
        aperta(1);
        aperta(3);
        confere(18'd0, 2'd0, "1. PLAY nao mexe no nome");

        force dut.s_estado = 4'b0100;   // END_WIN
        aperta(1); aperta(1);           // c0: '-' -> A -> B
        aperta(3);                      // cursor 1
        aperta(1);                      // c1: A
        confere({6'd0, 6'd1, 6'd2}, 2'd1, "2/3. BA- com cursor 1 ate o packet_sender");

        force dut.s_estado = 4'b0000;   // IDLE
        repeat (2) @(posedge clock); #1;
        confere({6'd0, 6'd1, 6'd2}, 2'd0, "4. IDLE: cursor 0, nome mantido");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
