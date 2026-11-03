/*-----------------------------------------------------------------------
 * Arquivo   : taxa_acerto.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Taxa de acerto em porcentagem para os displays HEX
 *             (TAXA_HEX):
 *
 *               taxa = floor(100 * acertos / notas),  0..100
 *
 *             Divisor sequencial (restaurador, 1 bit por ciclo). Ele roda
 *             sem parar: carrega os operandos, divide em 23 ciclos,
 *             registra o resultado e recomeca. A saida acompanha os
 *             contadores com atraso de ~25 ciclos (0,5 us), muito menos
 *             que um quadro.
 *
 *             Com notas = 0 a taxa e 0. O resultado e limitado a 100.
 *             Tambem entrega os digitos decimais para o hexa7seg.
 *-----------------------------------------------------------------------
 */

module taxa_acerto (
    input  wire        clock,
    input  wire        reset,
    input  wire [15:0] acertos,      // N do bloco de metricas
    input  wire [13:0] notas,        // soma das notas julgadas nas 4 trilhas
    output reg  [6:0]  taxa,         // 0..100
    output wire [3:0]  centena,      // 0 ou 1
    output wire [3:0]  dezena,
    output wire [3:0]  unidade
);

    localparam CARREGA = 2'd0;
    localparam DIVIDE  = 2'd1;
    localparam FIM     = 2'd2;

    reg [1:0]  estado;
    reg [4:0]  passos;      // bits restantes do dividendo
    reg [22:0] dividendo;   // 100 * acertos (ate 6_553_500 < 2^23)
    reg [13:0] divisor;
    reg [14:0] resto;
    reg [22:0] quociente;

    // Resto deslocado com o proximo bit do dividendo
    wire [14:0] resto_desl = {resto[13:0], dividendo[22]};
    wire        cabe       = (resto_desl >= {1'b0, divisor});

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            estado    <= CARREGA;
            passos    <= 5'd0;
            dividendo <= 23'd0;
            divisor   <= 14'd0;
            resto     <= 15'd0;
            quociente <= 23'd0;
            taxa      <= 7'd0;
        end else begin
            case (estado)
                CARREGA: begin
                    // 100*a = 64a + 32a + 4a
                    dividendo <= ({7'd0, acertos} << 6) + ({7'd0, acertos} << 5)
                               + ({7'd0, acertos} << 2);
                    divisor   <= notas;
                    resto     <= 15'd0;
                    quociente <= 23'd0;
                    passos    <= 5'd23;
                    estado    <= DIVIDE;
                end

                DIVIDE: begin
                    dividendo <= {dividendo[21:0], 1'b0};
                    resto     <= cabe ? (resto_desl - {1'b0, divisor}) : resto_desl;
                    quociente <= {quociente[21:0], cabe};
                    passos    <= passos - 5'd1;
                    if (passos == 5'd1)
                        estado <= FIM;
                end

                FIM: begin
                    if (divisor == 14'd0)
                        taxa <= 7'd0;
                    else if (quociente > 23'd100)
                        taxa <= 7'd100;
                    else
                        taxa <= quociente[6:0];
                    estado <= CARREGA;
                end

                default: estado <= CARREGA;
            endcase
        end
    end

    // Digitos decimais (0..100)
    wire [6:0] abaixo_de_100 = (taxa >= 7'd100) ? 7'd0 : taxa;
    assign centena = (taxa >= 7'd100) ? 4'd1 : 4'd0;
    assign dezena  = abaixo_de_100 / 7'd10;
    assign unidade = abaixo_de_100 % 7'd10;

endmodule
