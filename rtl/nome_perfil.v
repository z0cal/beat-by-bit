/*-----------------------------------------------------------------------
 * Arquivo   : nome_perfil.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Nome do perfil em estilo arcade (3 caracteres), editado
 *             no dashboard de fim de musica. O PC usa o nome como
 *             pseudonimo da sessao no historico.
 *
 *   Caracteres (6 bits): 0 = '-', 1..26 = 'A'..'Z', 27..36 = '0'..'9'.
 *   Nome "---" = sem perfil.
 *
 *   Com habilita=1 (estados END_WIN/END_LOSE):
 *     pulso[0] (D) = caractere anterior na posicao do cursor
 *     pulso[1] (F) = proximo caractere  (37 valores, volta ao inicio)
 *     pulso[2] (J) = cursor a esquerda  (para na posicao 0)
 *     pulso[3] (K) = cursor a direita   (para na posicao 2)
 *   Com habilita=0 o cursor volta a 0, mas o nome e mantido: a mesma
 *   crianca so confirma no proximo fim de musica. O reset leva o nome
 *   a "---".
 *
 *   nome = {c2, c1, c0}, c0 = caractere da esquerda.
 *-----------------------------------------------------------------------
 */

module nome_perfil (
    input  wire        clock,
    input  wire        reset,
    input  wire        habilita,   // ativo so no fim de musica
    input  wire [3:0]  pulso,      // btn_pulse das trilhas
    output reg  [17:0] nome,
    output reg  [1:0]  cursor
);

    localparam [5:0] ULTIMO = 6'd36;   // '9'

    wire [5:0] atual = nome[6*cursor +: 6];

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            nome   <= 18'd0;
            cursor <= 2'd0;
        end else if (!habilita) begin
            cursor <= 2'd0;
        end else begin
            // Caractere na posicao do cursor (D tem prioridade sobre F)
            if (pulso[0])
                nome[6*cursor +: 6] <= (atual == 6'd0) ? ULTIMO : atual - 6'd1;
            else if (pulso[1])
                nome[6*cursor +: 6] <= (atual == ULTIMO) ? 6'd0 : atual + 6'd1;

            // Cursor (J tem prioridade sobre K)
            if (pulso[2]) begin
                if (cursor != 2'd0) cursor <= cursor - 2'd1;
            end else if (pulso[3]) begin
                if (cursor != 2'd2) cursor <= cursor + 2'd1;
            end
        end
    end

endmodule
