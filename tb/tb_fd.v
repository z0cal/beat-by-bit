/*-----------------------------------------------------------------------
 * Testbench : tb_fd.v
 * Modulo    : fluxo_dados + unidade_controle (HC-SR04 simulado)
 *-----------------------------------------------------------------------
 * Temporizacao reduzida por defparam/parametros: quadro de 300 ciclos (300 us),
 * clock de 1 MHz, debounce de 8 ciclos, COUNTDOWN de 6 quadros, HC-SR04 medindo a cada
 * 3 ms. Um modelo de eco responde ao TRIG com largura = dist_us.
 *
 * Testa:
 *   Configuracoes (SETTINGS):
 *     1. Pagina 0: botao 1 avanca o atenuador (eq)
 *     2. Mao a 15 cm por 16 leituras: gesto de confirmacao -> pagina 1
 *     3. Pagina 1: botao 1 liga o perfil sensorial e NAO mexe no eq;
 *        botao 0 liga o metronomo (modo 1); botao 2 liga o sem som
 *     4. Novo gesto (apos rearmar, mao fora) volta a pagina 0
 *   Nota de gesto (PLAY, musica 0, o frame_counter e posicionado perto
 *   dos gestos do chart):
 *     5. Mao a 15 cm segurada ANTES da nota nascer: nao conta erro
 *        (bad_press ignorado: misses 0, score 0)
 *     6. Gesto dentro da janela: acerto de gesto (+100, combo 1,
 *        apresentados 1, acertados 1); metricas de trilha intocadas
 *     7. Mao a 40 cm (fora da faixa) no 2o gesto: nada acontece ate a
 *        nota escapar: miss, combo 0, apresentados 2, acertados 1
 *     8. Contadores zeram ao voltar ao COUNTDOWN
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_fd;

    reg        clock = 0, reset = 1;
    reg  [4:0] botoes_raw = 5'b0;
    reg        echo = 0;

    wire [3:0] s_jogada;
    wire       jogada_feita, start_pulso, fim_contagem, fim_tempo, perdeu, song_ok;
    wire       registraR, limpaR, zera_timer, conta_timer, game_active;
    wire [3:0] db_estado;
    wire       trig, servo;
    wire [7:0]  eq_out, dist_cm, gesto_age, gestos_apres, gestos_acertos, misses, combo;
    wire [15:0] score, met_n;
    wire [47:0] met_notas;
    wire [2:0]  progresso;
    wire        pagina, sem_som, sensorial;
    wire [1:0]  modo_met;

    fluxo_dados #(.HC_CLK_HZ(1_000_000), .HC_CICLO_MS(3), .HC_TIMEOUT_US(2800)) ufdt (
        .clock(clock), .reset(reset),
        .limpaR(limpaR), .registraR(registraR), .zera_timer(zera_timer),
        .conta_timer(conta_timer), .game_active(game_active), .db_estado(db_estado),
        .botoes_raw(botoes_raw),
        .hcsr04_echo(echo), .hcsr04_trig(trig), .servo_pwm(servo),
        .s_jogada(s_jogada), .jogada_feita(jogada_feita), .start_pulso(start_pulso),
        .fim_contagem(fim_contagem), .fim_tempo(fim_tempo), .perdeu(perdeu),
        .song_ok_pulso(song_ok), .eq_out(eq_out), .score(score), .misses(misses),
        .combo(combo), .met_n(met_n), .met_notas(met_notas),
        .dist_cm_out(dist_cm), .gesto_age(gesto_age), .gestos_apres(gestos_apres),
        .gestos_acertos(gestos_acertos), .progresso_out(progresso), .pagina_out(pagina),
        .sem_som_out(sem_som), .sensorial_out(sensorial), .modo_met_out(modo_met)
    );

    unidade_controle uuct (
        .clock(clock), .reset(reset), .start(start_pulso), .song_ok(song_ok),
        .jogada(jogada_feita), .fim_contagem(fim_contagem), .fim_tempo(fim_tempo),
        .perdeu(perdeu), .registraR(registraR), .limpaR(limpaR),
        .zera_timer(zera_timer), .conta_timer(conta_timer),
        .game_active(game_active), .db_estado(db_estado)
    );

    defparam ufdt.u_frame_counter.M = 300;
    defparam ufdt.u_countdown.M     = 6;
    defparam ufdt.u_db_btn0.DEBOUNCE_CYCLES  = 8;
    defparam ufdt.u_db_btn1.DEBOUNCE_CYCLES  = 8;
    defparam ufdt.u_db_btn2.DEBOUNCE_CYCLES  = 8;
    defparam ufdt.u_db_btn3.DEBOUNCE_CYCLES  = 8;
    defparam ufdt.u_db_start.DEBOUNCE_CYCLES = 8;

    always #500 clock = ~clock;   // 1 MHz: 1 ciclo = 1 us (o hcsr04 recebe HC_CLK_HZ = 1 MHz)

    // Modelo do HC-SR04: eco sobe 100 us apos o TRIG e dura dist_us (58 us/cm)
    integer dist_us = 0;    // 0 = sem eco
    always @(negedge trig) begin
        #100_000;
        if (dist_us != 0) begin
            echo = 1;
            #(dist_us * 1000);
            echo = 0;
        end
    end

    integer erros = 0;

    task confere;
        input            cond;
        input [8*56-1:0] nome;
        begin
            if (cond) $display("[OK]   %0s", nome);
            else begin
                $display("[FAIL] %0s", nome);
                erros = erros + 1;
            end
        end
    endtask

    // Aperta e solta o botao i (0..4) respeitando o debounce reduzido
    task aperta;
        input integer i;
        begin
            @(negedge clock); botoes_raw[i] = 1'b1;
            repeat (6) @(posedge clock);
            @(negedge clock); botoes_raw[i] = 1'b0;
            repeat (14) @(posedge clock);
            #1;
        end
    endtask

    task mao;   // distancia em cm; 0 = sem mao
        input integer cm;
        begin dist_us = cm * 58; end
    endtask

    // Espera n leituras do sensor (3 ms cada)
    task leituras;
        input integer n;
        begin repeat (n) #3_000_000; end
    endtask

    // Posiciona o frame_counter (so por simulacao) e espera ele chegar em alvo
    task vai_para;
        input integer f;
        begin
            @(negedge clock);
            ufdt.frame_counter = f;
        end
    endtask

    task espera_frame;
        input integer f;
        begin
            while (ufdt.frame_counter < f) @(posedge clock);
            #1;
        end
    endtask

    integer eq_ant, miss0;

    initial begin
        $display("=== TB fluxo_dados: gesto, paginas e metronomo ===");

        repeat (4) @(posedge clock);
        reset = 0;
        repeat (4) @(posedge clock);

        // ---- Configuracoes ----
        aperta(0);   // IDLE + botao -> SETTINGS
        confere(db_estado == 4'd7, "0. IDLE + botao -> SETTINGS");
        aperta(1);
        confere(eq_out == 8'b0000_0100 && pagina == 0, "1. pagina 0: botao 1 avanca eq[3:2]");
        eq_ant = eq_out;

        mao(15);
        leituras(10);
        confere(pagina == 0 && progresso != 0, "2a. 10 leituras: ainda pagina 0, progresso > 0");
        leituras(10);
        confere(pagina == 1, "2b. gesto de confirmacao troca para a pagina 1");

        aperta(1); aperta(0); aperta(2);
        confere(sensorial && modo_met == 2'd1 && sem_som, "3a. pagina 1: sensorial, metronomo fixo, sem som");
        confere(eq_out == eq_ant, "3b. pagina 1: eq intocado");
        aperta(0);
        confere(modo_met == 2'd0, "3c. botao 0 alterna o metronomo de volta (0)");
        aperta(0);

        // mao sai (>= 4 leituras fora) e volta: nova confirmacao
        mao(0);
        leituras(6);
        mao(15);
        leituras(20);
        confere(pagina == 0, "4. novo gesto volta a pagina 0");
        mao(0);
        aperta(1);
        confere(eq_out != eq_ant && sensorial, "4b. pagina 0: eq responde; registradores da pagina 1 mantidos");

        // ---- Jogo ----
        aperta(4);                    // SETTINGS -> IDLE
        aperta(4);                    // IDLE -> SELECT
        confere(db_estado == 4'd6, "5a. SELECT");
        aperta(0);                    // musica 0 -> COUNTDOWN
        confere(db_estado == 4'd1, "5b. COUNTDOWN");
        while (db_estado != 4'd2) @(posedge clock);
        #1;
        confere(gestos_apres == 0 && gestos_acertos == 0, "5c. contadores de gesto zerados no PLAY");

        // Mao a 15 cm desde antes da nota nascer (gesto 1 do chart no quadro 1178)
        mao(15);
        vai_para(1168);
        espera_frame(1176);
        leituras(3);
        confere(misses == 0 && score == 0 && combo == 0, "5d. mao na faixa sem nota: nenhum erro (bad_press ignorado)");
        espera_frame(1178 + 20);
        confere(gesto_age != 8'hFF && gesto_age > 8'd10 && gesto_age < 8'd30, "6a. nota de gesto nasce (bit 4 da ROM), age ~20");
        espera_frame(1178 + 150);
        confere(gestos_acertos == 1 && gestos_apres == 1, "6b. gesto acertado: apresentados 1, acertados 1");
        confere(score == 16'd100 && combo == 8'd1 && misses == 0, "6c. +100, combo 1, sem miss");
        confere(met_n == 0 && met_notas == 0, "6d. metricas de trilha intocadas");
        confere(gesto_age == 8'hFF, "6e. slot de gesto livre depois do acerto");

        // 2o gesto do chart (quadro 1795) com a mao fora da faixa (40 cm)
        mao(40);
        leituras(2);
        vai_para(1787);
        espera_frame(1795 + 140);
        miss0 = misses;   // as 2 notas de botao do trecho anterior ja escaparam (nao ha jogador)
        confere(gestos_acertos == 1 && gestos_apres == 1, "7a. mao a 40 cm: nada acontece na janela");
        espera_frame(1795 + 156);
        confere(gestos_apres == 2 && gestos_acertos == 1, "7b. escape conta como apresentado, nao como acerto");
        confere(misses == miss0 + 1 && combo == 0 && score == 16'd100, "7c. escape: miss, combo zera, score igual");

        // Fim: PAUSE congela, voltar ao COUNTDOWN zera
        aperta(4);                    // PLAY -> PAUSE
        confere(db_estado == 4'd3, "8a. PAUSE");
        aperta(4);                    // PAUSE -> PLAY
        aperta(4);                    // PLAY -> PAUSE
        aperta(0);                    // PAUSE + botao -> IDLE
        aperta(4);                    // IDLE -> SELECT
        aperta(0);                    // -> COUNTDOWN
        repeat (4) @(posedge clock); #1;
        confere(gestos_apres == 0 && gestos_acertos == 0, "8b. contadores zerados no COUNTDOWN");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

    initial begin
        #4_000_000_000;
        $fatal(1, "[TIMEOUT]");
    end

endmodule
