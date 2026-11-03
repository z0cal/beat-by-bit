/*-----------------------------------------------------------------------
 * Arquivo   : uart_rx.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Receptor UART 8N1 parametrizavel.
 *             Sincroniza a linha com 2 flip-flops, confirma o start bit
 *             no meio do bit e amostra cada bit de dados no meio.
 *             rx_valid fica em 1 por 1 ciclo quando um byte chega com
 *             stop bit valido (em 1); bytes com erro de quadro sao
 *             descartados.
 *-----------------------------------------------------------------------
 * Parametros:
 *   CLKS_PER_BIT : ciclos de clock por bit
 *                  Ex: 50_000_000 / 115_200 = 434 para 50MHz @ 115200 baud
 *-----------------------------------------------------------------------
 */

module uart_rx #(parameter CLKS_PER_BIT = 434) (
    input  wire       clock,
    input  wire       reset,
    input  wire       rx_serial,
    output reg  [7:0] rx_data,
    output reg        rx_valid
);

    // Estados internos
    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    reg [1:0]  state;
    reg [9:0]  clk_count;  // contador de ciclos por bit
    reg [2:0]  bit_index;  // qual bit de dados estamos recebendo (0-7)
    reg        rx_sync0, rx_sync1;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            rx_sync0 <= 1'b1;  // linha ociosa = HIGH
            rx_sync1 <= 1'b1;
        end else begin
            rx_sync0 <= rx_serial;
            rx_sync1 <= rx_sync0;
        end
    end

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            state     <= IDLE;
            clk_count <= 0;
            bit_index <= 0;
            rx_data   <= 0;
            rx_valid  <= 1'b0;
        end else begin
            rx_valid <= 1'b0;

            case (state)

                IDLE: begin
                    clk_count <= 0;
                    bit_index <= 0;
                    if (!rx_sync1)
                        state <= START;
                end

                // Meio do start bit: se a linha voltou a 1, era ruido
                START: begin
                    if (clk_count < CLKS_PER_BIT/2 - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count <= 0;
                        state     <= rx_sync1 ? IDLE : DATA;
                    end
                end

                DATA: begin
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count          <= 0;
                        rx_data[bit_index] <= rx_sync1;
                        if (bit_index < 7) begin
                            bit_index <= bit_index + 3'd1;
                        end else begin
                            bit_index <= 0;
                            state     <= STOP;
                        end
                    end
                end

                STOP: begin
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count <= 0;
                        rx_valid  <= rx_sync1;  // stop bit precisa ser HIGH
                        state     <= IDLE;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
