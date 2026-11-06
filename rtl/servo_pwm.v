/*-----------------------------------------------------------------------
 * Arquivo   : servo_pwm.v
 * Projeto   : Beat by Bit - Semestre 3
 *-----------------------------------------------------------------------
 * Descricao : Gerador de PWM para servo (50 Hz). Periodo de 20 ms
 *             (CLK_HZ/50 ciclos) e pulso de 1 a 2 ms, centro em 1,5 ms.
 *
 *   largura = largura do pulso em CICLOS DE CLOCK. Vale 1 ms ..2 ms
 *             (CLK_HZ/1000 .. CLK_HZ/500); fora disso e saturada.
 *             Com 50 MHz: 50_000 (1 ms), 75_000 (1,5 ms), 100_000 (2 ms).
 *
 *   A largura e amostrada uma vez por periodo (no inicio do pulso), entao
 *   mudar `largura` no meio do periodo nao gera pulso cortado/glitch.
 *   O reset assincrono leva a largura ao centro (1,5 ms).
 *-----------------------------------------------------------------------
 */

module servo_pwm #(
    parameter CLK_HZ = 50_000_000
) (
    input  wire        clock,
    input  wire        reset,
    input  wire [19:0] largura,   // pulso em ciclos de clock
    output wire        servo
);

    localparam PERIODO = CLK_HZ / 50;     // 20 ms
    localparam L_MIN   = CLK_HZ / 1000;   // 1 ms
    localparam L_MAX   = CLK_HZ / 500;    // 2 ms
    localparam L_CEN   = CLK_HZ * 3 / 2000; // 1,5 ms

    reg [19:0] cnt;
    reg [19:0] larg_lat;

    wire [19:0] larg_sat = (largura < L_MIN) ? L_MIN[19:0] :
                           (largura > L_MAX) ? L_MAX[19:0] : largura;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            cnt      <= 20'd0;
            larg_lat <= L_CEN[19:0];
        end else if (cnt == PERIODO - 1) begin
            cnt      <= 20'd0;
            larg_lat <= larg_sat;
        end else
            cnt <= cnt + 20'd1;
    end

    assign servo = (cnt < larg_lat);

endmodule
