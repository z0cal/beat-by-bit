/*-----------------------------------------------------------------------
 * Testbench : tb_energia.v
 * Modulo    : energia
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Leituras 0 (sem eco) e fora da faixa (< 5 ou > 30 cm) ignoradas
 *   2. Enchimento com a mao parada (+1 por leitura valida, 0 movimentos)
 *   3. Primeiro movimento so a partir da 2a leitura valida seguida, e
 *      respeitando LIMIAR_MOV = 3 cm (aproximar e afastar)
 *   4. Leitura invalida entre leituras validas desarma comparacao de mov.
 *   5. Saturacao em 255 e ativacao automatica do x2 (ativacoes = 1)
 *   6. Esvaziamento linear em 480 quadros sem encher com novas leituras,
 *      voltando a 0 no fim e retomando o enchimento
 *   7. Congelamento em PAUSE (game_active = 0) tanto enchendo quanto no x2
 *   8. Contagem de multiplas ativacoes (e saturacao em 127) e movimentos
 *   9. Zera tudo no COUNTDOWN (zera = 1)
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_energia;

    reg        clock       = 1'b0;
    reg        reset       = 1'b1;
    reg        zera        = 1'b0;
    reg        game_active = 1'b0;
    reg        frame_tick  = 1'b0;
    reg        leitura_ok  = 1'b0;
    reg  [7:0] dist_cm     = 8'd0;

    wire [7:0]  energia;
    wire        x2_ativo;
    wire [6:0]  ativacoes;
    wire [15:0] movimentos;

    energia dut (
        .clock      (clock),
        .reset      (reset),
        .zera       (zera),
        .game_active(game_active),
        .frame_tick (frame_tick),
        .leitura_ok (leitura_ok),
        .dist_cm    (dist_cm),
        .energia    (energia),
        .x2_ativo   (x2_ativo),
        .ativacoes  (ativacoes),
        .movimentos (movimentos)
    );

    always #5 clock = ~clock;

    integer erros = 0;

    task confere;
        input            cond;
        input [8*72-1:0] msg;
        begin
            if (cond) $display("[OK]   %0s", msg);
            else begin
                $display("[FAIL] %0s (energia=%0d x2=%0b ativ=%0d mov=%0d)",
                         msg, energia, x2_ativo, ativacoes, movimentos);
                erros = erros + 1;
            end
        end
    endtask

    task pulso_leitura;
        input [7:0] d;
        begin
            @(negedge clock);
            dist_cm    = d;
            leitura_ok = 1'b1;
            @(posedge clock); #1;
            @(negedge clock);
            leitura_ok = 1'b0;
        end
    endtask

    task pulso_quadro;
        begin
            @(negedge clock);
            frame_tick = 1'b1;
            @(posedge clock); #1;
            @(negedge clock);
            frame_tick = 1'b0;
        end
    endtask

    task n_quadros;
        input integer n;
        integer k;
        begin
            for (k = 0; k < n; k = k + 1)
                pulso_quadro;
        end
    endtask

    integer i, c;

    initial begin
        $display("=== TB energia ===");

        repeat (3) @(posedge clock);
        reset = 1'b0;
        @(posedge clock); #1;
        confere(energia == 0 && !x2_ativo && ativacoes == 0 && movimentos == 0,
                "0. estado inicial zerado apos reset");

        // 1. Leituras 0 e fora da faixa ignoradas
        game_active = 1'b1;
        pulso_leitura(8'd0);
        pulso_leitura(8'd4);
        pulso_leitura(8'd31);
        pulso_leitura(8'd60);
        pulso_leitura(8'd255);
        confere(energia == 0 && movimentos == 0 && !x2_ativo,
                "1. leituras 0, 4, 31, 60 e 255 cm ignoradas");

        // 2. Enchimento com mao parada (5 cm, 30 cm e 15 cm) e limiar < 3 cm
        pulso_leitura(8'd5);    // 1a leitura valida: +1, sem movimento
        confere(energia == 1 && movimentos == 0,
                "2a. 1a leitura valida (5 cm): +1 energia, 0 movimentos");
        pulso_leitura(8'd5);    // mao parada: +1
        pulso_leitura(8'd7);    // delta = 2 < 3: +1, sem movimento
        pulso_leitura(8'd5);    // delta = 2 < 3: +1, sem movimento
        confere(energia == 4 && movimentos == 0,
                "2b. mao parada / variacao < 3 cm soma so presenca (+1)");

        // 3. Movimento a partir da 2a leitura valida (afastar e aproximar, limiar = 3)
        pulso_leitura(8'd8);    // |8 - 5| = 3 >= 3 -> +3, mov = 1
        confere(energia == 7 && movimentos == 1,
                "3a. afastar 3 cm (5->8): +3 energia e 1 movimento");
        pulso_leitura(8'd5);    // |5 - 8| = 3 >= 3 -> +3, mov = 2
        confere(energia == 10 && movimentos == 2,
                "3b. aproximar 3 cm (8->5): +3 energia e 2 movimentos");
        pulso_leitura(8'd30);   // limite superior valido: |30 - 5| = 25 >= 3 -> +3, mov = 3
        confere(energia == 13 && movimentos == 3,
                "3c. limite 30 cm valido: +3 energia e 3 movimentos");

        // 4. Leitura fora da faixa / 0 entre leituras validas: nao conta movimento
        //    e exige 2 leituras validas seguidas de novo para novo movimento
        pulso_leitura(8'd0);    // sem eco: ignorada e desarma
        pulso_leitura(8'd10);   // 1a valida apos sem eco: so +1 (energia = 14), mov continua 3
        confere(energia == 14 && movimentos == 3,
                "4a. 1a leitura valida apos sem eco: +1 e nao conta movimento");
        pulso_leitura(8'd40);   // fora da faixa: ignorada e desarma
        pulso_leitura(8'd25);   // 1a valida apos fora da faixa: so +1 (energia = 15), mov = 3
        confere(energia == 15 && movimentos == 3,
                "4b. 1a leitura valida apos fora da faixa: +1 e sem movimento");
        pulso_leitura(8'd10);   // 2a valida seguida: |10 - 25| = 15 >= 3 -> +3 (18), mov = 4
        confere(energia == 18 && movimentos == 4,
                "4c. 2a leitura valida seguida conta movimento novamente");

        // 5. Congelamento em PAUSE durante enchimento
        game_active = 1'b0;
        pulso_leitura(8'd25);
        pulso_leitura(8'd10);
        n_quadros(10);
        confere(energia == 18 && movimentos == 4 && !x2_ativo,
                "5. PAUSE (game_active=0) congela enchimento e movimentos");
        game_active = 1'b1;

        // 6. Saturacao em 255 e ativacao do x2
        // De energia = 18 ate 254 faltam 236 pontos: 236 leituras com mao parada em 10 cm
        for (i = 0; i < 236; i = i + 1)
            pulso_leitura(8'd10);
        confere(energia == 254 && !x2_ativo && ativacoes == 0 && movimentos == 4,
                "6a. 236 leituras paradas levam energia a 254 sem ligar x2");

        // Movimento em 254 (+3 = 257 -> satura em 255 e ativa x2!)
        pulso_leitura(8'd20);
        confere(energia == 255 && x2_ativo && ativacoes == 1 && movimentos == 5,
                "6b. saturacao em 255 ativa x2 imediatamente (ativacoes=1, mov=5)");

        // 7. Esvaziamento linear em 480 quadros sem encher (mesmo com leituras na faixa)
        // Testa leituras durante o x2: a barra NAO pode subir
        n_quadros(120);   // restam 360/480 -> floor(360*255/480) = 191
        pulso_leitura(8'd10);
        pulso_leitura(8'd20);
        confere(x2_ativo && energia == 8'd191,
                "7a. apos 120 quadros (restam 360): energia=191, leituras nao enchem");

        // PAUSE no meio do x2 congela o esvaziamento
        game_active = 1'b0;
        n_quadros(50);
        pulso_leitura(8'd10);
        confere(x2_ativo && energia == 8'd191,
                "7b. PAUSE durante o x2 congela tempo restante e energia");
        game_active = 1'b1;

        n_quadros(120);   // restam 240/480 -> floor(240*255/480) = 127
        confere(x2_ativo && energia == 8'd127,
                "7c. apos 240 quadros (metade): energia=127");

        n_quadros(120);   // restam 120/480 -> floor(120*255/480) = 63
        confere(x2_ativo && energia == 8'd63,
                "7d. apos 360 quadros: energia=63");

        n_quadros(119);   // 479 quadros decorridos: resta 1 quadro, x2 ainda ativo
        confere(x2_ativo && energia == 8'd0,
                "7e. no 479o quadro x2 continua ativo (resta 1 quadro)");

        n_quadros(1);     // 480o quadro: x2 termina e energia = 0
        confere(!x2_ativo && energia == 8'd0 && ativacoes == 1,
                "7f. no 480o quadro x2 desliga e energia zera");

        // 8. Volta a encher apos o x2 e conta 2a ativacao (com vai-e-vem)
        pulso_leitura(8'd0);    // limpa historico para comecar do zero
        pulso_leitura(8'd10);   // +1 (energia = 1)
        for (i = 0; i < 85; i = i + 1)
            pulso_leitura((i % 2 == 0) ? 8'd20 : 8'd10); // 85 * 3 = 255 -> soma 256 -> satura
        confere(x2_ativo && energia == 255 && ativacoes == 2,
                "8a. 2a ativacao com vai-e-vem em 86 leituras (~5,16 s)");

        // Saturacao de ativacoes em 127 (7 bits)
        for (c = 0; c < 128; c = c + 1) begin
            n_quadros(480);
            pulso_leitura(8'd0);
            pulso_leitura(8'd10);
            for (i = 0; i < 85; i = i + 1)
                pulso_leitura((i % 2 == 0) ? 8'd20 : 8'd10);
        end
        confere(x2_ativo && ativacoes == 7'd127,
                "8b. ativacoes saturam em 127 apos >127 ciclos de x2");

        // 9. Zera no COUNTDOWN (zera = 1) mesmo com x2 ativo
        @(negedge clock);
        zera        = 1'b1;
        game_active = 1'b0;
        @(posedge clock); #1;
        @(negedge clock);
        zera = 1'b0;
        confere(energia == 0 && !x2_ativo && ativacoes == 0 && movimentos == 0,
                "9a. zera=1 (COUNTDOWN) limpa energia, x2, ativacoes e movimentos");

        // Verifica que apos zera=1 o 1o movimento exige novamente 2 leituras validas
        game_active = 1'b1;
        pulso_leitura(8'd25);
        confere(energia == 1 && movimentos == 0,
                "9b. apos COUNTDOWN, 1a leitura valida nao conta movimento");
        pulso_leitura(8'd10);
        confere(energia == 4 && movimentos == 1,
                "9c. apos COUNTDOWN, 2a leitura valida com delta>=3 conta movimento");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

    initial begin
        #10_000_000;
        $fatal(1, "[TIMEOUT]");
    end

endmodule
