`timescale 1ns/1ps

module tb_debounce_pulse;

    localparam integer CLK_PERIOD      = 10;
    localparam integer DEBOUNCE_CYCLES = 12;

    reg clock       = 0;
    reg reset       = 1;
    reg limpaR      = 0;
    reg botao_track = 0;
    reg botao_start = 0;

    wire pulse_track;
    wire pulse_start;

    integer errors             = 0;
    integer track_pulse_count  = 0;
    integer start_pulse_count  = 0;
    integer base_count         = 0;

    debounce_pulse #(.DEBOUNCE_CYCLES(DEBOUNCE_CYCLES)) dut_track (
        .clock(clock),
        .reset(reset | limpaR),
        .sinal(botao_track),
        .pulso(pulse_track)
    );

    debounce_pulse #(.DEBOUNCE_CYCLES(DEBOUNCE_CYCLES)) dut_start (
        .clock(clock),
        .reset(reset),
        .sinal(botao_start),
        .pulso(pulse_start)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    always @(posedge clock) begin
        if (pulse_track)
            track_pulse_count <= track_pulse_count + 1;
        if (pulse_start)
            start_pulse_count <= start_pulse_count + 1;
    end

    task wait_cycles;
        input integer cycles;
        integer i;
        begin
            for (i = 0; i < cycles; i = i + 1)
                @(posedge clock);
        end
    endtask

    initial begin
        $display("=== TB debounce_pulse ===");

        wait_cycles(4);
        reset = 0;
        wait_cycles(2);

        // Bounce rapido na trilha: deve contar um unico pulso.
        botao_track = 1; wait_cycles(3);
        botao_track = 0; wait_cycles(2);
        botao_track = 1; wait_cycles(2);
        botao_track = 0; wait_cycles(1);
        botao_track = 1; wait_cycles(3);
        botao_track = 0; wait_cycles(DEBOUNCE_CYCLES + 4);
        if (track_pulse_count == 1)
            $display("[OK]  Bounce na trilha gerou 1 pulso");
        else begin
            $display("[FAIL] Bounce na trilha gerou %0d pulsos", track_pulse_count);
            errors = errors + 1;
        end

        // Botao mantido pressionado alem da janela: nao pode repetir.
        base_count = track_pulse_count;
        botao_track = 1; wait_cycles(DEBOUNCE_CYCLES + 8);
        botao_track = 0; wait_cycles(4);
        if (track_pulse_count == base_count + 1)
            $display("[OK]  Botao mantido pressionado nao repetiu pulso");
        else begin
            $display("[FAIL] Hold da trilha adicionou %0d pulsos",
                     track_pulse_count - base_count);
            errors = errors + 1;
        end

        // Nova pressao apos soltar e esperar a janela: deve gerar novo pulso.
        base_count = track_pulse_count;
        wait_cycles(DEBOUNCE_CYCLES + 2);
        botao_track = 1; wait_cycles(3);
        botao_track = 0; wait_cycles(4);
        if (track_pulse_count == base_count + 1)
            $display("[OK]  Segunda pressao valida na trilha gerou novo pulso");
        else begin
            $display("[FAIL] Segunda pressao valida na trilha nao gerou pulso unico");
            errors = errors + 1;
        end

        // START com bounce rapido: mesma politica do botao de trilha.
        botao_start = 1; wait_cycles(3);
        botao_start = 0; wait_cycles(2);
        botao_start = 1; wait_cycles(2);
        botao_start = 0; wait_cycles(DEBOUNCE_CYCLES + 4);
        if (start_pulse_count == 1)
            $display("[OK]  Bounce no START gerou 1 pulso");
        else begin
            $display("[FAIL] Bounce no START gerou %0d pulsos", start_pulse_count);
            errors = errors + 1;
        end

        // Reset combinado da trilha deve limpar o estado interno dela.
        limpaR = 1;
        wait_cycles(2);
        limpaR = 0;
        wait_cycles(2);
        base_count = track_pulse_count;
        botao_track = 1; wait_cycles(3);
        botao_track = 0; wait_cycles(4);
        if (track_pulse_count == base_count + 1)
            $display("[OK]  Reset da trilha liberou nova deteccao");
        else begin
            $display("[FAIL] Reset da trilha nao restaurou a deteccao");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("=== PASSOU: 0 erros ===");
        else
            $fatal(1, "=== FALHOU: %0d erros ===", errors);

        $finish;
    end

    initial begin
        #5000;
        $display("[TIMEOUT]");
        $finish;
    end

endmodule
