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
 *   Barra de energia e x2 integrados (PLAY, musica 0, eco HC-SR04):
 *     5. Sem eco (0 cm) e fora da faixa (40 cm): nao somam energia,
 *        nao contam movimento e nao geram erro; acerto normal soma 100+combo
 *     6. Mao parada a 15 cm soma so presenca (0 movimentos); vai-e-vem
 *        10 <-> 20 cm enche ate 255, conta movimentos e ativa x2_ativo
 *     7. Com x2_ativo = 1, acertos de botao somam 2 * (100 + combo)
 *     8. PAUSE congela energia/x2/movimentos; fim dos 480 quadros desliga
 *        o x2 com energia = 0; voltar ao COUNTDOWN zera os contadores
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_fd;

    reg        clock = 0, reset = 1;
    reg  [4:0] botoes_raw = 5'b0;
    reg        echo = 0;

    wire [3:0]  s_jogada;
    wire        jogada_feita, start_pulso, fim_contagem, fim_tempo, perdeu, song_ok;
    wire        registraR, limpaR, zera_timer, conta_timer, game_active;
    wire [3:0]  db_estado;
    wire        trig, servo;
    wire [7:0]  t0n0, t1n0, t2n0, t3n0;
    wire [7:0]  eq_out, dist_cm, energia, misses, combo;
    wire        x2_ativo;
    wire [6:0]  ativacoes;
    wire [15:0] movimentos, score, met_n;
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
        .song_ok_pulso(song_ok),
        .t0n0(t0n0), .t1n0(t1n0), .t2n0(t2n0), .t3n0(t3n0),
        .eq_out(eq_out), .score(score), .misses(misses),
        .combo(combo), .met_n(met_n), .met_notas(met_notas),
        .dist_cm_out(dist_cm), .energia_out(energia), .x2_ativo_out(x2_ativo),
        .ativacoes_out(ativacoes), .movimentos_out(movimentos),
        .progresso_out(progresso), .pagina_out(pagina),
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
        input [8*68-1:0] nome;
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

    // Espera a proxima nota (em qualquer slot 0..2) chegar ao centro da janela
    // (age 130), captura score/combo imediatamente antes do aperto e acerta
    function tem_130;
        input [7:0] n0, n1, n2;
        begin
            tem_130 = (n0 == 8'd130) || (n1 == 8'd130) || (n2 == 8'd130);
        end
    endfunction

    function na_janela_centro;
        input [7:0] n0, n1, n2;
        begin
            na_janela_centro = (n0 >= 8'd128 && n0 <= 8'd132) ||
                               (n1 >= 8'd128 && n1 <= 8'd132) ||
                               (n2 >= 8'd128 && n2 <= 8'd132);
        end
    endfunction

    integer score_antes, combo_antes, x2_antes;
    task acerta_proxima_nota;
        integer tr;
        begin
            while (!tem_130(ufdt.t0n0, ufdt.t0n1, ufdt.t0n2) &&
                   !tem_130(ufdt.t1n0, ufdt.t1n1, ufdt.t1n2) &&
                   !tem_130(ufdt.t2n0, ufdt.t2n1, ufdt.t2n2) &&
                   !tem_130(ufdt.t3n0, ufdt.t3n1, ufdt.t3n2))
                @(posedge clock);
            // Evita coincidir exatamente com o pulso frame_tick
            repeat (4) @(posedge clock);
            score_antes = score;
            combo_antes = combo;
            x2_antes    = x2_ativo;
            if      (na_janela_centro(ufdt.t0n0, ufdt.t0n1, ufdt.t0n2)) tr = 0;
            else if (na_janela_centro(ufdt.t1n0, ufdt.t1n1, ufdt.t1n2)) tr = 1;
            else if (na_janela_centro(ufdt.t2n0, ufdt.t2n1, ufdt.t2n2)) tr = 2;
            else                                                        tr = 3;
            aperta(tr);
        end
    endtask

    integer eq_ant, k, e_pausa, m_pausa, a_pausa, q_espera;

    initial begin
        $display("=== TB fluxo_dados: paginas, metronomo, energia e x2 ===");

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

        // ---- Jogo: barra de energia e x2 ----
        aperta(4);                    // SETTINGS -> IDLE
        aperta(4);                    // IDLE -> SELECT
        confere(db_estado == 4'd6, "5a. SELECT");
        aperta(0);                    // musica 0 -> COUNTDOWN
        confere(db_estado == 4'd1, "5b. COUNTDOWN");
        while (db_estado != 4'd2) @(posedge clock);
        #1;
        confere(energia == 0 && !x2_ativo && ativacoes == 0 && movimentos == 0,
                "5c. energia, x2, ativacoes e movimentos zerados ao entrar no PLAY");

        // Sem eco (0 cm) e fora da faixa (40 cm) antes da 1a nota (age 130 no quadro ~143)
        mao(0);  leituras(3);
        mao(40); leituras(3);
        confere(energia == 0 && movimentos == 0 && misses == 0,
                "5d. sem eco e 40 cm ignorados, sem erro");

        // Acertos normais (sem x2): 100 + combo
        acerta_proxima_nota;
        confere(!x2_antes && score == score_antes + 100 + combo_antes && combo == combo_antes + 1,
                "5e. 1o acerto sem x2 soma 100 + combo");
        acerta_proxima_nota;
        confere(!x2_antes && combo_antes >= 1 && score == score_antes + 100 + combo_antes,
                "5f. 2o acerto seguido sem x2 soma 100 + combo");

        // Mao parada a 15 cm: soma presenca (+1/leitura), 0 movimentos
        mao(15);
        leituras(6);
        confere(energia >= 8'd4 && movimentos == 16'd0 && !x2_ativo,
                "6a. mao parada a 15 cm enche barra sem contar movimentos");

        // Vai-e-vem 10 <-> 20 cm ate encher (255) e ligar x2_ativo
        k = 0;
        while (!x2_ativo && k < 120) begin
            mao((k % 2 == 0) ? 10 : 20);
            leituras(1);
            k = k + 1;
        end
        confere(x2_ativo && ativacoes == 7'd1 && movimentos >= 16'd70,
                "6b. vai-e-vem 10<->20 cm ativa x2_ativo (ativacoes=1)");

        // Acertos durante x2_ativo: pontuacao em dobro 2 * (100 + combo)
        mao(0);
        acerta_proxima_nota;
        confere(x2_antes && score == score_antes + 2 * (100 + combo_antes),
                "7a. 1o acerto com x2_ativo soma 2*(100 + combo)");
        acerta_proxima_nota;
        confere(x2_antes && combo_antes >= 1 && score == score_antes + 2 * (100 + combo_antes),
                "7b. 2o acerto com x2_ativo e combo>=1 soma 2*(100 + combo)");

        // PAUSE congela energia, x2 e movimentos mesmo com eco variando
        aperta(4);                    // PLAY -> PAUSE
        confere(db_estado == 4'd3, "8a. PAUSE");
        e_pausa = energia;
        m_pausa = movimentos;
        a_pausa = ativacoes;
        mao(10); leituras(2);
        mao(20); leituras(2);
        mao(0);  leituras(1);
        confere(energia == e_pausa && movimentos == m_pausa && ativacoes == a_pausa && x2_ativo,
                "8b. PAUSE congela esvaziamento do x2 e movimentos");

        // Retoma PLAY e espera terminar os 480 quadros do x2
        aperta(4);                    // PAUSE -> PLAY
        q_espera = 0;
        while (x2_ativo && q_espera < 600) begin
            @(posedge ufdt.frame_tick);
            #1;
            q_espera = q_espera + 1;
        end
        confere(!x2_ativo && energia == 8'd0 && ativacoes == 7'd1,
                "8c. x2 termina apos esvaziar ate 0; ativacoes permanece 1");

        // Voltar ao COUNTDOWN zera todos os registradores de energia
        aperta(4);                    // PLAY -> PAUSE
        aperta(0);                    // PAUSE + botao -> IDLE
        aperta(4);                    // IDLE -> SELECT
        aperta(0);                    // -> COUNTDOWN
        repeat (4) @(posedge clock); #1;
        confere(energia == 0 && !x2_ativo && ativacoes == 0 && movimentos == 0,
                "8d. contadores de energia/x2/movimentos zerados no COUNTDOWN");

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
