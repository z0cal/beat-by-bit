/*-----------------------------------------------------------------------
 * Arquivo   : packet_sender.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Monta e envia um pacote de 22 bytes via UART a cada
 *             frame_tick. Aguarda o uart_tx ficar pronto entre bytes.
 *
 * Formato do pacote (22 bytes):
 *   [0]    0x55          marcador de inicio
 *   [1]    game_state    0=IDLE 1=COUNTDOWN 2=PLAY 3=PAUSE 4=WIN 5=LOSE
 *   [2]    countdown_sec 3,2,1,0
 *   [3]    trilha0_nota0 age (0xFF = vazio)
 *   [4]    trilha0_nota1 age
 *   [5]    trilha0_nota2 age
 *   [6-8]  trilha1 notas
 *   [9-11] trilha2 notas
 *  [12-14] trilha3 notas
 *  [15]    score[15:8]
 *  [16]    score[7:0]
 *  [17]    misses
 *  [18]    combo
 *  [19]    {2'b00, frame_counter[13:8]}  (6 bits altos)
 *  [20]    frame_counter[7:0]            (8 bits baixos)
 *  [21]    0xAA          marcador de fim
 *-----------------------------------------------------------------------
 */

module packet_sender (
    input  wire        clock,
    input  wire        reset,
    input  wire        frame_tick,      // pulso de 1 ciclo a 60fps

    // Estado do jogo
    input  wire [3:0]  game_state,
    input  wire [7:0]  countdown_sec,

    // Ages das notas: trilha[t], slot[s]
    input  wire [7:0]  t0n0, t0n1, t0n2,
    input  wire [7:0]  t1n0, t1n1, t1n2,
    input  wire [7:0]  t2n0, t2n1, t2n2,
    input  wire [7:0]  t3n0, t3n1, t3n2,

    // Placar
    input  wire [15:0] score,
    input  wire [7:0]  misses,
    input  wire [7:0]  combo,

    // Contador de frames (para barra de progresso no Python)
    input  wire [13:0] frame_counter,

    // ID da musica selecionada (bits 7:6 do byte 19)
    input  wire [1:0]  song_id,

    // Saida serial
    output wire        uart_tx_out
);

    // Interface com uart_tx
    reg  [7:0] tx_data;
    reg        tx_valid;
    wire       tx_ready;

    uart_tx #(.CLKS_PER_BIT(434)) u_tx (
        .clock     (clock),
        .reset     (reset),
        .tx_data   (tx_data),
        .tx_valid  (tx_valid),
        .tx_serial (uart_tx_out),
        .tx_ready  (tx_ready)
    );

    // Maquina de estados: espera frame_tick, depois envia 20 bytes
    localparam WAIT   = 2'd0;  // aguardando proximo frame
    localparam LOAD   = 2'd1;  // carrega byte atual no tx
    localparam SEND   = 2'd2;  // aguarda uart_tx absorver o byte
    localparam DONE   = 2'd3;  // pacote completo, volta a WAIT

    reg [1:0]  state;
    reg [4:0]  byte_idx;  // 0-21

    // Buffer do pacote (latchado no frame_tick para consistencia)
    reg [7:0] pkt [0:21];

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            state    <= WAIT;
            byte_idx <= 0;
            tx_valid <= 0;
        end else begin
            tx_valid <= 0;  // pulso de 1 ciclo

            case (state)

                WAIT: begin
                    if (frame_tick) begin
                        // Captura estado atual no buffer do pacote
                        pkt[0]  <= 8'h55;
                        pkt[1]  <= {4'b0000, game_state};
                        pkt[2]  <= countdown_sec;
                        pkt[3]  <= t0n0; pkt[4]  <= t0n1; pkt[5]  <= t0n2;
                        pkt[6]  <= t1n0; pkt[7]  <= t1n1; pkt[8]  <= t1n2;
                        pkt[9]  <= t2n0; pkt[10] <= t2n1; pkt[11] <= t2n2;
                        pkt[12] <= t3n0; pkt[13] <= t3n1; pkt[14] <= t3n2;
                        pkt[15] <= score[15:8];
                        pkt[16] <= score[7:0];
                        pkt[17] <= misses;
                        pkt[18] <= combo;
                        pkt[19] <= {song_id[1:0], frame_counter[13:8]};
                        pkt[20] <= frame_counter[7:0];
                        pkt[21] <= 8'hAA;
                        byte_idx <= 0;
                        state    <= LOAD;
                    end
                end

                LOAD: begin
                    if (tx_ready) begin
                        tx_data  <= pkt[byte_idx];
                        tx_valid <= 1;
                        state    <= SEND;
                    end
                end

                SEND: begin
                    // Aguarda uart_tx comecar a transmitir (tx_ready cai)
                    if (!tx_ready) begin
                        if (byte_idx == 21) begin
                            state <= DONE;
                        end else begin
                            byte_idx <= byte_idx + 5'd1;
                            state    <= LOAD;
                        end
                    end
                end

                DONE: begin
                    // Aguarda o ultimo byte terminar antes de aceitar novo frame
                    if (tx_ready)
                        state <= WAIT;
                end

                default: state <= WAIT;
            endcase
        end
    end

endmodule
