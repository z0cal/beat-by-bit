/*-----------------------------------------------------------------------
 * Testbench : tb_taxa_acerto.v
 * Modulo    : taxa_acerto (divisor sequencial + digitos decimais)
 *-----------------------------------------------------------------------
 * Testa taxa = floor(100 * acertos / notas) e os digitos:
 *   - notas = 0 da 0 (sem divisao por zero)
 *   - casos comuns (5/6 = 83, 1/3 = 33, 2/3 = 66)
 *   - 100% e limite (acertos > notas satura em 100)
 *   - arredondamento para baixo (999/1000 = 99, 7/1000 = 0)
 *   - valores extremos (65535 acertos, 16383 notas)
 *   - a saida acompanha a troca das entradas
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_taxa_acerto;

    localparam CLK_PERIOD = 10;

    reg         clock   = 0;
    reg         reset   = 1;
    reg  [15:0] acertos = 16'd0;
    reg  [13:0] notas   = 14'd0;
    wire [6:0]  taxa;
    wire [3:0]  centena, dezena, unidade;

    taxa_acerto dut (
        .clock  (clock),
        .reset  (reset),
        .acertos(acertos),
        .notas  (notas),
        .taxa   (taxa),
        .centena(centena),
        .dezena (dezena),
        .unidade(unidade)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    integer erros = 0;

    // Aplica as entradas e espera duas divisoes completas (~50 ciclos)
    task caso;
        input [15:0] a;
        input [13:0] n;
        input [6:0]  esperado;
        begin
            @(negedge clock);
            acertos = a;
            notas   = n;
            repeat (60) @(posedge clock);
            #1;
            if (taxa === esperado &&
                centena === esperado / 100 &&
                dezena  === (esperado % 100) / 10 &&
                unidade === esperado % 10)
                $display("[OK]   %0d/%0d -> %0d%% (%0d %0d %0d)",
                         a, n, taxa, centena, dezena, unidade);
            else begin
                $display("[FAIL] %0d/%0d -> %0d%% (%0d %0d %0d), esperado %0d%%",
                         a, n, taxa, centena, dezena, unidade, esperado);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("tb_taxa_acerto.vcd");
        $dumpvars(0, tb_taxa_acerto);

        $display("=== TB taxa_acerto ===");
        repeat (3) @(posedge clock);
        reset = 0;

        caso(16'd0,     14'd0,     7'd0);
        caso(16'd5,     14'd6,     7'd83);
        caso(16'd1,     14'd3,     7'd33);
        caso(16'd2,     14'd3,     7'd66);
        caso(16'd57,    14'd57,    7'd100);
        caso(16'd0,     14'd57,    7'd0);
        caso(16'd999,   14'd1000,  7'd99);
        caso(16'd7,     14'd1000,  7'd0);
        caso(16'd9,     14'd10,    7'd90);
        caso(16'd1,     14'd20,    7'd5);
        caso(16'd8,     14'd5,     7'd100);
        caso(16'd65535, 14'd16383, 7'd100);
        caso(16'd16000, 14'd16383, 7'd97);
        caso(16'd74,    14'd74,    7'd100);
        caso(16'd3,     14'd0,     7'd0);

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
