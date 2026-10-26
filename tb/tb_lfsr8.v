/*-----------------------------------------------------------------------
 * Testbench : tb_lfsr8.v
 * Modulo    : lfsr8
 *-----------------------------------------------------------------------
 * Testa:
 *   1. Nunca produz 0 (estado proibido)
 *   2. Periodo de 255 (sequencia completa de estados nao-nulos)
 *   3. Valores mudam a cada enable
 *   4. Nao muda quando enable=0
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_lfsr8;

    reg  clock  = 0;
    reg  reset  = 1;
    reg  enable = 0;
    wire [7:0] lfsr_out;

    lfsr8 dut (
        .clock   (clock),
        .reset   (reset),
        .enable  (enable),
        .lfsr_out(lfsr_out)
    );

    always #5 clock = ~clock;

    integer i;
    integer erros    = 0;
    integer zeros    = 0;
    integer unicos   = 0;
    reg [255:0] seen = 0;  // bitmask: seen[v]=1 se valor v apareceu
    reg [7:0] prev_val;

    initial begin
        $dumpfile("tb_lfsr8.vcd");
        $dumpvars(0, tb_lfsr8);

        reset = 1;
        repeat(4) @(posedge clock);
        reset = 0;
        @(posedge clock);

        $display("=== TB lfsr8 ===");

        // --- Teste 1: nao muda sem enable ---
        enable = 0;
        prev_val = lfsr_out;
        repeat(10) @(posedge clock);
        if (lfsr_out === prev_val)
            $display("[OK]  Sem enable: valor nao mudou (0x%02X)", lfsr_out);
        else begin
            $display("[FAIL] Sem enable: valor mudou!");
            erros = erros + 1;
        end

        // --- Teste 2: ciclo de 255 passos, verifica 0 e unicidade ---
        enable = 1;
        for (i = 0; i < 255; i = i+1) begin
            @(posedge clock);
            #1; // pequeno delay para capturar saida apos posedge
            if (lfsr_out === 8'h00) begin
                $display("[FAIL] Passo %0d: lfsr_out=0 (estado proibido)!", i);
                zeros  = zeros + 1;
                erros  = erros + 1;
            end
            if (!seen[lfsr_out])
                unicos = unicos + 1;
            seen[lfsr_out] = 1;
        end

        if (zeros == 0)
            $display("[OK]  Nunca produziu 0 em 255 passos");

        if (unicos == 255)
            $display("[OK]  Periodo = 255 (todos os estados unicos)");
        else begin
            $display("[FAIL] Apenas %0d estados unicos (esperado 255)", unicos);
            erros = erros + 1;
        end

        // --- Teste 3: reset volta ao mesmo estado inicial ---
        enable = 0;
        reset  = 1;
        @(posedge clock);
        reset = 0;
        @(posedge clock);
        #1;
        if (lfsr_out === 8'hAC)
            $display("[OK]  Apos reset: estado inicial = 0xAC");
        else begin
            $display("[FAIL] Apos reset: esperado 0xAC, recebeu 0x%02X", lfsr_out);
            erros = erros + 1;
        end

        // --- Resultado ---
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    initial begin
        #100000;
        $display("[TIMEOUT]");
        $finish;
    end

endmodule
