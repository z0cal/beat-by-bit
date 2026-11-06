/*-----------------------------------------------------------------------
 * Testbench : tb_servo_pwm.v
 * Modulo    : servo_pwm
 *-----------------------------------------------------------------------
 * Testa (clock real de 50 MHz):
 *   1. Periodo de 20 ms (1_000_000 ciclos)
 *   2. Largura do centro (1,5 ms = 75_000 ciclos), reset leva ao centro
 *   3. Extremos 1 ms (50_000) e 2 ms (100_000)
 *   4. Saturacao: abaixo de 1 ms e acima de 2 ms
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_servo_pwm;

    reg         clock   = 0;
    reg         reset   = 1;
    reg  [19:0] largura = 20'd75000;
    wire        servo;

    servo_pwm dut (.clock(clock), .reset(reset), .largura(largura), .servo(servo));

    always #10 clock = ~clock;

    integer erros = 0;
    integer alta, per;

    // mede um periodo completo: largura alta e distancia entre subidas
    task mede;
        integer t0, n;
        begin
            @(posedge servo); t0 = $time;
            alta = 0;
            @(posedge clock);
            while (servo) begin alta = alta + 1; @(posedge clock); end
            @(posedge servo);
            per = ($time - t0) / 20;
        end
    endtask

    task confere;
        input integer larg_esp;
        input [8*40-1:0] nome;
        begin
            mede;
            if (alta === larg_esp && per === 1000000)
                $display("[OK]   %0s: alta=%0d periodo=%0d", nome, alta, per);
            else begin
                $display("[FAIL] %0s: alta=%0d (esp %0d) periodo=%0d (esp 1000000)",
                         nome, alta, larg_esp, per);
                erros = erros + 1;
            end
        end
    endtask

    initial begin
        #100 reset = 0;
        confere(75000, "1. centro 1,5 ms");
        largura = 20'd50000;
        mede;                       // periodo de transicao (latch)
        confere(50000, "2. minimo 1 ms");
        largura = 20'd100000;
        mede;
        confere(100000, "3. maximo 2 ms");
        largura = 20'd1000;
        mede;
        confere(50000, "4. satura abaixo de 1 ms");
        largura = 20'd400000;
        mede;
        confere(100000, "5. satura acima de 2 ms");

        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);
        $finish;
    end

endmodule
