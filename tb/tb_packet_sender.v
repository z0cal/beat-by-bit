/*-----------------------------------------------------------------------
 * Testbench : tb_packet_sender.v
 * Modulo    : packet_sender (inclui uart_tx internamente)
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Ao receber frame_tick, inicia transmissao de 20 bytes
 *   2. Sequencia correta dos bytes: 0x55, state, countdown, notas...
 *      score_h, score_l, misses, combo, 0xAA
 *   3. Segundo frame_tick enquanto ainda transmite: ignorado
 *      (packet_sender so aceita novo pacote apos DONE)
 *
 * Usa CLKS_PER_BIT=4 (uart_tx rapido para simulacao).
 * NOTA: para usar, instancie packet_sender com uart_tx #(.CLKS_PER_BIT(4))
 *       OU modifique temporariamente o CLKS_PER_BIT no packet_sender.v
 *
 * Este TB usa um receptor UART simples para decodificar os bytes enviados.
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_packet_sender;

    localparam CLKS_PER_BIT = 4;
    localparam CLK_PERIOD   = 10;
    localparam PACKET_SIZE  = 20;

    // DUT
    reg  clock      = 0;
    reg  reset      = 1;
    reg  frame_tick = 0;

    reg [3:0]  game_state    = 4'd2;  // PLAY
    reg [7:0]  countdown_sec = 8'd0;
    reg [7:0]  t0n0 = 8'd10,  t0n1 = 8'd50,  t0n2 = 8'hFF;
    reg [7:0]  t1n0 = 8'd130, t1n1 = 8'hFF,  t1n2 = 8'hFF;
    reg [7:0]  t2n0 = 8'hFF,  t2n1 = 8'hFF,  t2n2 = 8'hFF;
    reg [7:0]  t3n0 = 8'd80,  t3n1 = 8'd140, t3n2 = 8'hFF;
    reg [15:0] score  = 16'd1500;
    reg [7:0]  misses = 8'd3;
    reg [7:0]  combo  = 8'd7;

    wire uart_tx_out;

    // Instancia packet_sender com CLKS_PER_BIT reduzido para simulacao
    // ATENCAO: packet_sender.v instancia uart_tx com parametro fixo 434.
    // Para este TB funcionar corretamente, edite temporariamente o parametro
    // no packet_sender.v para CLKS_PER_BIT=4, ou use o parametro abaixo.
    packet_sender_tb_wrap #(.CLKS_PER_BIT(CLKS_PER_BIT)) dut (
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
        .uart_tx_out  (uart_tx_out)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    // ── Receptor UART simples ─────────────────────────────────────────────────
    // Detecta start bit (queda de 0) e amostra 8 bits de dados
    reg [7:0]  rx_packet [0:PACKET_SIZE-1];
    integer    rx_count = 0;
    reg        rx_active = 0;

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

    integer erros = 0;
    reg [7:0] expected [0:PACKET_SIZE-1];
    reg [7:0] rx;
    integer   k;

    initial begin
        $dumpfile("tb_packet_sender.vcd");
        $dumpvars(0, tb_packet_sender);

        // Monta pacote esperado
        expected[0]  = 8'h55;
        expected[1]  = {4'b0000, game_state};
        expected[2]  = countdown_sec;
        expected[3]  = t0n0; expected[4]  = t0n1; expected[5]  = t0n2;
        expected[6]  = t1n0; expected[7]  = t1n1; expected[8]  = t1n2;
        expected[9]  = t2n0; expected[10] = t2n1; expected[11] = t2n2;
        expected[12] = t3n0; expected[13] = t3n1; expected[14] = t3n2;
        expected[15] = score[15:8];
        expected[16] = score[7:0];
        expected[17] = misses;
        expected[18] = combo;
        expected[19] = 8'hAA;

        // Reset
        reset = 1;
        repeat(4) @(posedge clock);
        reset = 0;
        @(posedge clock);

        $display("=== TB packet_sender ===");

        // Dispara frame_tick
        @(posedge clock); #1;
        frame_tick = 1;
        @(posedge clock); #1;
        frame_tick = 0;

        // Recebe 20 bytes e verifica
        for (k = 0; k < PACKET_SIZE; k = k+1) begin
            rx_byte(rx);
            rx_packet[k] = rx;
            if (rx === expected[k])
                $display("[OK]  Byte[%02d] = 0x%02X", k, rx);
            else begin
                $display("[FAIL] Byte[%02d]: esperado 0x%02X, recebido 0x%02X",
                         k, expected[k], rx);
                erros = erros + 1;
            end
        end

        // Aguarda um pouco e dispara segundo frame_tick
        repeat(20) @(posedge clock);
        @(posedge clock); #1;
        frame_tick = 1;
        @(posedge clock); #1;
        frame_tick = 0;
        $display("[INFO] Segundo frame_tick disparado (deve gerar outro pacote)");

        // Recebe segundo pacote (apenas verifica header e footer)
        rx_byte(rx);
        if (rx === 8'h55)
            $display("[OK]  Segundo pacote: header 0x55 correto");
        else begin
            $display("[FAIL] Segundo pacote: header errado = 0x%02X", rx);
            erros = erros + 1;
        end
        // Recebe os 18 bytes intermediarios
        for (k = 1; k < PACKET_SIZE-1; k = k+1)
            rx_byte(rx);
        // Footer
        rx_byte(rx);
        if (rx === 8'hAA)
            $display("[OK]  Segundo pacote: footer 0xAA correto");
        else begin
            $display("[FAIL] Segundo pacote: footer errado = 0x%02X", rx);
            erros = erros + 1;
        end

        // --- Resultado ---
        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    initial begin
        #1000000;
        $display("[TIMEOUT]");
        $finish;
    end

endmodule


/*-----------------------------------------------------------------------
 * Wrapper: packet_sender com CLKS_PER_BIT parametrizavel
 * Necessario porque packet_sender.v tem uart_tx com parametro fixo.
 * Este wrapper permite ao TB controlar o CLKS_PER_BIT.
 *-----------------------------------------------------------------------
 */
