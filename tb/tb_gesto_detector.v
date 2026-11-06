`timescale 1ns/1ps

module tb_gesto_detector;

    reg        clock = 0;
    reg        reset = 1;
    reg        hab   = 0;
    reg        leitura_ok = 0;
    reg  [7:0] dist_cm = 0;

    wire       na_faixa, gesto_pulso, confirma;
    wire [2:0] progresso;

    integer errors = 0;
    integer n_conf = 0;
    integer n_gesto = 0;

    gesto_detector dut (
        .clock(clock), .reset(reset), .habilita_confirma(hab),
        .leitura_ok(leitura_ok), .dist_cm(dist_cm),
        .na_faixa(na_faixa), .gesto_pulso(gesto_pulso),
        .confirma(confirma), .progresso(progresso)
    );

    always #5 clock = ~clock;

    always @(posedge clock) begin
        if (confirma)    n_conf  <= n_conf + 1;
        if (gesto_pulso) n_gesto <= n_gesto + 1;
    end

    task leitura;
        input [7:0] d;
        begin
            @(posedge clock); #1;
            dist_cm = d; leitura_ok = 1;
            @(posedge clock); #1;
            leitura_ok = 0;
            repeat (3) @(posedge clock);
        end
    endtask

    task leituras;
        input [7:0] d;
        input integer n;
        integer i;
        begin
            for (i = 0; i < n; i = i + 1) leitura(d);
        end
    endtask

    task confere;
        input integer ok;
        input [8*48-1:0] msg;
        begin
            if (ok) $display("[OK]  %0s", msg);
            else begin
                $display("[FAIL] %0s", msg);
                errors = errors + 1;
            end
        end
    endtask

    integer base;

    initial begin
        $display("=== TB gesto_detector ===");
        repeat (3) @(posedge clock);
        reset = 0;
        repeat (2) @(posedge clock);

        // limites da faixa (na_faixa e gesto_pulso, sem habilita)
        base = n_gesto;
        leitura(8'd4);  confere(na_faixa == 0, "4 cm fora");
        leitura(8'd5);  confere(na_faixa == 1, "5 cm dentro");
        leitura(8'd25); confere(na_faixa == 1, "25 cm dentro");
        leitura(8'd26); confere(na_faixa == 0, "26 cm fora");
        leitura(8'd0);  confere(na_faixa == 0, "0 cm (sem eco) fora");
        confere(n_gesto == base + 2, "gesto_pulso so nas 2 leituras dentro");
        confere(n_conf == 0, "sem confirma com habilita=0");

        // 16 leituras seguidas confirmam exatamente 1 vez
        hab = 1; repeat (2) @(posedge clock);
        leituras(8'd15, 8);
        confere(progresso == 4, "progresso 4 com 8/16");
        leituras(8'd15, 8);
        confere(n_conf == 1, "16 leituras: 1 confirma");
        confere(progresso == 0, "progresso 0 desarmado");

        // mao parada depois de confirmar nao dispara de novo
        leituras(8'd15, 40);
        confere(n_conf == 1, "mao parada nao redispara");

        // 3 fora + dentro nao rearma (precisa de 4 seguidas)
        leituras(8'd60, 3); leituras(8'd15, 1);
        leituras(8'd60, 3); leituras(8'd15, 20);
        confere(n_conf == 1, "3 fora nao rearma");

        // 4 fora rearma
        leituras(8'd60, 4);
        leituras(8'd15, 16);
        confere(n_conf == 2, "rearma apos 4 fora e confirma de novo");

        // 15 + 1 fora nao confirma
        leituras(8'd0, 4);
        leituras(8'd15, 15);
        confere(progresso == 7, "progresso 7 saturado com 15/16");
        leituras(8'd40, 1);
        confere(progresso == 0 && n_conf == 2, "15 + 1 fora zera, sem confirma");
        leituras(8'd15, 15);
        confere(n_conf == 2, "15 apos zerar nao confirma");
        leituras(8'd15, 1);
        confere(n_conf == 3, "16a leitura apos recomecar confirma");

        // habilita=0 zera e rearma imediatamente
        leituras(8'd15, 3);
        hab = 0; repeat (3) @(posedge clock);
        hab = 1; repeat (2) @(posedge clock);
        confere(progresso == 0, "habilita=0 zerou o progresso");
        leituras(8'd15, 16);
        confere(n_conf == 4, "habilita=0 liberou o desarme");

        if (errors == 0) $display("=== PASSOU: 0 erros ===");
        else $fatal(1, "=== FALHOU: %0d erros ===", errors);
        $finish;
    end

    initial begin
        #2000000;
        $display("[TIMEOUT]");
        $fatal(1, "=== FALHOU: timeout ===");
    end

endmodule
