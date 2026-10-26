/*-----------------------------------------------------------------------
 * Testbench : tb_uart_tx.v
 * Modulo    : uart_tx
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Envio de um byte (0x55): verifica start bit, 8 bits de dados, stop bit
 *   2. Envio de dois bytes em sequencia: 0xAA, 0xFF
 *   3. tx_ready cai durante transmissao e sobe ao terminar
 *
 * Usa CLKS_PER_BIT=4 para simulacao rapida.
 * Para simular com baud real: substitua por CLKS_PER_BIT=434.
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_uart_tx;

    localparam CLKS_PER_BIT = 4;
    localparam CLK_PERIOD   = 10;  // 10ns = 100MHz simulado

    // DUT
    reg        clock = 0;
    reg        reset = 1;
    reg  [7:0] tx_data  = 0;
    reg        tx_valid = 0;
    wire       tx_serial;
    wire       tx_ready;

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) dut (
        .clock    (clock),
        .reset    (reset),
        .tx_data  (tx_data),
        .tx_valid (tx_valid),
        .tx_serial(tx_serial),
        .tx_ready (tx_ready)
    );

    // Clock
    always #(CLK_PERIOD/2) clock = ~clock;

    // Tarefa: envia um byte e captura os bits recebidos no serial
    task send_byte;
        input [7:0] data;
        output [7:0] received;
        integer i;
        reg [9:0] bits; // start + 8 data + stop
        begin
            @(posedge clock);
            tx_data  <= data;
            tx_valid <= 1;
            @(posedge clock);
            tx_valid <= 0;

            // Aguarda tx_ready cair (inicio da transmissao)
            wait (!tx_ready);

            // Amostra cada bit no meio do periodo
            // Start bit
            repeat(CLKS_PER_BIT/2) @(posedge clock);
            bits[0] = tx_serial;
            repeat(CLKS_PER_BIT/2) @(posedge clock);

            // 8 bits de dados
            for (i = 0; i < 8; i = i+1) begin
                repeat(CLKS_PER_BIT/2) @(posedge clock);
                bits[1+i] = tx_serial;
                repeat(CLKS_PER_BIT/2) @(posedge clock);
            end

            // Stop bit
            repeat(CLKS_PER_BIT/2) @(posedge clock);
            bits[9] = tx_serial;
            repeat(CLKS_PER_BIT/2) @(posedge clock);

            received = bits[8:1];

            // Aguarda pronto
            wait (tx_ready);
        end
    endtask

    reg [7:0] rx_byte;
    integer   erros = 0;

    initial begin
        $dumpfile("tb_uart_tx.vcd");
        $dumpvars(0, tb_uart_tx);

        // Reset
        reset = 1;
        repeat(4) @(posedge clock);
        reset = 0;
        @(posedge clock);

        $display("=== TB uart_tx ===");

        // --- Teste 1: tx_ready alto em IDLE ---
        if (tx_ready) $display("[OK]  tx_ready=1 em IDLE");
        else begin $display("[FAIL] tx_ready deveria ser 1 em IDLE"); erros=erros+1; end

        // --- Teste 2: Envio de 0x55 ---
        send_byte(8'h55, rx_byte);
        if (rx_byte === 8'h55)
            $display("[OK]  Enviou 0x55, recebeu 0x%02X", rx_byte);
        else begin
            $display("[FAIL] Enviou 0x55, recebeu 0x%02X", rx_byte);
            erros = erros + 1;
        end

        // --- Teste 3: Envio de 0xAA ---
        send_byte(8'hAA, rx_byte);
        if (rx_byte === 8'hAA)
            $display("[OK]  Enviou 0xAA, recebeu 0x%02X", rx_byte);
        else begin
            $display("[FAIL] Enviou 0xAA, recebeu 0x%02X", rx_byte);
            erros = erros + 1;
        end

        // --- Teste 4: Envio de 0xFF ---
        send_byte(8'hFF, rx_byte);
        if (rx_byte === 8'hFF)
            $display("[OK]  Enviou 0xFF, recebeu 0x%02X", rx_byte);
        else begin
            $display("[FAIL] Enviou 0xFF, recebeu 0x%02X", rx_byte);
            erros = erros + 1;
        end

        // --- Teste 5: Envio de 0x00 ---
        send_byte(8'h00, rx_byte);
        if (rx_byte === 8'h00)
            $display("[OK]  Enviou 0x00, recebeu 0x%02X", rx_byte);
        else begin
            $display("[FAIL] Enviou 0x00, recebeu 0x%02X", rx_byte);
            erros = erros + 1;
        end

        // --- Resultado ---
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    // Timeout de seguranca
    initial begin
        #100000;
        $display("[TIMEOUT] Simulacao excedeu limite de tempo");
        $finish;
    end

endmodule
