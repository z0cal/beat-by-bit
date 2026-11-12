/*-----------------------------------------------------------------------
 * Testbench : tb_uc_nome.v
 * Modulo    : unidade_controle
 *-----------------------------------------------------------------------
 * Testa o estado NOME (1000), a tela do perfil depois do fim de musica:
 *   1. END_WIN + start -> NOME (e nao direto para IDLE)
 *   2. song_ok e jogada em NOME nao mudam o estado
 *   3. NOME e inerte: zera_timer=1, game_active=0, limpaR=0 (as
 *      metricas e o score seguem no pacote ate o PC salvar)
 *   4. NOME + start -> IDLE
 *   5. END_LOSE + start -> NOME -> start -> IDLE
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_uc_nome;

    localparam CLK_PERIOD = 10;

    localparam IDLE     = 4'b0000;
    localparam SELECT   = 4'b0110;
    localparam COUNTDOWN = 4'b0001;
    localparam PLAY      = 4'b0010;
    localparam END_WIN   = 4'b0100;
    localparam END_LOSE  = 4'b0101;
    localparam NOME      = 4'b1000;

    reg clock = 0, reset = 1;
    reg start = 0, song_ok = 0, jogada = 0;
    reg fim_contagem = 0, fim_tempo = 0, perdeu = 0;
    wire registraR, limpaR, zera_timer, conta_timer, game_active;
    wire [3:0] db_estado;

    unidade_controle dut (
        .clock       (clock),
        .reset       (reset),
        .start       (start),
        .song_ok     (song_ok),
        .jogada      (jogada),
        .fim_contagem(fim_contagem),
        .fim_tempo   (fim_tempo),
        .perdeu      (perdeu),
        .registraR   (registraR),
        .limpaR      (limpaR),
        .zera_timer  (zera_timer),
        .conta_timer (conta_timer),
        .game_active (game_active),
        .db_estado   (db_estado)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    integer erros = 0;

    // Pulso de 1 ciclo; retorna 1 ciclo depois, com o estado ja atualizado
    task pulse;
        input s, o, j;
        begin
            @(posedge clock); #1;
            start = s; song_ok = o; jogada = j;
            @(posedge clock); #1;
            start = 0; song_ok = 0; jogada = 0;
            @(posedge clock); #1;
        end
    endtask

    task expect_estado;
        input [3:0]      valor;
        input [8*40-1:0] nome;
        begin
            if (db_estado === valor)
                $display("[OK]   %0s: estado = %b", nome, db_estado);
            else begin
                $display("[FAIL] %0s: esperado %b, obtido %b", nome, valor, db_estado);
                erros = erros + 1;
            end
        end
    endtask

    // IDLE -> SELECT -> COUNTDOWN -> PLAY
    task ate_play;
        begin
            pulse(1, 0, 0);
            pulse(0, 1, 0);
            expect_estado(COUNTDOWN, "musica escolhida");
            @(posedge clock); #1; fim_contagem = 1;
            @(posedge clock); #1; fim_contagem = 0;
            expect_estado(PLAY, "fim da contagem");
        end
    endtask

    initial begin
        $dumpfile("tb_uc_nome.vcd");
        $dumpvars(0, tb_uc_nome);

        $display("=== TB UC: estado NOME ===");
        repeat(2) @(posedge clock);
        reset = 0;
        #1;
        expect_estado(IDLE, "apos reset");

        ate_play;
        @(posedge clock); #1; fim_tempo = 1;
        @(posedge clock); #1; fim_tempo = 0;
        expect_estado(END_WIN, "fim do chart");
        pulse(0, 1, 1);
        expect_estado(END_WIN, "botao de trilha em END_WIN");

        pulse(1, 0, 0);
        expect_estado(NOME, "1. END_WIN + start");

        pulse(0, 1, 1);
        pulse(0, 1, 0);
        expect_estado(NOME, "2. song_ok/jogada em NOME");

        if (limpaR !== 1'b0 || zera_timer !== 1'b1 ||
            game_active !== 1'b0 || registraR !== 1'b0) begin
            $display("[FAIL] 3. saidas em NOME: limpaR=%b zera=%b active=%b regR=%b",
                     limpaR, zera_timer, game_active, registraR);
            erros = erros + 1;
        end else
            $display("[OK]   3. NOME e inerte e nao limpa os registros");

        pulse(1, 0, 0);
        expect_estado(IDLE, "4. NOME + start");

        ate_play;
        @(posedge clock); #1; perdeu = 1;
        @(posedge clock); #1; perdeu = 0;
        expect_estado(END_LOSE, "10 erros");
        pulse(1, 0, 0);
        expect_estado(NOME, "5. END_LOSE + start");
        pulse(1, 0, 0);
        expect_estado(IDLE, "5. NOME + start");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
