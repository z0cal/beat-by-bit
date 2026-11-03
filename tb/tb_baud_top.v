/*-----------------------------------------------------------------------
 * Testbench : tb_baud_top.v
 * Modulo    : beat_by_bit (top), serial no baud padrao (921600)
 *-----------------------------------------------------------------------
 * Usa o top com os parametros padrao (CLK_HZ=50MHz, BAUD=921600); so o
 * gerador de quadros e encurtado por defparam.
 *   1. CLKS_PER_BIT = 54 chega ao packet_sender e ao uart_link
 *   2. O pino TX manda 1 pacote de 49 bytes, 0x55 ... 0xAA, estado IDLE,
 *      lido por um receptor do TB amostrando a cada 54 ciclos
 *   3. Cada bit do TX dura exatamente 54 ciclos (1080 ns)
 *   4. O pino RX recebe bytes do PC a 921600, a 923077 (taxa real do
 *      CP2102 para 921600) e com +-2% de erro de baud
 *   5. ACK 0xA5 acende o link_ok (LEDR3)
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_baud_top;

    localparam BIT   = 54;   // ciclos por bit esperados a 921600
    localparam real NS_PC = 1.0e9 / 921600.0;   // bit do PC (1085 ns)
    localparam NPKT  = 49;

    reg        clock  = 1'b0;
    reg        reset  = 1'b0;   // pino ativo em 0: 0 = em reset
    reg        rx_pin = 1'b1;   // linha ociosa em 1

    wire [6:0] db_estado, db_jogada, db_hex2;
    wire [4:0] leds_pulsos, db_botoes_raw;
    wire       tx_pin;

    beat_by_bit dut (
        .clock        (clock),
        .reset        (reset),
        .botoes       (5'b0),
        .db_estado    (db_estado),
        .db_jogada    (db_jogada),
        .db_hex2      (db_hex2),
        .leds_pulsos  (leds_pulsos),
        .db_botoes_raw(db_botoes_raw),
        .uart_tx_out  (tx_pin),
        .uart_rx_in   (rx_pin)
    );

    // Quadro curto: o pacote (49 x 10 x 54 = 26460 ciclos) cabe folgado
    defparam dut.u_fd.u_frame_counter.M = 40_000;

    always #10 clock = ~clock;   // 50 MHz

    integer erros = 0;

    task confere;
        input            cond;
        input [8*60-1:0] nome_teste;
        begin
            if (cond) $display("[OK]   %0s", nome_teste);
            else begin
                $display("[FAIL] %0s", nome_teste);
                erros = erros + 1;
            end
        end
    endtask

    // ── Receptor do TB no pino TX ─────────────────────────────────────
    reg [7:0] pkt [0:NPKT-1];
    integer   k, b;
    reg [7:0] byte_rx;

    task recebe_byte;
        output [7:0] dado;
        begin
            @(negedge tx_pin);                 // start bit
            repeat (BIT/2) @(posedge clock);   // meio do start
            for (b = 0; b < 8; b = b + 1) begin
                repeat (BIT) @(posedge clock);
                dado[b] = tx_pin;
            end
            repeat (BIT) @(posedge clock);     // meio do stop
            if (tx_pin !== 1'b1) begin
                $display("[FAIL] stop bit em 0");
                erros = erros + 1;
            end
        end
    endtask

    // ── Transmissor do TB no pino RX (periodo em ns por bit) ──────────
    task envia_byte;
        input [7:0] dado;
        input real  ns_bit;
        begin
            rx_pin = 1'b0; #(ns_bit);
            for (b = 0; b < 8; b = b + 1) begin
                rx_pin = dado[b]; #(ns_bit);
            end
            rx_pin = 1'b1; #(ns_bit);
            #(2 * ns_bit);
        end
    endtask

    // Bytes que o uart_rx do top entrega
    integer   n_rx = 0;
    reg [7:0] ultimo_rx = 8'h00;
    always @(posedge clock)
        if (dut.u_link.rx_valid) begin
            n_rx      <= n_rx + 1;
            ultimo_rx <= dut.u_link.rx_data;
        end

    // Duracao de 1 bit no TX: tempo entre a borda de descida do start e
    // a primeira borda seguinte num byte 0x55 (bits alternados)
    time t0, t1;

    initial begin
        $display("=== TB serial a 921600 no top ===");

        confere(dut.u_pkt.CLKS_PER_BIT == BIT && dut.u_link.CLKS_PER_BIT == BIT,
                "1. CLKS_PER_BIT = 54 no packet_sender e no uart_link");

        repeat (3) @(posedge clock);
        reset = 1'b1;   // solta o reset

        // 2/3. Primeiro pacote: header 0x55 = 0,1,0,1,... bits alternados
        @(negedge tx_pin); t0 = $time;
        @(posedge tx_pin); t1 = $time;
        confere((t1 - t0) == BIT * 20, "3. 1 bit do TX = 54 ciclos = 1080 ns");
        // Resto do header ja passou: le os 7 bits restantes + stop
        pkt[0] = 8'h55;
        repeat (BIT * 9 - BIT/2) @(posedge clock);
        for (k = 1; k < NPKT; k = k + 1) begin
            recebe_byte(byte_rx);
            pkt[k] = byte_rx;
        end
        confere(pkt[NPKT-1] == 8'hAA, "2. footer 0xAA no byte 48");
        confere((pkt[1] & 8'h0F) == 8'h00, "2. estado IDLE no byte 1");
        confere(pkt[45] == 8'h00 && pkt[46] == 8'h00 && pkt[47] == 8'h00,
                "2. nome '---' com cursor 0 nos bytes 45-47");

        // 4. RX: 921600 nominal, taxa real do CP2102 (48 MHz / 52) e +-2%
        envia_byte(8'h3C, NS_PC);
        confere(n_rx == 1 && ultimo_rx == 8'h3C, "4. RX 0x3C a 921600");
        envia_byte(8'h96, 1.0e9 / 923077.0);
        confere(n_rx == 2 && ultimo_rx == 8'h96, "4. RX 0x96 a 923077 (CP2102)");
        envia_byte(8'hC3, NS_PC * 0.98);
        confere(n_rx == 3 && ultimo_rx == 8'hC3, "4. RX 0xC3 a 921600 +2%");
        envia_byte(8'h5A, NS_PC * 1.02);
        confere(n_rx == 4 && ultimo_rx == 8'h5A, "4. RX 0x5A a 921600 -2%");

        // 5. ACK acende o LEDR3 (leds_pulsos[1])
        confere(leds_pulsos[1] == 1'b0, "5. sem ACK: link_ok apagado");
        envia_byte(8'hA5, NS_PC);
        repeat (5) @(posedge clock);
        confere(leds_pulsos[1] == 1'b1, "5. ACK 0xA5: link_ok aceso");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

    initial begin
        #5_000_000;
        $fatal(1, "=== FALHOU: timeout ===");
    end

endmodule
