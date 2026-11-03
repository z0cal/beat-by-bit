/*-----------------------------------------------------------------------
 * Testbench : tb_uart_link.v
 * Modulo    : uart_link (uart_rx + watchdog de confirmacao do PC)
 *-----------------------------------------------------------------------
 * Testa:
 *   1. link_ok=0 apos reset
 *   2. uart_rx recebe bytes corretos (0x55, 0xA5, 0x00, 0xFF)
 *   3. byte diferente de ACK (0x55) nao acende link_ok
 *   4. ACK (0xA5) acende link_ok
 *   5. ACKs periodicos mantem link_ok aceso alem de um TIMEOUT
 *   6. sem ACK por mais de TIMEOUT, link_ok apaga
 *   7. pulso curto em 0 na linha (ruido) nao gera byte
 *
 * Usa CLKS_PER_BIT=8 e TIMEOUT_CYCLES=400 para simulacao rapida.
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_uart_link;

    localparam CLKS_PER_BIT   = 8;
    localparam TIMEOUT_CYCLES = 400;
    localparam CLK_PERIOD     = 10;

    reg  clock = 0;
    reg  reset = 1;
    reg  rx_serial = 1;
    wire link_ok;

    uart_link #(
        .CLKS_PER_BIT  (CLKS_PER_BIT),
        .TIMEOUT_CYCLES(TIMEOUT_CYCLES)
    ) dut (
        .clock    (clock),
        .reset    (reset),
        .rx_serial(rx_serial),
        .link_ok  (link_ok)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    // Captura cada byte entregue pelo uart_rx interno
    reg [7:0] ultimo_byte;
    integer   n_bytes = 0;
    always @(posedge clock)
        if (dut.u_rx.rx_valid) begin
            ultimo_byte <= dut.u_rx.rx_data;
            n_bytes     <= n_bytes + 1;
        end

    // Envia um byte 8N1 na linha serial (LSB primeiro)
    task send_byte;
        input [7:0] data;
        integer i;
        begin
            rx_serial = 0;
            repeat (CLKS_PER_BIT) @(posedge clock);
            for (i = 0; i < 8; i = i + 1) begin
                rx_serial = data[i];
                repeat (CLKS_PER_BIT) @(posedge clock);
            end
            rx_serial = 1;
            repeat (CLKS_PER_BIT) @(posedge clock);
            repeat (4) @(posedge clock);  // folga para o sincronizador
        end
    endtask

    integer erros = 0;

    task confere_byte;
        input [7:0] esperado;
        begin
            if (ultimo_byte === esperado)
                $display("[OK]  recebeu 0x%02X", esperado);
            else begin
                $display("[FAIL] esperava 0x%02X, recebeu 0x%02X", esperado, ultimo_byte);
                erros = erros + 1;
            end
        end
    endtask

    task confere_link;
        input       esperado;
        input [8*40-1:0] msg;
        begin
            if (link_ok === esperado)
                $display("[OK]  link_ok=%0d: %0s", esperado, msg);
            else begin
                $display("[FAIL] link_ok=%b, esperava %0d: %0s", link_ok, esperado, msg);
                erros = erros + 1;
            end
        end
    endtask

    integer n_antes;

    initial begin
        $dumpfile("tb_uart_link.vcd");
        $dumpvars(0, tb_uart_link);

        repeat (4) @(posedge clock);
        reset = 0;
        repeat (4) @(posedge clock);

        $display("=== TB uart_link ===");

        confere_link(0, "apos reset");

        send_byte(8'h55);
        confere_byte(8'h55);
        confere_link(0, "byte 0x55 nao e ACK");

        send_byte(8'h00);
        confere_byte(8'h00);
        send_byte(8'hFF);
        confere_byte(8'hFF);
        confere_link(0, "bytes 0x00/0xFF nao sao ACK");

        send_byte(8'hA5);
        confere_byte(8'hA5);
        confere_link(1, "ACK recebido");

        // ACK a cada ~TIMEOUT/2: deve continuar aceso por 3 TIMEOUTs
        repeat (6) begin
            repeat (TIMEOUT_CYCLES/2 - 10*CLKS_PER_BIT) @(posedge clock);
            send_byte(8'hA5);
        end
        confere_link(1, "ACKs periodicos");

        // Ruido: pulso em 0 menor que meio bit nao e start bit
        n_antes = n_bytes;
        rx_serial = 0;
        repeat (CLKS_PER_BIT/2 - 2) @(posedge clock);
        rx_serial = 1;
        repeat (12*CLKS_PER_BIT) @(posedge clock);
        if (n_bytes == n_antes)
            $display("[OK]  pulso curto ignorado");
        else begin
            $display("[FAIL] pulso curto gerou byte 0x%02X", ultimo_byte);
            erros = erros + 1;
        end

        // Sem ACK: apaga depois de TIMEOUT
        repeat (TIMEOUT_CYCLES + 10) @(posedge clock);
        confere_link(0, "timeout sem ACK");

        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    initial begin
        #200000;
        $fatal(1, "[TIMEOUT] Simulacao excedeu limite de tempo");
    end

endmodule
