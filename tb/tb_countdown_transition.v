`timescale 1ns/1ps

module tb_countdown_transition;

    reg        clock  = 1'b0;
    reg        reset  = 1'b1;
    reg [4:0]  botoes = 5'b0;

    wire [6:0] db_estado;
    wire [6:0] db_jogada;
    wire [4:0] leds_pulsos;
    wire       uart_tx_out;

    beat_by_bit dut (
        .clock      (clock),
        .reset      (reset),
        .botoes     (botoes),
        .db_estado  (db_estado),
        .db_jogada  (db_jogada),
        .leds_pulsos(leds_pulsos),
        .uart_tx_out(uart_tx_out)
    );

    // Encurta a simulacao sem alterar a logica do hardware real.
    defparam dut.u_fd.u_frame_counter.M = 4;
    defparam dut.u_fd.u_countdown.M     = 6;
    defparam dut.u_fd.u_game_timer.M    = 20;
    defparam dut.u_pkt.u_tx.CLKS_PER_BIT = 4;

    always #5 clock = ~clock;

    task press_start;
        begin
            @(posedge clock);
            botoes[4] <= 1'b1;
            repeat (4) @(posedge clock);
            botoes[4] <= 1'b0;
        end
    endtask

    initial begin
        $display("=== TB countdown transition ===");

        repeat (3) @(posedge clock);
        reset <= 1'b0;

        repeat (2) @(posedge clock);
        press_start();

        repeat (20) begin
            @(posedge clock);
            $display("t=%0t state=%0d frame_q=%0d cd_q=%0d fim_contagem=%0b countdown=%0d",
                     $time,
                     dut.s_estado,
                     dut.u_fd.u_frame_counter.Q,
                     dut.u_fd.cd_frame_q,
                     dut.s_fim_contagem,
                     dut.s_countdown_sec);
        end

        if (dut.s_estado !== 4'd2) begin
            $display("[FAIL] Estado final esperado: PLAY (2), recebido: %0d", dut.s_estado);
            $fatal(1);
        end

        $display("[OK] Saiu de COUNTDOWN para PLAY");
        $finish;
    end

endmodule
