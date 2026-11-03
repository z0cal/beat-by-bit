/*-----------------------------------------------------------------------
 * Arquivo   : metricas.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Bloco de metricas da sessao (MEDE_ASSINCRONIA,
 *             ACERTOS_POR_TRILHA). A FPGA so acumula; media, desvio e
 *             taxa de acerto sao calculados no PC (e a taxa tambem no
 *             taxa_acerto.v, para os displays HEX).
 *
 *   A cada acerto na trilha t, com idade da nota hit_age[t]:
 *     e = hit_age - 130          (-11..+11 quadros; negativo = adiantado)
 *     N        <- N + 1
 *     soma_e   <- soma_e + e     (complemento de 2)
 *     soma_abs <- soma_abs + |e|
 *     soma_e2  <- soma_e2 + e^2  (tabela indexada por |e|)
 *     acertos[t] <- acertos[t] + 1
 *   A cada nota julgada na trilha t (acerto ou nota perdida):
 *     notas[t] <- notas[t] + 1
 *   Maior combo da sessao: max_combo <- max(max_combo, combo)
 *
 *   zera=1 (estado COUNTDOWN) zera tudo para a nova sessao. Os pulsos
 *   de acerto e de nota perdida so existem em PLAY (note_track usa
 *   game_active), entao em PAUSE e END os valores ficam congelados, e
 *   em IDLE continuam disponiveis ate a proxima musica.
 *
 *   Varias trilhas podem acertar no mesmo ciclo: as contribuicoes das
 *   4 trilhas sao somadas antes de entrar nos acumuladores.
 *
 * Larguras (ate 65535 acertos, |e| <= 11):
 *   N 16 bits, soma_e 21 bits com sinal, soma_abs 20 bits,
 *   soma_e2 23 bits, notas[t] e acertos[t] 12 bits cada.
 *   notas    = {notas[3], notas[2], notas[1], notas[0]}
 *   acertos  = {acertos[3], ..., acertos[0]}
 *   hit_age  = {age[3], age[2], age[1], age[0]}
 *-----------------------------------------------------------------------
 */

module metricas (
    input  wire        clock,
    input  wire        reset,
    input  wire        zera,          // estado COUNTDOWN: nova sessao

    input  wire [3:0]  hit_pulse,     // acerto por trilha (note_track)
    input  wire [31:0] hit_age,       // idade da nota acertada, por trilha
    input  wire [3:0]  escape_pulse,  // nota perdida por trilha
    input  wire [7:0]  combo,         // combo atual (fluxo_dados)

    output reg  [15:0] n_acertos,
    output reg  [20:0] soma_e,        // com sinal (complemento de 2)
    output reg  [19:0] soma_abs,
    output reg  [22:0] soma_e2,
    output reg  [47:0] notas,
    output reg  [47:0] acertos,
    output reg  [7:0]  max_combo
);

    localparam [7:0] HIT_CENTER = 8'd130;

    // e^2 para |e| de 0 a 15 (a janela garante |e| <= 11)
    function [7:0] quadrado;
        input [3:0] a;
        begin
            case (a)
                4'd0:  quadrado = 8'd0;
                4'd1:  quadrado = 8'd1;
                4'd2:  quadrado = 8'd4;
                4'd3:  quadrado = 8'd9;
                4'd4:  quadrado = 8'd16;
                4'd5:  quadrado = 8'd25;
                4'd6:  quadrado = 8'd36;
                4'd7:  quadrado = 8'd49;
                4'd8:  quadrado = 8'd64;
                4'd9:  quadrado = 8'd81;
                4'd10: quadrado = 8'd100;
                4'd11: quadrado = 8'd121;
                4'd12: quadrado = 8'd144;
                4'd13: quadrado = 8'd169;
                4'd14: quadrado = 8'd196;
                default: quadrado = 8'd225;
            endcase
        end
    endfunction

    // ----------------------------------------------------------------
    // Contribuicao de cada trilha neste ciclo (0 se nao houve acerto)
    // ----------------------------------------------------------------
    reg  [8:0] e_t   [0:3];   // e em complemento de 2 (9 bits)
    reg  [3:0] abs_t [0:3];   // |e|
    reg  [8:0] d_e;           // soma dos e (ate +-44)
    reg  [5:0] d_abs;         // soma dos |e| (ate 44)
    reg  [9:0] d_e2;          // soma dos e^2 (ate 484)
    reg  [2:0] d_n;           // acertos neste ciclo (0..4)
    reg  [7:0] age;
    integer t, k;

    always @* begin
        d_e   = 9'd0;
        d_abs = 6'd0;
        d_e2  = 10'd0;
        d_n   = 3'd0;
        for (t = 0; t < 4; t = t + 1) begin
            age = hit_age[8*t +: 8];
            if (age >= HIT_CENTER) begin
                e_t[t]   = {1'b0, age - HIT_CENTER};
                abs_t[t] = age - HIT_CENTER;
            end else begin
                e_t[t]   = 9'd0 - {1'b0, HIT_CENTER - age};
                abs_t[t] = HIT_CENTER - age;
            end
            if (hit_pulse[t]) begin
                d_e   = d_e + e_t[t];
                d_abs = d_abs + {2'b00, abs_t[t]};
                d_e2  = d_e2 + {2'b00, quadrado(abs_t[t])};
                d_n   = d_n + 3'd1;
            end
        end
    end

    // ----------------------------------------------------------------
    // Acumuladores da sessao
    // ----------------------------------------------------------------
    always @(posedge clock or posedge reset) begin
        if (reset) begin
            n_acertos <= 16'd0;
            soma_e    <= 21'd0;
            soma_abs  <= 20'd0;
            soma_e2   <= 23'd0;
            max_combo <= 8'd0;
        end else if (zera) begin
            n_acertos <= 16'd0;
            soma_e    <= 21'd0;
            soma_abs  <= 20'd0;
            soma_e2   <= 23'd0;
            max_combo <= 8'd0;
        end else begin
            n_acertos <= n_acertos + {13'd0, d_n};
            soma_e    <= soma_e + {{12{d_e[8]}}, d_e};
            soma_abs  <= soma_abs + {14'd0, d_abs};
            soma_e2   <= soma_e2 + {13'd0, d_e2};
            if (combo > max_combo)
                max_combo <= combo;
        end
    end

    // ----------------------------------------------------------------
    // Contadores por trilha: notas julgadas e acertos
    // ----------------------------------------------------------------
    always @(posedge clock or posedge reset) begin
        if (reset) begin
            notas   <= 48'd0;
            acertos <= 48'd0;
        end else if (zera) begin
            notas   <= 48'd0;
            acertos <= 48'd0;
        end else begin
            for (k = 0; k < 4; k = k + 1) begin
                notas[12*k +: 12]   <= notas[12*k +: 12]
                                       + {11'd0, hit_pulse[k]}
                                       + {11'd0, escape_pulse[k]};
                acertos[12*k +: 12] <= acertos[12*k +: 12]
                                       + {11'd0, hit_pulse[k]};
            end
        end
    end

endmodule
