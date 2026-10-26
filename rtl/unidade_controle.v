/*-----------------------------------------------------------------------
 * Arquivo   : unidade_controle.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : FSM do jogo.
 *             Adiciona estado SELECT para selecao de musica via botao 0.
 *
 * Estados:
 *   0000 = idle      Tela inicial
 *   0001 = countdown Contagem regressiva (3, 2, 1)
 *   0010 = play      Jogo ativo
 *   0011 = pause     Jogo pausado
 *   0100 = end_win   Vitoria (fim do chart sem 10 erros)
 *   0101 = end_lose  Derrota (10 erros)
 *   0110 = select    Selecao de musica
 *
 * Transicoes:
 *   idle    --(START)--> select
 *   select  --(song_ok/botao0)--> countdown
 *   select  --(START)--> idle
 *   countdown --(fim_contagem)--> play
 *   play    --(perdeu)--> end_lose
 *   play    --(fim_tempo)--> end_win
 *   play    --(START)--> pause
 *   pause   --(START)--> play
 *   end_win/end_lose --(START)--> idle
 *-----------------------------------------------------------------------
 */

module unidade_controle (
    input  wire       clock,
    input  wire       reset,
    input  wire       start,        // start_pulso do FD
    input  wire       song_ok,      // btn_pulse[0]: seleciona Concerning Hobbits
    input  wire       jogada,       // jogada_feita do FD
    input  wire       fim_contagem, // fim do countdown (3s)
    input  wire       fim_tempo,    // fim do chart - vem do FD
    input  wire       perdeu,       // misses >= 10 - vem do FD

    output reg        registraR,
    output reg        limpaR,
    output reg        zera_timer,
    output reg        conta_timer,
    output reg        game_active,  // HIGH apenas em estado PLAY
    output reg [3:0]  db_estado
);

    localparam idle      = 4'b0000;
    localparam countdown = 4'b0001;
    localparam play      = 4'b0010;
    localparam pause     = 4'b0011;
    localparam end_win   = 4'b0100;
    localparam end_lose  = 4'b0101;
    localparam select    = 4'b0110;

    reg [3:0] Eatual, Eprox;

    always @(posedge clock or posedge reset) begin
        if (reset) Eatual <= idle;
        else       Eatual <= Eprox;
    end

    always @* begin
        case (Eatual)
            idle:      Eprox = start    ? select    : idle;
            select:    Eprox = song_ok  ? countdown :
                               start    ? idle      : select;
            countdown: Eprox = fim_contagem ? play : countdown;
            play:      Eprox = perdeu    ? end_lose :
                               fim_tempo ? end_win  :
                               start     ? pause    : play;
            pause:     Eprox = song_ok ? idle :
                               start   ? play  : pause;
            end_win:   Eprox = start ? idle : end_win;
            end_lose:  Eprox = start ? idle : end_lose;
            default:   Eprox = idle;
        endcase
    end

    always @* begin
        // Timers zerados em idle, select, end_win e end_lose
        zera_timer  = (Eatual == idle || Eatual == select ||
                       Eatual == end_win || Eatual == end_lose) ? 1'b1 : 1'b0;
        conta_timer = (Eatual == countdown || Eatual == play) ? 1'b1 : 1'b0;
        game_active = (Eatual == play) ? 1'b1 : 1'b0;
        // Registros limpos em idle e select (antes de comecar a musica)
        limpaR      = (Eatual == idle || Eatual == select) ? 1'b1 : 1'b0;
        registraR   = (Eatual == play && jogada) ? 1'b1 : 1'b0;
        db_estado   = Eatual;
    end

endmodule
