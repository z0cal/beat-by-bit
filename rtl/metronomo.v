/*-----------------------------------------------------------------------
 * Arquivo   : metronomo.v
 * Projeto   : Beat by Bit - Semestre 3
 *-----------------------------------------------------------------------
 * Descricao : Metronomo com servo. Gera um tempo a cada `periodo` ciclos
 *             de clock, em fase com as notas: o primeiro tempo ocorre
 *             `fase` ciclos depois do inicio do PLAY (frame_counter = 0).
 *
 *   rodando = 1 em PLAY: o contador anda. Com 0 (PAUSE, IDLE...) ele
 *             congela, sem perder a fase (os tempos so se deslocam pela
 *             duracao da pausa).
 *   reinicia = 1 (nivel ou pulso, no COUNTDOWN/IDLE): recarrega a fase,
 *             zera o lado e o pulso. Deve valer antes de rodando subir.
 *   modo    : 0 desligado, 1 fixo, 2 adaptativo (tratado como fixo
 *             nesta semana), 3 desligado.
 *   nivel_in: reservado para a S4 (adaptativo); hoje ignorado, nivel = 0.
 *
 *   Contagem exata: contador regressivo `resta` carregado com `fase` e
 *   recarregado com `periodo` a cada tempo (sem acumular erro). Se
 *   fase = 0 o primeiro tempo sai no primeiro ciclo. `tempo_pulso` dura
 *   1 ciclo, `fase` ciclos de PLAY apos o inicio (periodo >= 1).
 *
 *   Troca de andamento: k conta os tempos desde o reinicia (1o tempo = 0).
 *   O intervalo apos o tempo k e `periodo` se k < ntroca e `periodo2` se
 *   k >= ntroca (ntroca = FFFFFFFF: nunca troca).
 *
 *   Saidas: tempo_pulso, lado (inverte a cada tempo; 0 no inicio, 1o
 *   tempo -> 1), nivel (0 = 60 graus, 1 = 30, 2 = 15, 3 = parado; modo
 *   desligado = 3) e servo (PWM).
 *
 *   Interpretacao dos niveis: "60/30/15 graus" e a oscilacao TOTAL do
 *   braco; o braco vai a +A ou -A do centro conforme o lado, com
 *   A = 30, 15 e 7,5 graus. Mapeamento: 0 graus = 1,5 ms, +-90 graus =
 *   +-0,5 ms (so uso de +-30 graus: +-0,167 ms). Braco no centro quando
 *   rodando = 0, modo desligado ou nivel 3.
 *-----------------------------------------------------------------------
 */

module metronomo #(
    parameter CLK_HZ = 50_000_000
) (
    input  wire        clock,
    input  wire        reset,
    input  wire        rodando,
    input  wire        reinicia,
    input  wire [1:0]  modo,
    input  wire [1:0]  nivel_in,   // S4; ignorado nesta semana
    input  wire [31:0] periodo,    // ciclos por tempo
    input  wire [31:0] fase,       // ciclos ate o 1o tempo
    input  wire [31:0] periodo2,   // ciclos por tempo depois da troca de andamento
    input  wire [31:0] ntroca,     // indice do 1o tempo que usa periodo2 (FFFFFFFF = sem troca)
    output reg         tempo_pulso,
    output reg         lado,
    output wire [1:0]  nivel,
    output wire        servo
);

    wire ligado = (modo == 2'd1) || (modo == 2'd2);

    assign nivel = ligado ? 2'd0 : 2'd3;   // fixo: sempre 60 graus

    reg [31:0] resta;
    reg [31:0] k;       // indice do proximo tempo (0 = 1o); zera no reinicia

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            resta       <= 32'd0;
            k           <= 32'd0;
            tempo_pulso <= 1'b0;
            lado        <= 1'b0;
        end else begin
            tempo_pulso <= 1'b0;
            if (reinicia) begin
                resta <= fase;
                k     <= 32'd0;
                lado  <= 1'b0;
            end else if (rodando && ligado) begin
                if (resta <= 32'd1) begin
                    // intervalo ate o tempo k+1: periodo se k < ntroca, senao periodo2
                    // (tempo k chega em fase + k*periodo ate k = ntroca, ver bpm_params.vh)
                    resta       <= (k >= ntroca) ? periodo2 : periodo;
                    if (k != 32'hFFFFFFFF) k <= k + 32'd1;
                    tempo_pulso <= 1'b1;
                    lado        <= ~lado;
                end else
                    resta <= resta - 32'd1;
            end
        end
    end

    // Metade da faixa (+-90 graus = +-0,5 ms) e deslocamento por nivel
    localparam MEIA  = CLK_HZ / 2000;
    localparam CENTRO = CLK_HZ * 3 / 2000;
    localparam D0 = MEIA * 30 / 90;         // +-30 graus
    localparam D1 = MEIA * 15 / 90;         // +-15 graus
    localparam D2 = MEIA * 75 / 900;        // +-7,5 graus

    reg [19:0] largura;
    always @(*) begin
        largura = CENTRO[19:0];
        if (rodando && ligado) begin
            case (nivel)
                2'd0: largura = lado ? CENTRO[19:0] + D0[19:0] : CENTRO[19:0] - D0[19:0];
                2'd1: largura = lado ? CENTRO[19:0] + D1[19:0] : CENTRO[19:0] - D1[19:0];
                2'd2: largura = lado ? CENTRO[19:0] + D2[19:0] : CENTRO[19:0] - D2[19:0];
                default: largura = CENTRO[19:0];
            endcase
        end
    end

    servo_pwm #(.CLK_HZ(CLK_HZ)) u_servo (
        .clock  (clock),
        .reset  (reset),
        .largura(largura),
        .servo  (servo)
    );

endmodule
