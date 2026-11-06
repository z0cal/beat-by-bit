/* ------------------------------------------------------------------------
 *  Arquivo   : hcsr04.v
 *  Projeto   : Beat by Bit - Semestre 3
 * ------------------------------------------------------------------------
 *  Descricao : interface do sensor ultrassonico HC-SR04.
 *              - dispara TRIG com pulso de TRIG_US (10 us) a cada CICLO_MS
 *                (60 ms);
 *              - sincroniza o ECHO com 2 FFs;
 *              - mede a largura do ECHO sem divisor: um contador de us
 *                (CLK_HZ/1e6 ciclos) e um contador de US_POR_CM (58) us
 *                que incrementa dist_cm;
 *              - se o ECHO nao terminar (ou nem subir) em TIMEOUT_US apos o
 *                fim do TRIG, a medida termina com dist_cm = 0 (sem eco).
 *
 *  Saidas    : dist_cm    distancia em cm, saturada em 255 (0 = sem eco)
 *              leitura_ok pulso de 1 ciclo a cada medida concluida
 *                         (inclusive no timeout, com dist_cm = 0)
 *              trig       pulso de disparo para o sensor
 *
 *  Os parametros permitem acelerar a simulacao (CICLO_MS, TIMEOUT_US);
 *  CLK_HZ deve refletir o clock real para a conversao em cm.
 * ------------------------------------------------------------------------
 */

module hcsr04 #(
    parameter integer CLK_HZ     = 50_000_000,
    parameter integer CICLO_MS   = 60,
    parameter integer TRIG_US    = 10,
    parameter integer TIMEOUT_US = 25_000,
    parameter integer US_POR_CM  = 58
) (
    input  wire       clock,
    input  wire       reset,
    input  wire       echo,
    output reg        trig,
    output reg  [7:0] dist_cm,
    output reg        leitura_ok
);

    localparam integer CICLOS_US   = CLK_HZ / 1_000_000;
    localparam integer CICLOS_CICLO = (CLK_HZ / 1000) * CICLO_MS;
    localparam integer CICLOS_TRIG = CICLOS_US * TRIG_US;
    localparam integer CICLOS_TOUT = CICLOS_US * TIMEOUT_US;

    localparam [1:0] S_IDLE   = 2'd0;
    localparam [1:0] S_TRIG   = 2'd1;
    localparam [1:0] S_ESPERA = 2'd2;
    localparam [1:0] S_MEDE   = 2'd3;

    reg [1:0] estado;

    // sincronizador de 2 FFs + borda
    reg echo_s0, echo_s1, echo_prev;

    reg [25:0] cnt_ciclo;  // periodo entre disparos
    reg [25:0] cnt_fase;   // TRIG e timeout (ciclos desde o inicio da fase)
    reg [7:0]  cnt_us;     // ciclos dentro do us atual
    reg [6:0]  cnt_58;     // us dentro do cm atual
    reg [7:0]  cm;         // cm acumulados (saturado)

    wire borda_sobe  = echo_s1 & ~echo_prev;
    wire fim_ciclo   = (cnt_ciclo == CICLOS_CICLO - 1);
    wire fim_trig    = (cnt_fase  == CICLOS_TRIG - 1);
    wire estourou    = (cnt_fase  == CICLOS_TOUT - 1);
    wire tick_us     = (cnt_us    == CICLOS_US - 1);
    wire tick_cm     = tick_us && (cnt_58 == US_POR_CM - 1);

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            estado     <= S_IDLE;
            echo_s0    <= 1'b0;
            echo_s1    <= 1'b0;
            echo_prev  <= 1'b0;
            cnt_ciclo  <= 26'd0;
            cnt_fase   <= 26'd0;
            cnt_us     <= 8'd0;
            cnt_58     <= 7'd0;
            cm         <= 8'd0;
            trig       <= 1'b0;
            dist_cm    <= 8'd0;
            leitura_ok <= 1'b0;
        end else begin
            leitura_ok <= 1'b0;
            echo_s0    <= echo;
            echo_s1    <= echo_s0;
            echo_prev  <= echo_s1;

            // periodo de disparo
            if (fim_ciclo) cnt_ciclo <= 26'd0;
            else           cnt_ciclo <= cnt_ciclo + 26'd1;

            case (estado)
                S_IDLE: begin
                    cnt_fase <= 26'd0;
                    if (fim_ciclo) begin
                        trig   <= 1'b1;
                        estado <= S_TRIG;
                    end
                end

                S_TRIG: begin
                    if (fim_trig) begin
                        trig     <= 1'b0;
                        cnt_fase <= 26'd0;
                        estado   <= S_ESPERA;
                    end else begin
                        cnt_fase <= cnt_fase + 26'd1;
                    end
                end

                S_ESPERA: begin
                    if (borda_sobe) begin
                        // o ciclo da borda ja conta como 1 ciclo de ECHO
                        cnt_us   <= 8'd1;
                        cnt_58   <= 7'd0;
                        cm       <= 8'd0;
                        cnt_fase <= cnt_fase + 26'd1;
                        estado   <= S_MEDE;
                    end else if (estourou) begin
                        dist_cm    <= 8'd0;
                        leitura_ok <= 1'b1;
                        estado     <= S_IDLE;
                    end else begin
                        cnt_fase <= cnt_fase + 26'd1;
                    end
                end

                S_MEDE: begin
                    if (!echo_s1) begin
                        dist_cm    <= cm;
                        leitura_ok <= 1'b1;
                        estado     <= S_IDLE;
                    end else if (estourou) begin
                        dist_cm    <= 8'd0;
                        leitura_ok <= 1'b1;
                        estado     <= S_IDLE;
                    end else begin
                        cnt_fase <= cnt_fase + 26'd1;
                        cnt_us   <= tick_us ? 8'd0 : cnt_us + 8'd1;
                        if (tick_us)
                            cnt_58 <= tick_cm ? 7'd0 : cnt_58 + 7'd1;
                        if (tick_cm && cm != 8'd255)
                            cm <= cm + 8'd1;
                    end
                end
            endcase
        end
    end

endmodule
