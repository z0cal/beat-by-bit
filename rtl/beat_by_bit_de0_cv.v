/*-----------------------------------------------------------------------
 * Arquivo   : beat_by_bit_de0_cv.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Wrapper especifico da placa DE0-CV. Liga o clock fisico,
 *             adapta a polaridade dos botoes KEY (ativos em 0) e
 *             expõe um pino GPIO dedicado para a UART TX.
 *
 * Mapeamento adotado:
 *   KEY0  -> reset
 *   KEY1  -> start/pause
 *   KEY2  -> trilha 0  (botao, ativo em 0)
 *   KEY3  -> trilha 1  (botao, ativo em 0)
 *   HEX0  -> estado da FSM
 *   HEX1  -> ultima jogada
 *   LEDR0-4 -> pulsos de debug
 *   GPIO_0_D0 -> uart_tx_out
 *
 * Nota: DE0-CV tem apenas 4 KEYs; por isso so 2 trilhas sao jogaveis.
 *       O spawn foi limitado as trilhas 0 e 1 em fluxo_dados.v.
 *-----------------------------------------------------------------------
 */

module beat_by_bit_de0_cv (
    input  wire       CLOCK_50,
    input  wire       KEY0,
    input  wire       KEY1,
    input  wire       KEY2,
    input  wire       KEY3,
    output wire [6:0] HEX0,
    output wire [6:0] HEX1,
    output wire [9:0] LEDR,
    output wire       GPIO_0_D0
);

    wire [4:0] botoes;
    wire [6:0] db_estado;
    wire [6:0] db_jogada;
    wire [4:0] leds_pulsos;
    wire       uart_tx_out;

    // Todos os KEYs sao ativos em 0; invertidos para logica ativa em 1.
    // Trilhas 2 e 3 desabilitadas (sem botoes disponiveis).
    assign botoes = {~KEY1, 1'b0, 1'b0, ~KEY3, ~KEY2};

    beat_by_bit u_core (
        .clock      (CLOCK_50),
        .reset      (~KEY0),
        .botoes     (botoes),
        .db_estado  (db_estado),
        .db_jogada  (db_jogada),
        .leds_pulsos(leds_pulsos),
        .uart_tx_out(uart_tx_out)
    );

    assign HEX0      = db_estado;
    assign HEX1      = db_jogada;
    assign LEDR[4:0] = leds_pulsos;
    // Debug: sinais crus dos botoes (antes do debounce)
    assign LEDR[5]   = ~KEY2;  // trilha 0 cru
    assign LEDR[6]   = ~KEY3;  // trilha 1 cru
    assign LEDR[7]   = ~KEY1;  // start cru
    assign LEDR[8]   = ~KEY0;  // reset cru
    assign LEDR[9]   = 1'b0;
    assign GPIO_0_D0 = uart_tx_out;

endmodule
