/*-----------------------------------------------------------------------
 * Arquivo   : uart_link.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Indicador de link serial com o PC.
 *             O Python devolve ACK_BYTE pelo TXD do CP2102 enquanto
 *             recebe pacotes validos. link_ok fica em 1 enquanto chegam
 *             ACKs e cai TIMEOUT_CYCLES depois do ultimo.
 *             link_ok = 1 prova o caminho completo: FPGA -> RXD do
 *             CP2102 -> Python -> TXD do CP2102 -> FPGA.
 *-----------------------------------------------------------------------
 * Parametros:
 *   CLKS_PER_BIT   : ciclos de clock por bit (54 = 921600 baud @ 50MHz)
 *   TIMEOUT_CYCLES : ciclos sem ACK ate apagar (50_000_000 = 1 s)
 *   ACK_BYTE       : byte de confirmacao enviado pelo PC
 *-----------------------------------------------------------------------
 */

module uart_link #(
    parameter CLKS_PER_BIT   = 54,
    parameter TIMEOUT_CYCLES = 50_000_000,
    parameter ACK_BYTE       = 8'hA5
) (
    input  wire clock,
    input  wire reset,
    input  wire rx_serial,
    output wire link_ok
);

    localparam W = $clog2(TIMEOUT_CYCLES + 1);

    wire [7:0] rx_data;
    wire       rx_valid;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clock    (clock),
        .reset    (reset),
        .rx_serial(rx_serial),
        .rx_data  (rx_data),
        .rx_valid (rx_valid)
    );

    reg [W-1:0] restante;  // ciclos ate apagar o LED

    always @(posedge clock or posedge reset) begin
        if (reset)
            restante <= 0;
        else if (rx_valid && rx_data == ACK_BYTE)
            restante <= TIMEOUT_CYCLES[W-1:0];
        else if (restante != 0)
            restante <= restante - 1'b1;
    end

    assign link_ok = (restante != 0);

endmodule
