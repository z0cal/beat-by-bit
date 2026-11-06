/*-----------------------------------------------------------------------
 * Testbench : tb_cfg_jogo.v
 * Modulo    : cfg_jogo
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Reset: pagina 0, modo 0, sensorial 0, sem_som 0
 *   2. Pagina 0: os botoes nao mexem nos registradores de jogo
 *   3. confirma alterna a pagina (0 -> 1 -> 0)
 *   4. Pagina 1: botao 0 alterna o metronomo 0 <-> 1 (nunca 2/3);
 *      botao 1 o sensorial; botao 2 o sem som; botao 3 reservado
 *   5. habilita=0 ignora botoes e confirma
 *   6. Os valores sobrevivem a sair e voltar das configuracoes
 *      (habilita 0 -> 1) e so o reset zera
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_cfg_jogo;

    reg        clock = 0, reset = 1, habilita = 0, confirma = 0;
    reg  [3:0] pulso = 4'b0;
    wire       pagina, sensorial, sem_som;
    wire [1:0] modo_met;

    cfg_jogo dut (
        .clock(clock), .reset(reset), .habilita(habilita), .confirma(confirma),
        .pulso(pulso), .pagina(pagina), .modo_met(modo_met),
        .sensorial(sensorial), .sem_som(sem_som)
    );

    always #5 clock = ~clock;

    integer erros = 0;

    task press;
        input [3:0] mask;
        input       conf;
        begin
            @(posedge clock); #1;
            pulso = mask; confirma = conf;
            @(posedge clock); #1;
            pulso = 4'b0; confirma = 1'b0;
        end
    endtask

    task confere;
        input [4:0]      esperado;   // {pagina, sem_som, sensorial, modo_met}
        input [8*40-1:0] nome;
        begin
            if ({pagina, sem_som, sensorial, modo_met} === esperado)
                $display("[OK]   %0s", nome);
            else begin
                $display("[FAIL] %0s: obtido %b, esperado %b", nome,
                         {pagina, sem_som, sensorial, modo_met}, esperado);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        $display("=== TB cfg_jogo ===");
        repeat (2) @(posedge clock);
        reset = 0; habilita = 1; #1;
        confere(5'b0_0_0_00, "1. apos reset");

        press(4'b0111, 0);
        confere(5'b0_0_0_00, "2. pagina 0: botoes ignorados");

        press(4'b0000, 1);
        confere(5'b1_0_0_00, "3a. confirma -> pagina 1");

        press(4'b0001, 0);
        confere(5'b1_0_0_01, "4a. botao 0: metronomo fixo");
        press(4'b0001, 0);
        confere(5'b1_0_0_00, "4b. botao 0: metronomo desligado");
        press(4'b0001, 0);
        press(4'b0010, 0);
        confere(5'b1_0_1_01, "4c. botao 1: sensorial");
        press(4'b0100, 0);
        confere(5'b1_1_1_01, "4d. botao 2: sem som");
        press(4'b1000, 0);
        confere(5'b1_1_1_01, "4e. botao 3 reservado");
        press(4'b0110, 0);
        confere(5'b1_0_0_01, "4f. botoes 1 e 2 juntos");
        press(4'b0010, 0);

        press(4'b0000, 1);
        confere(5'b0_0_1_01, "3b. confirma -> pagina 0, valores mantidos");
        press(4'b1111, 0);
        confere(5'b0_0_1_01, "2b. pagina 0 de novo: botoes ignorados");
        press(4'b0000, 1);   // pagina 1

        habilita = 0;
        press(4'b0111, 1);
        confere(5'b1_0_1_01, "5. habilita=0 ignora");
        habilita = 1;
        #1;
        confere(5'b1_0_1_01, "6a. voltar as configuracoes mantem tudo");

        @(posedge clock); #1 reset = 1;
        @(posedge clock); #1 reset = 0;
        confere(5'b0_0_0_00, "6b. reset zera");

        $display("---");
        if (erros == 0) $display("=== PASSOU: %0d erros ===", erros);
        else $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
