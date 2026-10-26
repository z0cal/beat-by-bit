/*-----------------------------------------------------------------------
 * Arquivo   : note_track.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Gerenciador de notas para UMA trilha do jogo.
 *             Mantem ate 3 notas simultaneas, cada uma com contador
 *             de "age" (quadros desde o spawn).
 *
 *             Convercao age -> posicao Y no Python: Y = age * NOTE_SPEED
 *             NOTE_SPEED = 4  =>  age=130 -> Y=520 (hit zone center)
 *
 * Parametros de jogo:
 *   HIT_CENTER = 130  (age quando nota esta no centro da hit zone)
 *   HIT_WINDOW = 11   (tolerancia: ±11 frames = ±44 pixels)
 *   MISS_AGE   = 151  (nota sai da tela apos 151 frames)
 *   EMPTY      = 0xFF (slot vazio)
 *-----------------------------------------------------------------------
 */

module note_track (
    input  wire clock,
    input  wire reset,

    // Pulso de 1 ciclo a cada frame (~60fps), vindo do fluxo_dados
    input  wire frame_tick,

    // Habilitacao geral: notas so se movem durante o jogo
    input  wire game_active,

    // LFSR autoriza spawn nesta trilha neste frame
    input  wire spawn_en,

    // Borda de subida do botao desta trilha (detectada externamente)
    input  wire btn_press,

    // Ages dos 3 slots (0xFF = slot vazio)
    output reg [7:0] note0,
    output reg [7:0] note1,
    output reg [7:0] note2,

    // Pulsos de 1 ciclo
    output reg hit_pulse,     // acerto: botao na janela de uma nota
    output reg escape_pulse,  // erro: nota passou sem ser apertada
    output reg bad_press      // erro: botao apertado sem nota na janela
);

    localparam EMPTY      = 8'hFF;
    localparam HIT_CENTER = 8'd130;
    localparam HIT_WINDOW = 8'd11;
    localparam MISS_AGE   = 8'd151;

    // ----------------------------------------------------------------
    // Funcao auxiliar: verifica se age esta na janela de acerto
    // |age - 130| <= 11  =>  119 <= age <= 141
    // ----------------------------------------------------------------
    function automatic in_hit_window;
        input [7:0] age;
        begin
            in_hit_window = (age >= (HIT_CENTER - HIT_WINDOW)) &&
                            (age <= (HIT_CENTER + HIT_WINDOW));
        end
    endfunction

    // ----------------------------------------------------------------
    // Logica principal
    // ----------------------------------------------------------------
    integer i;
    reg [7:0] ages [2:0];   // 3 slots internos
    reg       valid [2:0];  // 1 = slot ocupado

    // Saidas combinatorias dos slots
    always @* begin
        note0 = valid[0] ? ages[0] : EMPTY;
        note1 = valid[1] ? ages[1] : EMPTY;
        note2 = valid[2] ? ages[2] : EMPTY;
    end

    // Logica sequencial
    always @(posedge clock or posedge reset) begin
        if (reset) begin
            ages[0]  <= 0; valid[0] <= 0;
            ages[1]  <= 0; valid[1] <= 0;
            ages[2]  <= 0; valid[2] <= 0;
            hit_pulse    <= 0;
            escape_pulse <= 0;
            bad_press    <= 0;
        end else begin
            // Limpa pulsos a cada ciclo
            hit_pulse    <= 0;
            escape_pulse <= 0;
            bad_press    <= 0;

            // --- Avanca notas a cada frame ---
            if (frame_tick && game_active) begin
                for (i = 0; i < 3; i = i + 1) begin
                    if (valid[i]) begin
                        if (ages[i] >= MISS_AGE) begin
                            // Nota saiu da tela sem ser apertada
                            valid[i]     <= 0;
                            escape_pulse <= 1;
                        end else begin
                            ages[i] <= ages[i] + 8'd1;
                        end
                    end
                end

                // --- Spawn: insere nota no primeiro slot livre ---
                if (spawn_en) begin
                    if (!valid[0]) begin
                        ages[0]  <= 0;
                        valid[0] <= 1;
                    end else if (!valid[1]) begin
                        ages[1]  <= 0;
                        valid[1] <= 1;
                    end else if (!valid[2]) begin
                        ages[2]  <= 0;
                        valid[2] <= 1;
                    end
                    // Se todos os slots estao ocupados, ignora o spawn
                end
            end

            // --- Deteccao de botao pressionado ---
            // (independente do frame_tick, responde imediatamente)
            if (btn_press && game_active) begin
                // Procura a nota com age mais proximo do centro da hit zone
                // Prioridade: slot 0 > slot 1 > slot 2
                // Verifica se alguma nota esta dentro da janela
                if (valid[0] && in_hit_window(ages[0])) begin
                    valid[0]  <= 0;
                    hit_pulse <= 1;
                end else if (valid[1] && in_hit_window(ages[1])) begin
                    valid[1]  <= 0;
                    hit_pulse <= 1;
                end else if (valid[2] && in_hit_window(ages[2])) begin
                    valid[2]  <= 0;
                    hit_pulse <= 1;
                end else begin
                    // Botao pressionado sem nota na janela
                    bad_press <= 1;
                end
            end
        end
    end

endmodule
