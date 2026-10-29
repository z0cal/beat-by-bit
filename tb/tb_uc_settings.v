/*-----------------------------------------------------------------------
 * Testbench : tb_uc_settings.v
 * Modulo    : unidade_controle
 *-----------------------------------------------------------------------
 * Testa o estado SETTINGS (0111):
 *   1. IDLE + song_ok (botao de trilha) -> SETTINGS
 *   2. song_ok e jogada em SETTINGS nao mudam o estado
 *   3. SETTINGS e inerte: limpaR=1, zera_timer=1, game_active=0
 *   4. SETTINGS + start -> IDLE (e nao segue para SELECT)
 *   5. IDLE + start continua indo para SELECT
 *   6. start e song_ok juntos em IDLE: START tem prioridade
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_uc_settings;

    localparam CLK_PERIOD = 10;

    localparam IDLE     = 4'b0000;
    localparam SELECT   = 4'b0110;
    localparam SETTINGS = 4'b0111;

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

    initial begin
        $dumpfile("tb_uc_settings.vcd");
        $dumpvars(0, tb_uc_settings);

        $display("=== TB UC: estado SETTINGS ===");
        repeat(2) @(posedge clock);
        reset = 0;
        #1;
        expect_estado(IDLE, "apos reset");

        pulse(0, 1, 1);
        expect_estado(SETTINGS, "1. IDLE + song_ok");

        pulse(0, 1, 1);
        pulse(0, 1, 0);
        expect_estado(SETTINGS, "2. song_ok/jogada em SETTINGS");

        if (limpaR !== 1'b1 || zera_timer !== 1'b1 ||
            game_active !== 1'b0 || registraR !== 1'b0) begin
            $display("[FAIL] 3. saidas em SETTINGS: limpaR=%b zera=%b active=%b regR=%b",
                     limpaR, zera_timer, game_active, registraR);
            erros = erros + 1;
        end else
            $display("[OK]   3. SETTINGS e inerte");

        pulse(1, 0, 0);
        expect_estado(IDLE, "4. SETTINGS + start");
        repeat(3) @(posedge clock); #1;
        expect_estado(IDLE, "4. continua em IDLE");

        pulse(1, 0, 0);
        expect_estado(SELECT, "5. IDLE + start");
        pulse(1, 0, 0);
        expect_estado(IDLE, "5. SELECT + start volta");

        pulse(1, 1, 0);
        expect_estado(SELECT, "6. start e song_ok juntos");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
