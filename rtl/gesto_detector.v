/* ------------------------------------------------------------------------
 *  Arquivo   : gesto_detector.v
 *  Projeto   : Beat by Bit - Semestre 3
 * ------------------------------------------------------------------------
 *  Descricao : interpreta as leituras do HC-SR04 como gesto da mao.
 *              na_faixa     ultima leitura entre DIST_MIN e DIST_MAX (cm)
 *              gesto_pulso  pulso de 1 ciclo a cada leitura na faixa
 *                           (usado no PLAY como "acerto" da nota de gesto)
 *              confirma     pulso unico apos N_CONFIRMA leituras seguidas
 *                           na faixa (usado no SETTINGS). Uma leitura fora
 *                           zera o progresso; depois de confirmar, so rearma
 *                           apos N_REARME leituras seguidas fora da faixa.
 *              progresso    contagem*8/N_CONFIRMA (0..7); 0 se desarmado.
 *              Com habilita_confirma = 0 os contadores zeram e o detector
 *              fica armado.
 * ------------------------------------------------------------------------
 */

module gesto_detector #(
    parameter integer DIST_MIN   = 5,
    parameter integer DIST_MAX   = 25,
    parameter integer N_CONFIRMA = 16,
    parameter integer N_REARME   = 4
) (
    input  wire       clock,
    input  wire       reset,
    input  wire       habilita_confirma,
    input  wire       leitura_ok,
    input  wire [7:0] dist_cm,
    output reg        na_faixa,
    output reg        gesto_pulso,
    output reg        confirma,
    output wire [2:0] progresso
);

    reg       armado;
    reg [7:0] cont;      // leituras seguidas na faixa (armado)
    reg [7:0] cont_fora; // leituras seguidas fora da faixa (desarmado)

    wire dentro = (dist_cm >= DIST_MIN) && (dist_cm <= DIST_MAX);

    wire [10:0] prog_full = ({3'b000, cont} * 11'd8) / N_CONFIRMA;
    assign progresso = !armado ? 3'd0 :
                       (prog_full > 11'd7) ? 3'd7 : prog_full[2:0];

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            na_faixa    <= 1'b0;
            gesto_pulso <= 1'b0;
            confirma    <= 1'b0;
            armado      <= 1'b1;
            cont        <= 8'd0;
            cont_fora   <= 8'd0;
        end else begin
            gesto_pulso <= 1'b0;
            confirma    <= 1'b0;

            if (leitura_ok) begin
                na_faixa    <= dentro;
                gesto_pulso <= dentro;
            end

            if (!habilita_confirma) begin
                armado    <= 1'b1;
                cont      <= 8'd0;
                cont_fora <= 8'd0;
            end else if (leitura_ok) begin
                if (armado) begin
                    if (dentro) begin
                        if (cont == N_CONFIRMA - 1) begin
                            confirma  <= 1'b1;
                            armado    <= 1'b0;
                            cont      <= 8'd0;
                            cont_fora <= 8'd0;
                        end else begin
                            cont <= cont + 8'd1;
                        end
                    end else begin
                        cont <= 8'd0;
                    end
                end else begin
                    if (dentro) begin
                        cont_fora <= 8'd0;
                    end else if (cont_fora == N_REARME - 1) begin
                        armado    <= 1'b1;
                        cont_fora <= 8'd0;
                    end else begin
                        cont_fora <= cont_fora + 8'd1;
                    end
                end
            end
        end
    end

endmodule
