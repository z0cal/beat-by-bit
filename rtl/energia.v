/*-----------------------------------------------------------------------
 * Arquivo   : energia.v
 * Projeto   : Beat by Bit - Semestre 3
 *-----------------------------------------------------------------------
 * Descricao : Barra de energia alimentada pelo sensor ultrassonico
 *             HC-SR04 e multiplicador x2 automatico (substitui a antiga
 *             nota de gesto, sem punicao e sem gerar erro).
 *
 * Regras:
 *   - So conta em PLAY (game_active = 1). Congela em PAUSE/END/IDLE.
 *   - Zera no COUNTDOWN (zera = 1), mantendo ativacoes e movimentos
 *     disponiveis no fim da musica ate a proxima partida (como metricas.v).
 *   - Leitura valida com mao: leitura_ok com DIST_MIN <= dist_cm <= DIST_MAX.
 *     Leituras 0 (sem eco) ou fora da faixa sao ignoradas (nao somam
 *     energia nem contam movimento) e desarmam a comparacao com a leitura
 *     anterior.
 *   - Enchimento (fora do x2): a cada leitura valida soma GANHO_PRESENCA (1).
 *     Se a leitura anterior tambem foi valida e |dist_cm - dist_anterior| >=
 *     LIMIAR_MOV (3 cm), soma tambem GANHO_MOV (2) e incrementa movimentos.
 *   - Energia satura em ENERGIA_MAX (255). Ao atingir ENERGIA_MAX, liga
 *     x2_ativo por DURACAO_X2 (480) quadros (8 s a 60 fps) e incrementa
 *     ativacoes (saturando em 127).
 *   - Durante o x2, a barra nao enche e a saida `energia` exibe o tempo
 *     restante escalado linearmente para 0..ENERGIA_MAX. Ao fim dos
 *     DURACAO_X2 quadros, x2_ativo desliga e energia volta a 0.
 *-----------------------------------------------------------------------
 */

module energia #(
    parameter integer DIST_MIN       = 5,
    parameter integer DIST_MAX       = 30,
    parameter integer GANHO_PRESENCA = 1,
    parameter integer GANHO_MOV      = 2,
    parameter integer LIMIAR_MOV     = 3,
    parameter integer ENERGIA_MAX    = 255,
    parameter integer DURACAO_X2     = 480
) (
    input  wire        clock,
    input  wire        reset,
    input  wire        zera,         // estado COUNTDOWN: zera para nova sessao
    input  wire        game_active,  // ativo apenas em PLAY (congela em PAUSE)
    input  wire        frame_tick,   // pulso de 1 ciclo a 60 fps
    input  wire        leitura_ok,   // pulso de 1 ciclo do hcsr04
    input  wire [7:0]  dist_cm,      // distancia medida em cm (0 = sem eco)

    output wire [7:0]  energia,      // 0..255; no x2 = tempo restante escalado
    output reg         x2_ativo,
    output reg  [6:0]  ativacoes,    // satura em 127
    output reg  [15:0] movimentos    // satura em 65535
);

    reg [7:0]  energia_int;
    reg [15:0] x2_restante;
    reg [7:0]  dist_ant;
    reg        ant_valida;

    wire dentro = (dist_cm >= DIST_MIN[7:0]) && (dist_cm <= DIST_MAX[7:0]);

    wire [7:0] diff_dist = (dist_cm >= dist_ant) ? (dist_cm - dist_ant)
                                                 : (dist_ant - dist_cm);
    wire houve_mov = ant_valida && (diff_dist >= LIMIAR_MOV[7:0]);

    wire [9:0] ganho = GANHO_PRESENCA[9:0] + (houve_mov ? GANHO_MOV[9:0] : 10'd0);
    wire [9:0] soma  = {2'b00, energia_int} + ganho;

    // Escala linear do tempo restante no x2 para 0..ENERGIA_MAX.
    // Para os parametros padrao (255 e 480), floor(255*x/480) == floor(17*x/32)
    // para todo inteiro x >= 0, evitando inferir divisor combinacional.
    wire [31:0] esc_padrao = ({16'd0, x2_restante} * 32'd17) >> 5;
    wire [31:0] esc_geral  = ({16'd0, x2_restante} * ENERGIA_MAX) / DURACAO_X2;
    wire [31:0] energia_x2 = (ENERGIA_MAX == 255 && DURACAO_X2 == 480) ? esc_padrao
                                                                        : esc_geral;

    assign energia = x2_ativo ? energia_x2[7:0] : energia_int;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            energia_int <= 8'd0;
            x2_ativo    <= 1'b0;
            x2_restante <= 16'd0;
            ativacoes   <= 7'd0;
            movimentos  <= 16'd0;
            dist_ant    <= 8'd0;
            ant_valida  <= 1'b0;
        end else if (zera) begin
            energia_int <= 8'd0;
            x2_ativo    <= 1'b0;
            x2_restante <= 16'd0;
            ativacoes   <= 7'd0;
            movimentos  <= 16'd0;
            dist_ant    <= 8'd0;
            ant_valida  <= 1'b0;
        end else if (game_active) begin
            // Contagem de quadros do x2 (so quando ja estava ativo antes do ciclo)
            if (x2_ativo && frame_tick) begin
                if (x2_restante <= 16'd1) begin
                    x2_ativo    <= 1'b0;
                    x2_restante <= 16'd0;
                    energia_int <= 8'd0;
                end else begin
                    x2_restante <= x2_restante - 16'd1;
                end
            end

            // Leituras do sensor ultrassonico
            if (leitura_ok) begin
                if (dentro) begin
                    dist_ant   <= dist_cm;
                    ant_valida <= 1'b1;
                    if (houve_mov && movimentos != 16'hFFFF)
                        movimentos <= movimentos + 16'd1;

                    // Durante o x2 a barra nao enche
                    if (!x2_ativo) begin
                        if (soma >= ENERGIA_MAX[9:0]) begin
                            energia_int <= ENERGIA_MAX[7:0];
                            x2_ativo    <= 1'b1;
                            x2_restante <= DURACAO_X2[15:0];
                            if (ativacoes != 7'd127)
                                ativacoes <= ativacoes + 7'd1;
                        end else begin
                            energia_int <= soma[7:0];
                        end
                    end
                end else begin
                    // Sem eco (0) ou fora da faixa: ignorada, nao conta movimento
                    ant_valida <= 1'b0;
                end
            end
        end
    end

endmodule
