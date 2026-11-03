/*-----------------------------------------------------------------------
 * Testbench : tb_metricas.v
 * Modulo    : metricas (com 4 note_track reais, ligados como no
 *             fluxo_dados)
 *-----------------------------------------------------------------------
 * Cenarios do relatorio (Semana 1):
 *   2. Assincronia na trilha 0: toques nas idades 125, 130, 133, 141 e
 *      119 sao acertos; 118 e 142 ficam fora da janela e nao mudam os
 *      acumuladores. Ao fim: N=5, soma_e=-2, soma_abs=30, soma_e2=276.
 *   2b. Com game_active=0 (PAUSE) os acumuladores ficam congelados.
 *   2c. zera (COUNTDOWN) leva tudo a zero.
 *   3. Contadores por trilha: t0 3 notas/2 acertos, t1 2/2,
 *      t2 1/0 (toque fora da janela nao conta como nota), t3 0/0.
 *   4. Acertos em duas trilhas no mesmo ciclo (e=-3 e e=+1).
 *   5. Maior combo da sessao.
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_metricas;

    localparam CLK_PERIOD = 10;

    reg        clock       = 0;
    reg        reset       = 1;
    reg        frame_tick  = 0;
    reg        game_active = 0;
    reg        zera        = 0;
    reg  [3:0] spawn_en    = 4'b0000;
    reg  [3:0] btn         = 4'b0000;
    reg  [7:0] combo       = 8'd0;

    wire [7:0] n0 [0:3];
    wire [7:0] n1 [0:3];
    wire [7:0] n2 [0:3];
    wire [3:0] hit_pulse, escape_pulse, bad_press;
    wire [7:0] hit_age [0:3];

    genvar g;
    generate
        for (g = 0; g < 4; g = g + 1) begin : trilha
            note_track u_nt (
                .clock       (clock),
                .reset       (reset),
                .frame_tick  (frame_tick),
                .game_active (game_active),
                .spawn_en    (spawn_en[g]),
                .btn_press   (btn[g]),
                .note0       (n0[g]),
                .note1       (n1[g]),
                .note2       (n2[g]),
                .hit_pulse   (hit_pulse[g]),
                .escape_pulse(escape_pulse[g]),
                .bad_press   (bad_press[g]),
                .hit_age     (hit_age[g])
            );
        end
    endgenerate

    wire [15:0] n_acertos;
    wire [20:0] soma_e;
    wire [19:0] soma_abs;
    wire [22:0] soma_e2;
    wire [47:0] notas, acertos;
    wire [7:0]  max_combo;

    metricas dut (
        .clock       (clock),
        .reset       (reset),
        .zera        (zera),
        .hit_pulse   (hit_pulse),
        .hit_age     ({hit_age[3], hit_age[2], hit_age[1], hit_age[0]}),
        .escape_pulse(escape_pulse),
        .combo       (combo),
        .n_acertos   (n_acertos),
        .soma_e      (soma_e),
        .soma_abs    (soma_abs),
        .soma_e2     (soma_e2),
        .notas       (notas),
        .acertos     (acertos),
        .max_combo   (max_combo)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    integer erros = 0;

    // ── Tasks ────────────────────────────────────────────────────────────────

    // 1 frame_tick, com spawn nas trilhas da mascara
    task tick_spawn;
        input [3:0] mask;
        begin
            @(negedge clock);
            spawn_en   = mask;
            frame_tick = 1;
            @(posedge clock); #1;
            spawn_en   = 4'b0000;
            frame_tick = 0;
        end
    endtask

    task ticks;
        input integer n;
        integer k;
        begin
            for (k = 0; k < n; k = k + 1) tick_spawn(4'b0000);
        end
    endtask

    // Aperta as trilhas da mascara por 1 ciclo e espera os acumuladores
    // (hit_pulse sai 1 ciclo depois; metricas registra no seguinte)
    task press;
        input [3:0] mask;
        begin
            @(negedge clock);
            btn = mask;
            @(posedge clock); #1;
            btn = 4'b0000;
            repeat (2) @(posedge clock);
            #1;
        end
    endtask

    task zerar;
        begin
            @(negedge clock);
            zera = 1;
            @(posedge clock); #1;
            zera = 0;
        end
    endtask

    task expect_acum;
        input [15:0]      n;
        input integer     se;     // soma_e com sinal
        input [19:0]      sa;
        input [22:0]      s2;
        input [8*40-1:0]  nome;
        begin
            if (n_acertos === n && $signed(soma_e) === se &&
                soma_abs === sa && soma_e2 === s2)
                $display("[OK]   %0s: N=%0d soma_e=%0d soma_abs=%0d soma_e2=%0d",
                         nome, n_acertos, $signed(soma_e), soma_abs, soma_e2);
            else begin
                $display("[FAIL] %0s: N=%0d soma_e=%0d soma_abs=%0d soma_e2=%0d (esperado %0d %0d %0d %0d)",
                         nome, n_acertos, $signed(soma_e), soma_abs, soma_e2,
                         n, se, sa, s2);
                erros = erros + 1;
            end
        end
    endtask

    task expect_trilha;
        input integer    t;
        input [11:0]     nt;
        input [11:0]     ac;
        input [8*40-1:0] nome;
        begin
            if (notas[12*t +: 12] === nt && acertos[12*t +: 12] === ac)
                $display("[OK]   %0s: trilha %0d notas=%0d acertos=%0d",
                         nome, t, nt, ac);
            else begin
                $display("[FAIL] %0s: trilha %0d notas=%0d acertos=%0d (esperado %0d %0d)",
                         nome, t, notas[12*t +: 12], acertos[12*t +: 12], nt, ac);
                erros = erros + 1;
            end
        end
    endtask

    task expect_bit;
        input            valor;
        input [8*40-1:0] nome;
        begin
            if (valor === 1'b1)
                $display("[OK]   %0s", nome);
            else begin
                $display("[FAIL] %0s", nome);
                erros = erros + 1;
            end
        end
    endtask

    // ── Teste ────────────────────────────────────────────────────────────────

    initial begin
        $dumpfile("tb_metricas.vcd");
        $dumpvars(0, tb_metricas);

        $display("=== TB metricas ===");
        repeat (3) @(posedge clock);
        reset = 0;
        game_active = 1;
        @(posedge clock); #1;
        expect_acum(0, 0, 0, 0, "apos reset");

        // ── Cenario 2: assincronia na trilha 0 ───────────────────────────────
        // 118: fora da janela, depois a mesma nota e acertada em 125 (e=-5)
        tick_spawn(4'b0001);
        ticks(118);
        expect_bit(n0[0] === 8'd118, "2. nota na idade 118");
        press(4'b0001);
        expect_acum(0, 0, 0, 0, "2. toque em 118 nao conta");
        ticks(7);
        press(4'b0001);
        expect_acum(1, -5, 5, 25, "2. acerto em 125 (e=-5)");

        tick_spawn(4'b0001); ticks(130); press(4'b0001);
        expect_acum(2, -5, 5, 25, "2. acerto em 130 (e=0)");
        tick_spawn(4'b0001); ticks(133); press(4'b0001);
        expect_acum(3, -2, 8, 34, "2. acerto em 133 (e=+3)");
        tick_spawn(4'b0001); ticks(141); press(4'b0001);
        expect_acum(4, 9, 19, 155, "2. acerto em 141 (e=+11)");
        tick_spawn(4'b0001); ticks(119); press(4'b0001);
        expect_acum(5, -2, 30, 276, "2. acerto em 119 (e=-11)");

        // 142: fora da janela; a nota escapa depois e conta como julgada
        tick_spawn(4'b0001); ticks(142);
        press(4'b0001);
        expect_acum(5, -2, 30, 276, "2. toque em 142 nao conta");
        ticks(12);
        expect_bit(n0[0] === 8'hFF, "2. nota de 142 escapou");
        expect_trilha(0, 6, 5, "2. fim");

        // ── 2b: PAUSE congela ────────────────────────────────────────────────
        game_active = 0;
        tick_spawn(4'b1111);
        ticks(130);
        press(4'b1111);
        expect_acum(5, -2, 30, 276, "2b. PAUSE congela");
        expect_trilha(0, 6, 5, "2b. PAUSE congela");
        expect_trilha(3, 0, 0, "2b. PAUSE congela");

        // ── 2c: COUNTDOWN zera ───────────────────────────────────────────────
        zerar;
        expect_acum(0, 0, 0, 0, "2c. zera");
        expect_trilha(0, 0, 0, "2c. zera");
        expect_bit(max_combo === 8'd0, "2c. max_combo zerado");

        // ── Cenario 3: contadores por trilha ─────────────────────────────────
        game_active = 1;
        // trilha 0: 3 notas, 2 acertos
        tick_spawn(4'b0001); ticks(130); press(4'b0001);
        tick_spawn(4'b0001); ticks(128); press(4'b0001);
        tick_spawn(4'b0001); ticks(155);
        // trilha 1: 2 notas, 2 acertos
        tick_spawn(4'b0010); ticks(131); press(4'b0010);
        tick_spawn(4'b0010); ticks(130); press(4'b0010);
        // trilha 2: 1 nota perdida; toque fora da janela antes
        tick_spawn(4'b0100); ticks(50); press(4'b0100);
        expect_bit(bad_press === 1'b0, "3. (bad_press ja limpo)");
        ticks(110);
        expect_trilha(0, 3, 2, "3.");
        expect_trilha(1, 2, 2, "3.");
        expect_trilha(2, 1, 0, "3.");
        expect_trilha(3, 0, 0, "3.");
        expect_acum(4, -1, 3, 5, "3. acumuladores");

        // ── Cenario 4: duas trilhas no mesmo ciclo ───────────────────────────
        zerar;
        tick_spawn(4'b0010);
        ticks(3);
        tick_spawn(4'b0100);       // trilha 1 em 4, trilha 2 em 0
        ticks(127);                // trilha 1 em 131, trilha 2 em 127
        expect_bit(n0[1] === 8'd131 && n0[2] === 8'd127, "4. idades 131 e 127");
        press(4'b0110);
        expect_acum(2, -2, 4, 10, "4. acertos simultaneos");
        expect_trilha(1, 1, 1, "4.");
        expect_trilha(2, 1, 1, "4.");

        // ── Cenario 5: maior combo ───────────────────────────────────────────
        @(negedge clock); combo = 8'd3;
        @(negedge clock); combo = 8'd7;
        @(negedge clock); combo = 8'd0;
        @(negedge clock); combo = 8'd2;
        @(posedge clock); #1;
        expect_bit(max_combo === 8'd7, "5. max_combo = 7");
        zerar;
        expect_bit(max_combo === 8'd0, "5. zera o max_combo");
        @(posedge clock); #1;
        expect_bit(max_combo === 8'd2, "5. depois volta a acompanhar o combo (2)");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
