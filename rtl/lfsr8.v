/*-----------------------------------------------------------------------
 * Arquivo   : lfsr8.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : LFSR (Linear Feedback Shift Register) de 8 bits.
 *             Polinomio: x^8 + x^6 + x^5 + x^4 + 1  (taps: 8,6,5,4)
 *             Periodo maximo: 255 estados nao-nulos.
 *
 *             Avanca 1 passo a cada pulso de 'enable'.
 *             'lfsr_out' e o valor atual do registrador.
 *             Nunca retorna 0 (estado proibido -> reinicia em 1).
 *-----------------------------------------------------------------------
 */

module lfsr8 (
    input  wire       clock,
    input  wire       reset,
    input  wire       enable,
    output wire [7:0] lfsr_out
);

    reg [7:0] lfsr_reg;

    // Feedback: taps em bits 8,6,5,4 (indexados de 1)
    // Na notacao Verilog [7:0]: bit7=tap8, bit5=tap6, bit4=tap5, bit3=tap4
    wire feedback = lfsr_reg[7] ^ lfsr_reg[5] ^ lfsr_reg[4] ^ lfsr_reg[3];

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            lfsr_reg <= 8'hAC;  // semente inicial (qualquer valor != 0)
        end else if (enable) begin
            if (lfsr_reg == 8'h00)
                lfsr_reg <= 8'h01;  // recupera de estado invalido
            else
                lfsr_reg <= {lfsr_reg[6:0], feedback};
        end
    end

    assign lfsr_out = lfsr_reg;

endmodule
