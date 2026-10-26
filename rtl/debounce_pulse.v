/* ------------------------------------------------------------------------
 *  Arquivo   : debounce_pulse.v
 *  Projeto   : Beat by Bit - Semestre 2
 * ------------------------------------------------------------------------
 *  Descricao : condiciona um botao assincrono para o dominio do clock
 *              e gera um pulso de 1 ciclo na primeira borda de subida
 *              sincronizada. Apos um toque valido, novas transicoes
 *              ficam bloqueadas por DEBOUNCE_CYCLES.
 *
 *              O modulo tambem exige que o botao seja solto antes de
 *              permitir um novo pulso, evitando repeticao ao manter a
 *              tecla pressionada por mais tempo que a janela de debounce.
 * ------------------------------------------------------------------------
 */

module debounce_pulse #(parameter integer DEBOUNCE_CYCLES = 9_000_000) (
    input  wire clock,
    input  wire reset,
    input  wire sinal,
    output wire pulso
);

    reg sync0;
    reg sync1;
    reg prev_sync1;

    reg        pulso_reg;
    reg        lockout_active;
    reg        wait_release;
    reg [23:0] lockout_count;

    localparam integer LAST_COUNT = DEBOUNCE_CYCLES - 1;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            sync0          <= 1'b1;  // 1 = estado de repouso com pull-up (sem pulso espurio)
            sync1          <= 1'b1;
            prev_sync1     <= 1'b1;
            pulso_reg      <= 1'b0;
            lockout_active <= 1'b0;
            wait_release   <= 1'b0;
            lockout_count  <= 24'd0;
        end else begin
            pulso_reg  <= 1'b0;
            sync0      <= sinal;
            sync1      <= sync0;
            prev_sync1 <= sync1;

            if (!sync1)
                wait_release <= 1'b0;

            if (lockout_active) begin
                if (lockout_count == LAST_COUNT[23:0]) begin
                    lockout_active <= 1'b0;
                    lockout_count  <= 24'd0;
                end else begin
                    lockout_count <= lockout_count + 24'd1;
                end
            end else begin
                lockout_count <= 24'd0;
                if (sync1 && !prev_sync1 && !wait_release) begin
                    pulso_reg      <= 1'b1;
                    lockout_active <= 1'b1;
                    wait_release   <= 1'b1;
                end
            end
        end
    end

    assign pulso = pulso_reg;

endmodule
