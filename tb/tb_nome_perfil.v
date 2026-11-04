/*-----------------------------------------------------------------------
 * Testbench : tb_nome_perfil.v
 * Modulo    : nome_perfil
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Reset: nome "---", cursor 0
 *   2. habilita=0 ignora os pulsos
 *   3. F avanca e D volta o caractere; '-' <-> '9' nas pontas
 *   4. K/J movem o cursor e param nas posicoes 2 e 0
 *   5. Cada posicao e editada de forma independente ("AN0")
 *   6. Sair do fim de musica volta o cursor a 0 e mantem o nome
 *   7. Pulsos simultaneos: D vence F, J vence K, letra e cursor juntos
 *   8. Reset volta a "---"
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_nome_perfil;

    localparam CLK_PERIOD = 10;

    reg         clock    = 0;
    reg         reset    = 1;
    reg         habilita = 0;
    reg  [3:0]  pulso    = 4'b0000;
    wire [17:0] nome;
    wire [1:0]  cursor;

    nome_perfil dut (
        .clock   (clock),
        .reset   (reset),
        .habilita(habilita),
        .pulso   (pulso),
        .nome    (nome),
        .cursor  (cursor)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    integer erros = 0;

    localparam [3:0] D = 4'b0001, F = 4'b0010, J = 4'b0100, K = 4'b1000;

    task press;
        input [3:0] mask;
        begin
            @(negedge clock);
            pulso = mask;
            @(posedge clock); #1;
            pulso = 4'b0000;
        end
    endtask

    task press_n;
        input [3:0]   mask;
        input integer n;
        integer k;
        begin
            for (k = 0; k < n; k = k + 1) press(mask);
        end
    endtask

    // Codigos esperados de c0, c1, c2 e cursor
    task confere;
        input [5:0]      c0, c1, c2;
        input [1:0]      cur;
        input [8*44-1:0] nome_teste;
        begin
            if (nome === {c2, c1, c0} && cursor === cur)
                $display("[OK]   %0s: %0d %0d %0d, cursor %0d",
                         nome_teste, c0, c1, c2, cur);
            else begin
                $display("[FAIL] %0s: %0d %0d %0d cursor %0d (esperado %0d %0d %0d cursor %0d)",
                         nome_teste, nome[5:0], nome[11:6], nome[17:12], cursor,
                         c0, c1, c2, cur);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("tb_nome_perfil.vcd");
        $dumpvars(0, tb_nome_perfil);
        $display("=== TB nome_perfil ===");

        repeat (2) @(posedge clock);
        reset = 0;
        #1;
        confere(0, 0, 0, 0, "1. reset = ---");

        press(F); press(K);
        confere(0, 0, 0, 0, "2. habilita=0 ignora");

        habilita = 1;
        press(F);
        confere(1, 0, 0, 0, "3. F: '-' -> 'A'");
        press(D); press(D);
        confere(36, 0, 0, 0, "3. D: 'A' -> '-' -> '9'");
        press(F);
        confere(0, 0, 0, 0, "3. F: '9' -> '-'");

        press(J);
        confere(0, 0, 0, 0, "4. J na posicao 0 fica em 0");
        press_n(K, 3);
        confere(0, 0, 0, 2, "4. K x3 para na posicao 2");

        // "AN0": A=1, N=14, 0=27
        press_n(J, 2);
        press(F);
        press(K); press_n(F, 14);
        press(K); press_n(D, 10);   // '-' -> '9' ... -> '0' (36 - 9 = 27)
        confere(1, 14, 27, 2, "5. AN0");

        habilita = 0;
        @(posedge clock); #1;
        confere(1, 14, 27, 0, "6. fora do fim: cursor 0, nome mantido");
        habilita = 1;
        press(D | F);
        confere(0, 14, 27, 0, "7. D e F juntos: D vence");
        press(F | K);
        confere(1, 14, 27, 1, "7. F e K juntos: letra e cursor");
        press(J | K);
        confere(1, 14, 27, 0, "7. J e K juntos: J vence");

        @(negedge clock); reset = 1;
        @(negedge clock); reset = 0;
        #1;
        confere(0, 0, 0, 0, "8. reset volta a ---");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