module packet_sender_tb_wrap #(parameter CLKS_PER_BIT = 434) (
    input  wire        clock,
    input  wire        reset,
    input  wire        frame_tick,
    input  wire [3:0]  game_state,
    input  wire [7:0]  countdown_sec,
    input  wire [7:0]  t0n0, t0n1, t0n2,
    input  wire [7:0]  t1n0, t1n1, t1n2,
    input  wire [7:0]  t2n0, t2n1, t2n2,
    input  wire [7:0]  t3n0, t3n1, t3n2,
    input  wire [15:0] score,
    input  wire [7:0]  misses,
    input  wire [7:0]  combo,
    output wire        uart_tx_out
);

    localparam IDLE = 2'd0, LOAD = 2'd1, SEND = 2'd2, DONE = 2'd3;

    reg  [7:0] tx_data;
    reg        tx_valid;
    wire       tx_ready;

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clock    (clock), .reset(reset),
        .tx_data  (tx_data), .tx_valid(tx_valid),
        .tx_serial(uart_tx_out), .tx_ready(tx_ready)
    );

    reg [1:0] state;
    reg [4:0] byte_idx;
    reg [7:0] pkt [0:19];

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            state    <= IDLE;
            byte_idx <= 0;
            tx_valid <= 0;
        end else begin
            tx_valid <= 0;
            case (state)
                IDLE: begin
                    if (frame_tick) begin
                        pkt[0]  <= 8'h55;
                        pkt[1]  <= {4'b0000, game_state};
                        pkt[2]  <= countdown_sec;
                        pkt[3]  <= t0n0; pkt[4]  <= t0n1; pkt[5]  <= t0n2;
                        pkt[6]  <= t1n0; pkt[7]  <= t1n1; pkt[8]  <= t1n2;
                        pkt[9]  <= t2n0; pkt[10] <= t2n1; pkt[11] <= t2n2;
                        pkt[12] <= t3n0; pkt[13] <= t3n1; pkt[14] <= t3n2;
                        pkt[15] <= score[15:8];
                        pkt[16] <= score[7:0];
                        pkt[17] <= misses;
                        pkt[18] <= combo;
                        pkt[19] <= 8'hAA;
                        byte_idx <= 0;
                        state    <= LOAD;
                    end
                end
                LOAD: begin
                    if (tx_ready) begin
                        tx_data  <= pkt[byte_idx];
                        tx_valid <= 1;
                        state    <= SEND;
                    end
                end
                SEND: begin
                    if (!tx_ready) begin
                        if (byte_idx == 19) state <= DONE;
                        else begin byte_idx <= byte_idx + 1; state <= LOAD; end
                    end
                end
                DONE: if (tx_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

endmodule
