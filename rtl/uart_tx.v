/*-----------------------------------------------------------------------
 * Arquivo   : uart_tx.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Transmissor UART 8N1 parametrizavel.
 *             Envia um byte serial (start bit, 8 bits de dados, stop bit).
 *             tx_valid deve ser mantido em 1 por 1 ciclo para iniciar
 *             a transmissao. tx_ready indica que o modulo esta livre.
 *-----------------------------------------------------------------------
 * Parametros:
 *   CLKS_PER_BIT : ciclos de clock por bit
 *                  Ex: 50_000_000 / 115_200 = 434 para 50MHz @ 115200 baud
 *-----------------------------------------------------------------------
 */

module uart_tx #(parameter CLKS_PER_BIT = 434) (
    input  wire       clock,
    input  wire       reset,
    input  wire [7:0] tx_data,
    input  wire       tx_valid,
    output reg        tx_serial,
    output wire       tx_ready
);

    // Estados internos
    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    reg [1:0]  state;
    reg [9:0]  clk_count;  // contador de ciclos por bit
    reg [2:0]  bit_index;  // qual bit de dados estamos enviando (0-7)
    reg [7:0]  tx_data_r;  // dado latched

    assign tx_ready = (state == IDLE);

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            state     <= IDLE;
            tx_serial <= 1'b1;  // linha ociosa = HIGH
            clk_count <= 0;
            bit_index <= 0;
            tx_data_r <= 0;
        end else begin
            case (state)

                IDLE: begin
                    tx_serial <= 1'b1;
                    clk_count <= 0;
                    bit_index <= 0;
                    if (tx_valid) begin
                        tx_data_r <= tx_data;
                        state     <= START;
                    end
                end

                START: begin
                    tx_serial <= 1'b0;  // start bit = LOW
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count <= 0;
                        state     <= DATA;
                    end
                end

                DATA: begin
                    tx_serial <= tx_data_r[bit_index];
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count <= 0;
                        if (bit_index < 7) begin
                            bit_index <= bit_index + 3'd1;
                        end else begin
                            bit_index <= 0;
                            state     <= STOP;
                        end
                    end
                end

                STOP: begin
                    tx_serial <= 1'b1;  // stop bit = HIGH
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 10'd1;
                    end else begin
                        clk_count <= 0;
                        state     <= IDLE;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
