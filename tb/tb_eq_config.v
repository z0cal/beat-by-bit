/*-----------------------------------------------------------------------
 * Testbench : tb_eq_config.v
 * Modulo    : eq_config
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Reset deixa todas as faixas em 0 (sem atenuacao)
 *   2. Cada pulso avanca so a sua faixa
 *   3. Nivel 3 volta a 0 no proximo pulso
 *   4. Pulsos simultaneos em faixas diferentes
 *   5. habilita=0 ignora os pulsos
 *   6. Reset no meio zera tudo
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_eq_config;

    localparam CLK_PERIOD = 10;

    reg        clock    = 0;
    reg        reset    = 1;
    reg        habilita = 0;
    reg  [3:0] pulso    = 4'b0000;
    wire [7:0] eq;

    eq_config dut (
        .clock   (clock),
        .reset   (reset),
        .habilita(habilita),
        .pulso   (pulso),
        .eq      (eq)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    integer erros = 0;

    // Pulso de 1 ciclo nas faixas indicadas
    task press;
        input [3:0] mask;
        begin
            @(posedge clock); #1;
            pulso = mask;
            @(posedge clock); #1;
            pulso = 4'b0000;
        end
    endtask

    task expect_eq;
        input [7:0]       valor;
        input [8*40-1:0]  nome;
        begin
            if (eq === valor)
                $display("[OK]   %0s: eq = 0x%02X", nome, eq);
            else begin
                $display("[FAIL] %0s: esperado 0x%02X, obtido 0x%02X", nome, valor, eq);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("tb_eq_config.vcd");
        $dumpvars(0, tb_eq_config);

        $display("=== TB eq_config ===");
        repeat(2) @(posedge clock);
        reset = 0;
        #1;
        expect_eq(8'h00, "1. apos reset");

        habilita = 1;
        press(4'b0001);
        expect_eq(8'b00_00_00_01, "2. GRAVES +1");
        press(4'b1000);
        expect_eq(8'b01_00_00_01, "2. BRILHO +1");

        press(4'b0001);
        press(4'b0001);
        expect_eq(8'b01_00_00_11, "3. GRAVES em 3");
        press(4'b0001);
        expect_eq(8'b01_00_00_00, "3. GRAVES volta a 0");

        press(4'b0110);
        expect_eq(8'b01_01_01_00, "4. MEDIOS e AGUDOS juntos");

        habilita = 0;
        press(4'b1111);
        expect_eq(8'b01_01_01_00, "5. habilita=0 ignora");

        @(posedge clock); #1;
        reset = 1;
        @(posedge clock); #1;
        reset = 0;
        expect_eq(8'h00, "6. reset zera");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
