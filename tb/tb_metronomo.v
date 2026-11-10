/*-----------------------------------------------------------------------
 * Testbench : tb_metronomo.v
 * Modulo    : metronomo
 *-----------------------------------------------------------------------
 * Usa periodo=1000 e fase=300 ciclos (CLK_HZ=1 MHz so para o servo).
 * Testa:
 *   1. 30 tempos exatos em fase + k*periodo (sem deriva)
 *   2. Pausa (rodando=0): sem pulsos e os tempos seguintes se deslocam
 *      exatamente pela duracao da pausa (conta so ciclos rodando)
 *   3. reinicia: volta a fase, lado = 0, 1o tempo apos `fase` ciclos
 *   4. Lado alterna a cada tempo
 *   5. modo 0 e 3: sem pulsos, nivel = 3, braco no centro
 *   6. modo 2 tratado como fixo
 *   7. Largura do PWM: centro, +A, -A conforme lado/rodando/modo
 *   8. comemora: abana entre +55 e +85 graus, mesmo com modo desligado,
 *      tem prioridade sobre o tempo e volta ao centro ao terminar
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_metronomo;

    localparam CLK_HZ  = 1_000_000;
    localparam PER_SRV = CLK_HZ / 50;
    localparam CENTRO  = CLK_HZ * 3 / 2000;
    localparam D0      = (CLK_HZ / 2000) * 30 / 90;
    localparam PERIODO = 1000;
    localparam FASE    = 300;
    localparam MEIA    = CLK_HZ / 2000;
    localparam L_BAIXA = CENTRO + MEIA * 55 / 90;
    localparam L_ALTA  = CENTRO + MEIA * 85 / 90;
    localparam T_ABANO = CLK_HZ / 4;

    reg         clock = 0;
    reg         reset = 1;
    reg         rodando = 0;
    reg         reinicia = 0;
    reg         comemora = 0;
    reg  [1:0]  modo = 2'd1;
    reg  [1:0]  nivel_in = 2'd2;
    reg  [31:0] periodo = PERIODO;
    reg  [31:0] fase = FASE;
    reg  [31:0] periodo2 = PERIODO;
    reg  [31:0] ntroca = 32'hFFFFFFFF;
    wire        tempo_pulso, lado, servo;
    wire [1:0]  nivel;

    metronomo #(.CLK_HZ(CLK_HZ)) dut (
        .clock(clock), .reset(reset), .rodando(rodando), .reinicia(reinicia), .comemora(comemora),
        .modo(modo), .nivel_in(nivel_in), .periodo(periodo), .fase(fase), .periodo2(periodo2), .ntroca(ntroca),
        .tempo_pulso(tempo_pulso), .lado(lado), .nivel(nivel), .servo(servo)
    );

    always #5 clock = ~clock;

    integer erros = 0;
    integer ecount = 0;      // ciclos rodando desde o ultimo reinicia
    integer k = 0;           // indice do proximo tempo esperado
    integer npulsos = 0;     // total no teste corrente
    integer pulsos_proibidos = 0;
    reg     lado_ant = 0;
    reg     esperado_lado = 0;

    // Monitor de tempos: o k-esimo pulso vem com ecount == FASE + k*PERIODO
    reg rodando_d, lado_d, tempo_d, comemora_d;
    reg [1:0] modo_d, nivel_d;
    always @(posedge clock) begin
        if (reinicia) begin
            ecount = 0; k = 0;
        end else begin
            if (tempo_pulso) begin
                npulsos = npulsos + 1;
                if (ecount !== (k <= ntroca ? FASE + k * PERIODO : FASE + ntroca * PERIODO + (k - ntroca) * periodo2)) begin
                    $display("[FAIL] tempo %0d em %0d ciclos", k, ecount);
                    erros = erros + 1;
                end
                if (lado !== ~lado_ant) begin
                    $display("[FAIL] lado nao alternou no tempo %0d", k);
                    erros = erros + 1;
                end
                k = k + 1;
            end
            if (rodando) ecount = ecount + 1;
        end
        if (tempo_pulso && pulsos_proibidos) begin
            $display("[FAIL] pulso em estado proibido (t=%0t)", $time);
            erros = erros + 1;
        end
        lado_ant <= lado;
    end

    // Monitor do PWM: largura medida == esperada pelo estado no latch
    reg servo_d = 0;
    integer wcnt = 0, wexp = 0, nchk = 0, n_mais = 0, n_menos = 0, n_cen = 0;
    integer n_alta = 0, n_baixa = 0, n_outra = 0, ultima = 0;
    always @(posedge clock) begin
        if (servo && !servo_d) begin
            wcnt = 1;
            if (comemora_d)
                wexp = 0;                // conferido pelas contagens n_alta/n_baixa
            else if (rodando_d && (modo_d == 2'd1 || modo_d == 2'd2))
                wexp = lado_d ? CENTRO + D0 : CENTRO - D0;
            else
                wexp = CENTRO;
        end else if (servo)
            wcnt = wcnt + 1;
        else if (servo_d) begin
            ultima = wcnt;
            if (wexp == 0) begin
                if (wcnt == L_ALTA) n_alta = n_alta + 1;
                else if (wcnt == L_BAIXA) n_baixa = n_baixa + 1;
                else n_outra = n_outra + 1;
            end
            if (wexp != 0) nchk = nchk + 1;
            if (wexp != 0 && wcnt !== wexp) begin
                $display("[FAIL] largura PWM %0d (esp %0d)", wcnt, wexp);
                erros = erros + 1;
            end
            if (wexp == CENTRO) n_cen = n_cen + 1;
            else if (wexp > CENTRO) n_mais = n_mais + 1;
            else n_menos = n_menos + 1;
        end
        servo_d <= servo;
        rodando_d <= rodando; lado_d <= lado; modo_d <= modo; nivel_d <= nivel; comemora_d <= comemora;
    end

    task ciclos;
        input integer n;
        begin repeat (n) @(posedge clock); #1; end
    endtask

    task reinicia_pulso;
        begin
            @(posedge clock); #1 reinicia = 1;
            @(posedge clock); #1 reinicia = 0;
        end
    endtask

    task confere;
        input cond;
        input [8*44-1:0] nome;
        begin
            if (cond) $display("[OK]   %0s", nome);
            else begin $display("[FAIL] %0s", nome); erros = erros + 1; end
        end
    endtask

    integer ant;

    initial begin
        #50 reset = 0;
        ciclos(5);

        // 1/2/4: 30 tempos, com pausa no meio (instante nao coincidente com tempo)
        reinicia_pulso;
        npulsos = 0;
        rodando = 1;
        ciclos(FASE + 14 * PERIODO + 450);     // 15 tempos, meio de periodo
        rodando = 0;
        pulsos_proibidos = 1;
        ciclos(2345);                           // pausa
        confere(npulsos == 15, "1. 15 tempos antes da pausa");
        confere(npulsos == 15, "2. sem pulso durante a pausa");
        pulsos_proibidos = 0;
        rodando = 1;
        ciclos(15 * PERIODO + 50);              // mais 15 (total 30)
        confere(npulsos == 30, "3. 30 tempos sem deriva");
        rodando = 0;

        // 3: reinicia no meio da musica
        reinicia_pulso;
        ciclos(7);
        rodando = 1;
        ciclos(FASE - 5);
        confere(npulsos == 30, "4. antes de FASE ainda sem tempo");
        ciclos(10);
        confere(npulsos == 31 && lado === 1'b1, "5. 1o tempo apos FASE, lado=1");
        ciclos(PERIODO);
        confere(npulsos == 32 && lado === 1'b0, "6. 2o tempo, lado alterna");
        rodando = 0;
        reinicia_pulso;
        confere(lado === 1'b0, "7. reinicia zera o lado");

        // PWM em PLAY, fixo (precisa de varios periodos de 20000 ciclos)
        rodando = 1;
        ciclos(5 * PER_SRV);
        confere(n_mais > 0 && n_menos > 0, "8. PWM em +A e -A");
        confere(nivel === 2'd0, "9. nivel 0 no modo fixo (nivel_in ignorado)");
        rodando = 0;

        // pausa/IDLE: braco no centro
        ciclos(3 * PER_SRV);
        confere(n_cen > 0, "10. braco no centro com rodando=0");

        // 5: modos desligados
        pulsos_proibidos = 1;
        ant = npulsos;
        modo = 2'd0; reinicia_pulso; rodando = 1;
        ciclos(5 * PER_SRV);
        confere(nivel === 2'd3, "11. modo 0 -> nivel 3");
        modo = 2'd3;
        ciclos(5 * PER_SRV);
        confere(nivel === 2'd3, "12. modo 3 -> nivel 3");
        pulsos_proibidos = 0;
        confere(npulsos == ant, "13. modos desligados sem pulsos");

        // 6: modo 2 = fixo
        modo = 2'd2; reinicia_pulso;
        ant = npulsos;
        ciclos(FASE + 3 * PERIODO + 10);
        confere(npulsos == ant + 4 && nivel === 2'd0, "14. modo 2 tratado como fixo");

        // 7: troca de andamento no tempo 5 (periodo2 = 600): o monitor confere
        // cada instante: tempos 0..5 em FASE+k*1000, depois +600 cada
        modo = 2'd1; rodando = 0; periodo2 = 600; ntroca = 5;
        reinicia_pulso;
        ant = npulsos;
        rodando = 1;
        ciclos(FASE + 5 * PERIODO - 2);          // 2 ciclos antes do tempo 5
        confere(npulsos == ant + 5, "16. tempos 0..4 antes da troca");
        ciclos(4);
        confere(npulsos == ant + 6, "17. tempo 5 ainda em FASE+5*PERIODO");
        ciclos(580);                             // ~tempo5+582: ainda nao
        confere(npulsos == ant + 6, "18a. tempo 6 ainda nao (antes de +PERIODO2)");
        ciclos(40);                              // ~tempo5+622: ja saiu (nao em +1000)
        confere(npulsos == ant + 7, "18. tempo 6 em +PERIODO2");
        ciclos(600);
        confere(npulsos == ant + 8, "19. tempo 7 em +PERIODO2");
        rodando = 0;
        reinicia_pulso;                          // zera o contador de tempos
        periodo2 = PERIODO; ntroca = 32'hFFFFFFFF;

        // 8: comemoracao com o metronomo desligado e fora do PLAY
        modo = 2'd0; rodando = 0;
        comemora = 1;
        ciclos(2 * PER_SRV);
        n_alta = 0; n_baixa = 0; n_outra = 0;
        pulsos_proibidos = 1;
        ciclos(3 * T_ABANO + T_ABANO / 2);
        confere(n_alta > 0 && n_baixa > 0, "20. abana entre as duas posicoes altas");
        confere(n_outra == 0, "21. so as larguras de +55 e +85 graus");
        confere(n_alta >= 20 && n_baixa >= 20, "22. ritmo de 250 ms por posicao");
        pulsos_proibidos = 0;

        // prioridade sobre o metronomo em andamento
        modo = 2'd1; rodando = 1;
        ciclos(2 * PER_SRV);
        n_alta = 0; n_baixa = 0; n_outra = 0;
        ciclos(T_ABANO + 2 * PER_SRV);
        confere(n_outra == 0 && n_alta + n_baixa > 0, "23. comemora tem prioridade sobre o tempo");
        rodando = 0; modo = 2'd0;

        // fim da tela de vitoria: volta ao centro
        comemora = 0;
        ciclos(3 * PER_SRV);
        confere(ultima == CENTRO, "24. volta ao centro ao sair da vitoria");

        confere(nchk > 10, "15. PWM medido varias vezes");
        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
