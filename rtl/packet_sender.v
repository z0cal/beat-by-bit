/*-----------------------------------------------------------------------
 * Arquivo   : packet_sender.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Monta e envia um pacote de 49 bytes via UART a cada
 *             frame_tick. Aguarda o uart_tx ficar pronto entre bytes.
 *             49 bytes levam ~0,5 ms a 1 Mbaud (~4,3 ms a 115200); o
 *             quadro tem 16,7 ms.
 *
 * Formato do pacote (49 bytes, valores multibyte em big-endian):
 *   [0]    0x55          marcador de inicio
 *   [1]    {eq[3:0], game_state}
 *            game_state  0=IDLE 1=COUNTDOWN 2=PLAY 3=PAUSE 4=WIN 5=LOSE
 *                        6=SELECT 7=SETTINGS
 *            eq[1:0]=GRAVES (bits 5:4), eq[3:2]=MEDIOS (bits 7:6)
 *   [2]    {eq[7:4], countdown_sec[3:0]}
 *            countdown   3,2,1,0
 *            eq[5:4]=AGUDOS (bits 5:4), eq[7:6]=BRILHO (bits 7:6)
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
 *  [19]    {song_id, frame_counter[13:8]}  (6 bits altos)
 *  [20]    frame_counter[7:0]            (8 bits baixos)
 *  --- metricas da sessao (metricas.v) ---
 *  [21]    maior combo
 *  [22-23] N: acertos em botao (16 bits)
 *  [24-26] soma de e = age - 130 (24 bits, complemento de 2)
 *  [27-29] soma de |e|  (24 bits)
 *  [30-32] soma de e^2  (24 bits)
 *  [33-44] por trilha t (3 bytes cada, a partir de 33 + 3t):
 *            {notas[t][11:0], acertos[t][11:0]}
 *  --- nome do perfil (nome_perfil.v) ---
 *  [45]    {cursor[1:0], c0}   c0 = caractere da esquerda
 *  [46]    {2'b00, c1}
 *  [47]    {2'b00, c2}
 *            caractere: 0='-', 1..26='A'..'Z', 27..36='0'..'9'
 *  [48]    0xAA          marcador de fim
 *-----------------------------------------------------------------------
 */

module packet_sender #(
    parameter CLKS_PER_BIT = 50      // 50 MHz / 1 Mbaud (o top passa o valor); o TB encurta
) (
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

    // Niveis do atenuador de frequencias (nibbles altos dos bytes 1 e 2)
    input  wire [7:0]  eq,

    // Metricas da sessao (ver metricas.v)
    input  wire [7:0]  met_max_combo,
    input  wire [15:0] met_n,
    input  wire [20:0] met_soma_e,
    input  wire [19:0] met_soma_abs,
    input  wire [22:0] met_soma_e2,
    input  wire [47:0] met_notas,      // 12 bits por trilha
    input  wire [47:0] met_acertos,    // 12 bits por trilha

    // Nome do perfil (ver nome_perfil.v)
    input  wire [17:0] nome,
    input  wire [1:0]  nome_cursor,

    // Saida serial
    output wire        uart_tx_out
);

    // Interface com uart_tx
    reg  [7:0] tx_data;
    reg        tx_valid;
    wire       tx_ready;

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clock     (clock),
        .reset     (reset),
        .tx_data   (tx_data),
        .tx_valid  (tx_valid),
        .tx_serial (uart_tx_out),
        .tx_ready  (tx_ready)
    );

    localparam integer PACKET_SIZE = 49;

    // Maquina de estados: espera frame_tick, depois envia PACKET_SIZE bytes
    localparam WAIT   = 2'd0;  // aguardando proximo frame
    localparam LOAD   = 2'd1;  // carrega byte atual no tx
    localparam SEND   = 2'd2;  // aguarda uart_tx absorver o byte
    localparam DONE   = 2'd3;  // pacote completo, volta a WAIT

    reg [1:0]  state;
    reg [5:0]  byte_idx;  // 0 a PACKET_SIZE-1

    // Buffer do pacote (latchado no frame_tick para consistencia)
    reg [7:0] pkt [0:PACKET_SIZE-1];

    // Somas estendidas para 24 bits (soma_e com sinal)
    wire [23:0] soma_e_24   = {{3{met_soma_e[20]}}, met_soma_e};
    wire [23:0] soma_abs_24 = {4'd0, met_soma_abs};
    wire [23:0] soma_e2_24  = {1'b0, met_soma_e2};
    integer t;

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
                        pkt[1]  <= {eq[3:0], game_state};
                        pkt[2]  <= {eq[7:4], countdown_sec[3:0]};
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
                        pkt[21] <= met_max_combo;
                        pkt[22] <= met_n[15:8];
                        pkt[23] <= met_n[7:0];
                        pkt[24] <= soma_e_24[23:16];
                        pkt[25] <= soma_e_24[15:8];
                        pkt[26] <= soma_e_24[7:0];
                        pkt[27] <= soma_abs_24[23:16];
                        pkt[28] <= soma_abs_24[15:8];
                        pkt[29] <= soma_abs_24[7:0];
                        pkt[30] <= soma_e2_24[23:16];
                        pkt[31] <= soma_e2_24[15:8];
                        pkt[32] <= soma_e2_24[7:0];
                        for (t = 0; t < 4; t = t + 1) begin
                            pkt[33 + 3*t] <= met_notas[12*t + 4 +: 8];
                            pkt[34 + 3*t] <= {met_notas[12*t +: 4],
                                              met_acertos[12*t + 8 +: 4]};
                            pkt[35 + 3*t] <= met_acertos[12*t +: 8];
                        end
                        pkt[45] <= {nome_cursor, nome[5:0]};
                        pkt[46] <= {2'b00, nome[11:6]};
                        pkt[47] <= {2'b00, nome[17:12]};
                        pkt[48] <= 8'hAA;
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
                        if (byte_idx == PACKET_SIZE - 1) begin
                            state <= DONE;
                        end else begin
                            byte_idx <= byte_idx + 6'd1;
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
