`timescale 1ns/1ps

// Base de tempo real (50 ciclos/us); so o periodo e o timeout sao reduzidos.
module tb_hcsr04;

    localparam integer CLK_HZ     = 50_000_000;
    localparam integer CICLO_MS   = 4;
    localparam integer TIMEOUT_US = 3000;
    localparam integer ATRASO_US  = 150;   // TRIG -> subida do ECHO

    reg  clock = 0;
    reg  reset = 1;
    reg  echo  = 0;
    wire trig;
    wire [7:0] dist_cm;
    wire leitura_ok;

    integer errors = 0;

    hcsr04 #(.CLK_HZ(CLK_HZ), .CICLO_MS(CICLO_MS), .TIMEOUT_US(TIMEOUT_US)) dut (
        .clock(clock), .reset(reset), .echo(echo),
        .trig(trig), .dist_cm(dist_cm), .leitura_ok(leitura_ok)
    );

    always #10 clock = ~clock;

    // ---- monitor do TRIG: largura e periodo
    realtime t_sobe, t_desce, t_sobe_ant;
    integer  n_trig = 0;
    integer  erros_trig = 0;
    real     largura, periodo;
    always @(posedge trig) begin
        t_sobe_ant = t_sobe;
        t_sobe = $realtime;
        n_trig = n_trig + 1;
        if (n_trig > 1) begin
            periodo = t_sobe - t_sobe_ant;
            if (periodo != CICLO_MS * 1000000.0) begin
                $display("[FAIL] periodo do TRIG = %0f ns", periodo);
                erros_trig = erros_trig + 1;
            end
        end
    end
    always @(negedge trig) begin
        t_desce = $realtime;
        largura = t_desce - t_sobe;
        if (n_trig > 0 && largura != 10000.0) begin
            $display("[FAIL] largura do TRIG = %0f ns", largura);
            erros_trig = erros_trig + 1;
        end
    end

    // ---- modelo do sensor: eco_us = largura do ECHO (0 = sem eco)
    integer eco_us = 0;
    always @(negedge trig) begin
        if (eco_us > 0) begin
            #(ATRASO_US * 1000);
            echo = 1;
            #(eco_us * 1000);
            echo = 0;
        end
    end

    // ---- captura de leituras
    integer n_leit = 0;
    reg [7:0] ult_dist;
    always @(posedge clock) if (leitura_ok) begin
        ult_dist <= dist_cm;
        n_leit   <= n_leit + 1;
    end

    task mede;
        input integer us;
        input integer esperado;  // cm esperado (0 = sem eco)
        input integer tol;
        integer ant, diff;
        begin
            eco_us = us;
            ant = n_leit;
            while (n_leit == ant) @(posedge clock);
            repeat (2) @(posedge clock);
            diff = ult_dist - esperado;
            if (diff < 0) diff = -diff;
            if (diff <= tol)
                $display("[OK]  eco %0d us -> %0d cm (esperado %0d)", us, ult_dist, esperado);
            else begin
                $display("[FAIL] eco %0d us -> %0d cm (esperado %0d)", us, ult_dist, esperado);
                errors = errors + 1;
            end
        end
    endtask

    integer cm;

    initial begin
        $display("=== TB hcsr04 ===");
        repeat (4) @(posedge clock);
        reset = 0;

        mede(5 * 58,  5,  2);
        mede(15 * 58, 15, 2);
        mede(25 * 58, 25, 2);
        mede(40 * 58, 40, 2);
        mede(20 * 58 + 30, 20, 2);       // meio de cm
        mede(0, 0, 0);                   // sem eco: timeout
        mede(TIMEOUT_US + 500, 0, 0);    // ECHO que nao termina a tempo
        mede(10 * 58, 10, 2);            // volta ao normal depois do timeout

        if (n_trig < 8) begin
            $display("[FAIL] poucos disparos: %0d", n_trig);
            errors = errors + 1;
        end
        if (erros_trig == 0)
            $display("[OK]  TRIG: 10 us de largura, periodo de %0d ms (%0d disparos)", CICLO_MS, n_trig);
        else
            errors = errors + erros_trig;

        if (errors == 0) $display("=== PASSOU: 0 erros ===");
        else $fatal(1, "=== FALHOU: %0d erros ===", errors);
        $finish;
    end

    initial begin
        #200_000_000;
        $display("[TIMEOUT]");
        $fatal(1, "=== FALHOU: timeout ===");
    end

endmodule
