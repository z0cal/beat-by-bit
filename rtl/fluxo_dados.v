/*-----------------------------------------------------------------------
 * Arquivo   : fluxo_dados.v
 * Projeto   : Beat by Bit - Semestre 2
 *-----------------------------------------------------------------------
 * Descricao : Fluxo de dados completo do jogo. Toda a logica do jogo
 *             esta aqui. O Python so renderiza o que este modulo produz.
 *
 * Logica implementada:
 *   - Gerador de frame_tick (~60fps com clock de 50MHz)
 *   - Timer de countdown (3 segundos = 180 frames)
 *   - 4 ROMs de chart de 4 bits (4 trilhas)
 *   - Contador de frames para indexar a ROM (depth dinamico por musica)
 *   - 4x note_track (uma por trilha)
 *   - hcsr04 + gesto_detector (confirmacao em SETTINGS) + energia (barra/x2)
 *   - cfg_jogo (pagina "Jogo") e metronomo (servo)
 *   - Contadores de score (dobrado com x2_ativo), misses e combo
 *   - Debounce + deteccao de borda para os botoes
 *   - Saida song_ok_pulso para selecao de musica na UC (btn0/1/2)
 *   - Registrador song_id_reg para selecionar qual ROM usar
 *   - Registrador do atenuador de frequencias (eq_config), ajustado
 *     pelos botoes de trilha no estado SETTINGS
 *   - Bloco de metricas da sessao (metricas.v): assincronia de cada
 *     acerto, notas e acertos por trilha e maior combo. Zera em
 *     COUNTDOWN e fica congelado em PAUSE, END e IDLE
 *   - Nome do perfil em estilo arcade (nome_perfil.v), editado pelos
 *     botoes de trilha no fim de musica (END_WIN/END_LOSE)
 *-----------------------------------------------------------------------
 * Clock assumido: 50 MHz
 *   frame_tick: M = 833_333 ciclos (= 50MHz / 60fps)
 *   countdown:  180 frames = 3 segundos
 *   ROM depths: 3831 (Hobbits), 4219 (Ocarina), 3880 (Power Rangers)
 *   debounce:   9_000_000 ciclos (= 180ms)
 *-----------------------------------------------------------------------
 */

// Caminho relativo ao diretorio de execucao (igual aos $readmemh abaixo): o
// Quartus e o yosys rodam em quartus/de0_cv/ (macro QUARTUS), o Icarus na raiz.
`ifdef QUARTUS
`include "../../rtl/bpm_params.vh"
`else
`include "rtl/bpm_params.vh"
`endif

module fluxo_dados #(
    // Sensor HC-SR04: defaults reais; os testbenches encurtam.
    parameter integer HC_CLK_HZ     = 50_000_000,   // clock visto pelo hcsr04 (TB: 1 MHz = 1 ciclo/us)
    parameter integer HC_CICLO_MS   = 60,
    parameter integer HC_TIMEOUT_US = 25_000
) (
    input  wire       clock,
    input  wire       reset,

    // Sinais de controle vindos da UC
    input  wire       limpaR,
    input  wire       registraR,
    input  wire       zera_timer,
    input  wire       conta_timer,     // legado (nao usado ativamente)
    input  wire       game_active,     // ativo so em PLAY
    input  wire [3:0] db_estado,

    // Entradas fisicas
    input  wire [4:0] botoes_raw,      // [4]=Start/Pause, [3:0]=botoes do jogo

    // HC-SR04 e servo do metronomo
    input  wire       hcsr04_echo,
    output wire       hcsr04_trig,
    output wire       servo_pwm,

    // Saidas para UC
    output wire       jogada_feita,
    output wire       start_pulso,
    output wire       fim_contagem,    // fim do countdown (3s)
    output wire       fim_tempo,       // fim do chart
    output wire       perdeu,          // misses >= 10
    output wire       song_ok_pulso,   // botao 0 pressionado (selecao de musica)

    // Saida de debug (display HEX)
    output wire [3:0] s_jogada,

    // Saida de countdown para exibicao
    output wire [7:0] countdown_sec,   // 3,2,1,0

    // Ages das notas por trilha/slot para o packet_sender
    output wire [7:0] t0n0, t0n1, t0n2,
    output wire [7:0] t1n0, t1n1, t1n2,
    output wire [7:0] t2n0, t2n1, t2n2,
    output wire [7:0] t3n0, t3n1, t3n2,

    // Placar
    output wire [15:0] score,
    output wire [7:0]  misses,
    output wire [7:0]  combo,

    // Frame tick e contador exposto para o packet_sender no top-level
    output wire        frame_tick_out,
    output wire [13:0] frame_counter_out,

    // ID da musica selecionada (0=Hobbits, 1=Ocarina, 2=Power Rangers)
    output wire [1:0]  song_id_out,

    // Niveis do atenuador de frequencias (2 bits por faixa, ver eq_config.v)
    output wire [7:0]  eq_out,

    // Metricas da sessao (ver metricas.v)
    output wire [15:0] met_n,          // acertos em botao
    output wire [20:0] met_soma_e,     // soma de e = age - 130, com sinal
    output wire [19:0] met_soma_abs,   // soma de |e|
    output wire [22:0] met_soma_e2,    // soma de e^2
    output wire [47:0] met_notas,      // notas julgadas, 12 bits por trilha
    output wire [47:0] met_acertos,    // acertos, 12 bits por trilha
    output wire [7:0]  met_max_combo,  // maior combo da sessao

    // Nome do perfil: 3 caracteres de 6 bits e cursor (ver nome_perfil.v)
    output wire [17:0] nome_out,
    output wire [1:0]  nome_cursor,

    // Sensor HC-SR04, barra de energia (x2), configuracao "Jogo" e metronomo
    output wire [7:0]  dist_cm_out,
    output wire [7:0]  energia_out,      // 0..255; no x2 = tempo restante escalado
    output wire        x2_ativo_out,
    output wire [6:0]  ativacoes_out,    // ativacoes do x2 na sessao (0..127)
    output wire [15:0] movimentos_out,   // movimentos da mao na sessao
    output wire [2:0]  progresso_out,    // progresso do gesto de confirmacao (0..7)
    output wire        pagina_out,       // 0 = atenuador, 1 = jogo
    output wire        sem_som_out,
    output wire        sensorial_out,
    output wire [1:0]  modo_met_out,
    output wire        met_lado,
    output wire [1:0]  met_nivel
);

    // ----------------------------------------------------------------
    // Separacao dos botoes
    // ----------------------------------------------------------------
    wire [3:0] botoes_trilha = botoes_raw[3:0];
    wire       start_raw     = botoes_raw[4];

    // ----------------------------------------------------------------
    // Debounce + pulso imediato: 180ms @ 50MHz = 9_000_000 ciclos.
    // ----------------------------------------------------------------
    localparam integer BTN_DEBOUNCE_CYCLES = 9_000_000;
    wire [3:0] btn_pulse;

    // Todos os botoes usam apenas reset (sem limpaR): song_ok precisa ser detectado
    // no estado SELECT, onde limpaR=1 (UC mantem limpaR alto em idle e select).
    debounce_pulse #(.DEBOUNCE_CYCLES(BTN_DEBOUNCE_CYCLES))
        u_db_btn0 (.clock(clock), .reset(reset), .sinal(botoes_trilha[0]), .pulso(btn_pulse[0]));
    debounce_pulse #(.DEBOUNCE_CYCLES(BTN_DEBOUNCE_CYCLES))
        u_db_btn1 (.clock(clock), .reset(reset), .sinal(botoes_trilha[1]), .pulso(btn_pulse[1]));
    debounce_pulse #(.DEBOUNCE_CYCLES(BTN_DEBOUNCE_CYCLES))
        u_db_btn2 (.clock(clock), .reset(reset), .sinal(botoes_trilha[2]), .pulso(btn_pulse[2]));
    debounce_pulse #(.DEBOUNCE_CYCLES(BTN_DEBOUNCE_CYCLES))
        u_db_btn3 (.clock(clock), .reset(reset), .sinal(botoes_trilha[3]), .pulso(btn_pulse[3]));
    debounce_pulse #(.DEBOUNCE_CYCLES(BTN_DEBOUNCE_CYCLES))
        u_db_start (.clock(clock), .reset(reset), .sinal(start_raw), .pulso(start_pulso));

    assign jogada_feita  = |btn_pulse;
    // Qualquer dos 4 botoes de selecao de musica dispara song_ok para a UC
    assign song_ok_pulso = btn_pulse[0] | btn_pulse[1] | btn_pulse[2] | btn_pulse[3];

    // Registra qual musica foi selecionada (0=Hobbits, 1=Ocarina, 2=Power Rangers).
    // So no estado SELECT: evita que apertar trilhas durante o jogo altere a
    // ROM ativa, e que o aperto que abre as configuracoes (IDLE) troque a musica.
    reg [1:0] song_id_reg;
    always @(posedge clock or posedge reset) begin
        if (reset)                    song_id_reg <= 2'd0;
        else if (db_estado == 4'b0110) begin
            if      (btn_pulse[0])    song_id_reg <= 2'd0;
            else if (btn_pulse[1])    song_id_reg <= 2'd1;
            else if (btn_pulse[2])    song_id_reg <= 2'd2;
            else if (btn_pulse[3])    song_id_reg <= 2'd3;
        end
    end

    // ----------------------------------------------------------------
    // Atenuador de frequencias: cada trilha avanca o nivel da sua faixa,
    // so no estado SETTINGS (db_estado == 7)
    // ----------------------------------------------------------------
    wire em_settings = (db_estado == 4'b0111);
    wire pagina;
    wire [1:0] modo_met;
    wire sensorial, sem_som;
    wire confirma;
    wire [2:0] progresso;
    wire [7:0] dist_cm;
    wire leitura_ok;
    wire [7:0]  energia_val;
    wire        x2_ativo;
    wire [6:0]  ativacoes;
    wire [15:0] movimentos;

    // O eq_config so recebe pulsos na pagina 0 (atenuador)
    eq_config u_eq (
        .clock   (clock),
        .reset   (reset),
        .habilita(em_settings),
        .pulso   (btn_pulse & {4{~pagina}}),
        .eq      (eq_out)
    );

    // Pagina 1 ("Jogo"): metronomo, perfil sensorial, sem som; o gesto troca a pagina
    cfg_jogo u_cfg (
        .clock    (clock),
        .reset    (reset),
        .habilita (em_settings),
        .confirma (confirma),
        .pulso    (btn_pulse),
        .pagina   (pagina),
        .modo_met (modo_met),
        .sensorial(sensorial),
        .sem_som  (sem_som)
    );

    // HC-SR04 + detector de gesto (confirmacao em SETTINGS) + barra de energia (PLAY)
    hcsr04 #(.CLK_HZ(HC_CLK_HZ), .CICLO_MS(HC_CICLO_MS), .TIMEOUT_US(HC_TIMEOUT_US)) u_hcsr04 (
        .clock(clock), .reset(reset), .echo(hcsr04_echo),
        .trig(hcsr04_trig), .dist_cm(dist_cm), .leitura_ok(leitura_ok)
    );

    gesto_detector u_gesto (
        .clock(clock), .reset(reset), .habilita_confirma(em_settings),
        .leitura_ok(leitura_ok), .dist_cm(dist_cm),
        .na_faixa(), .gesto_pulso(),
        .confirma(confirma), .progresso(progresso)
    );

    assign dist_cm_out    = dist_cm;
    assign energia_out    = energia_val;
    assign x2_ativo_out   = x2_ativo;
    assign ativacoes_out  = ativacoes;
    assign movimentos_out = movimentos;
    assign progresso_out  = progresso;
    assign pagina_out     = pagina;
    assign sem_som_out    = sem_som;
    assign sensorial_out  = sensorial;
    assign modo_met_out   = modo_met;

    // ----------------------------------------------------------------
    // Nome do perfil: D/F trocam o caractere, J/K movem o cursor, so no
    // fim de musica (db_estado == 4 ou 5)
    // ----------------------------------------------------------------
    nome_perfil u_nome (
        .clock   (clock),
        .reset   (reset),
        .habilita((db_estado == 4'b0100) || (db_estado == 4'b0101)),
        .pulso   (btn_pulse),
        .nome    (nome_out),
        .cursor  (nome_cursor)
    );

    // ----------------------------------------------------------------
    // Frame tick: 1 pulso por frame (~60fps)
    // M = 833_333 para clock de 50MHz
    // ----------------------------------------------------------------
    wire frame_tick;

    contador_m #(.M(833_333), .N(20)) u_frame_counter (
        .clock  (clock),
        .zera_as(reset),
        .zera_s (1'b0),
        .conta  (1'b1),
        .Q      (),
        .fim    (frame_tick),
        .meio   ()
    );

    // ----------------------------------------------------------------
    // Timer de countdown: 180 frames = 3 segundos
    // Conta apenas quando UC esta em estado COUNTDOWN (db_estado == 1)
    // ----------------------------------------------------------------
    wire [7:0] cd_frame_q;
    wire       s_fim_contagem;

    contador_m #(.M(180), .N(8)) u_countdown (
        .clock  (clock),
        .zera_as(reset),
        .zera_s (zera_timer),
        .conta  (frame_tick && (db_estado == 4'b0001)),
        .Q      (cd_frame_q),
        .fim    (s_fim_contagem),
        .meio   ()
    );

    assign fim_contagem = s_fim_contagem;

    // Countdown em segundos (3,2,1,0) para exibicao no Python.
    // u_countdown conta de 0 a 179 (M=180); fim dispara em Q=179.
    // Mostra 3 nos frames 0-59, 2 nos frames 60-119, 1 nos frames 120-178,
    // e 0 no frame 179 (ultimo frame antes de transitar para PLAY).
    reg [7:0] cd_sec_reg;
    always @* begin
        if      (cd_frame_q < 8'd60)  cd_sec_reg = 8'd3;
        else if (cd_frame_q < 8'd120) cd_sec_reg = 8'd2;
        else if (cd_frame_q < 8'd179) cd_sec_reg = 8'd1;
        else                          cd_sec_reg = 8'd0;
    end
    assign countdown_sec = cd_sec_reg;

    // ----------------------------------------------------------------
    // ROMs dos charts (uma por musica)
    //   bit[i] = 1 → spawnar nota na trilha i neste frame (i = 0..3)
    //   Profundidades declaradas como potencia de 2 para que o Quartus
    //   infira block RAM sem mismatch de tamanho no MIF.
    //   Os enderecos alem do chart real contem 0 (nenhum spawn).
    // ----------------------------------------------------------------
    localparam integer ROM_DEPTH_0 = 4096;   // condado_curta:   3831 frames, padded to 2^12
    localparam integer ROM_DEPTH_1 = 8192;   // ocarina_curta:   4219 frames, padded to 2^13
    localparam integer ROM_DEPTH_2 = 4096;   // power_rangers:   3880 frames, padded to 2^12
    localparam integer ROM_DEPTH_3 = 4096;   // pokemon:         3718 frames, padded to 2^12

    reg [3:0] chart_rom0 [0:ROM_DEPTH_0-1];
    reg [3:0] chart_rom1 [0:ROM_DEPTH_1-1];
    reg [3:0] chart_rom2 [0:ROM_DEPTH_2-1];
    reg [3:0] chart_rom3 [0:ROM_DEPTH_3-1];

    // Caminho relativo ao diretorio de execucao: o Quartus roda em
    // quartus/de0_cv/ (o .qsf define QUARTUS); Icarus e yosys, na raiz do repo.
`ifdef QUARTUS
    initial $readmemh("../../rtl/condado_curta.hex",  chart_rom0);
    initial $readmemh("../../rtl/ocarina_curta.hex",  chart_rom1);
    initial $readmemh("../../rtl/power_rangers.hex",  chart_rom2);
    initial $readmemh("../../rtl/pokemon.hex",        chart_rom3);
`else
    initial $readmemh("rtl/condado_curta.hex",  chart_rom0);
    initial $readmemh("rtl/ocarina_curta.hex",  chart_rom1);
    initial $readmemh("rtl/power_rangers.hex",  chart_rom2);
    initial $readmemh("rtl/pokemon.hex",        chart_rom3);
`endif

    // Profundidade real de cada chart (para fim_tempo e teto do frame_counter)
    wire [13:0] current_rom_depth;
    assign current_rom_depth = (song_id_reg == 2'd1) ? 14'd4219 :
                               (song_id_reg == 2'd2) ? 14'd3880 :
                               (song_id_reg == 2'd3) ? 14'd3718 :
                                                       14'd3831;

    // ----------------------------------------------------------------
    // Contador de frames de jogo (indexa a ROM)
    //   Incrementa a cada frame_tick enquanto game_active = 1.
    //   Reseta junto com zera_timer (ao voltar para IDLE/WIN/LOSE).
    //   Para em current_rom_depth-1 para evitar acesso fora da ROM.
    // ----------------------------------------------------------------
    reg [13:0] frame_counter;   // 14 bits: alcanca ate 16383

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            frame_counter <= 14'd0;
        end else if (zera_timer) begin
            frame_counter <= 14'd0;
        end else if (frame_tick && game_active && frame_counter < current_rom_depth - 1) begin
            frame_counter <= frame_counter + 14'd1;
        end
    end

    // fim_tempo: dispara quando o ultimo frame do chart e atingido
    assign fim_tempo = (frame_counter == current_rom_depth - 1) && frame_tick && game_active;

    // ----------------------------------------------------------------
    // Note tracks (uma por trilha)
    // ----------------------------------------------------------------
    wire [3:0] spawn_en;
    wire [3:0] hit_pulse;
    wire [3:0] escape_pulse;
    wire [3:0] bad_press;
    wire [7:0] hit_age [0:3];   // idade da nota acertada, por trilha

    note_track u_track0 (
        .clock(clock), .reset(reset | limpaR),
        .frame_tick(frame_tick), .game_active(game_active),
        .spawn_en(spawn_en[0]), .btn_press(btn_pulse[0]),
        .note0(t0n0), .note1(t0n1), .note2(t0n2),
        .hit_pulse(hit_pulse[0]), .escape_pulse(escape_pulse[0]), .bad_press(bad_press[0]),
        .hit_age(hit_age[0])
    );

    note_track u_track1 (
        .clock(clock), .reset(reset | limpaR),
        .frame_tick(frame_tick), .game_active(game_active),
        .spawn_en(spawn_en[1]), .btn_press(btn_pulse[1]),
        .note0(t1n0), .note1(t1n1), .note2(t1n2),
        .hit_pulse(hit_pulse[1]), .escape_pulse(escape_pulse[1]), .bad_press(bad_press[1]),
        .hit_age(hit_age[1])
    );

    note_track u_track2 (
        .clock(clock), .reset(reset | limpaR),
        .frame_tick(frame_tick), .game_active(game_active),
        .spawn_en(spawn_en[2]), .btn_press(btn_pulse[2]),
        .note0(t2n0), .note1(t2n1), .note2(t2n2),
        .hit_pulse(hit_pulse[2]), .escape_pulse(escape_pulse[2]), .bad_press(bad_press[2]),
        .hit_age(hit_age[2])
    );

    note_track u_track3 (
        .clock(clock), .reset(reset | limpaR),
        .frame_tick(frame_tick), .game_active(game_active),
        .spawn_en(spawn_en[3]), .btn_press(btn_pulse[3]),
        .note0(t3n0), .note1(t3n1), .note2(t3n2),
        .hit_pulse(hit_pulse[3]), .escape_pulse(escape_pulse[3]), .bad_press(bad_press[3]),
        .hit_age(hit_age[3])
    );

    // Slot livre por trilha: spawn permitido se qualquer dos 3 slots estiver vazio
    wire [3:0] track_has_slot;
    assign track_has_slot[0] = (t0n0 == 8'hFF) || (t0n1 == 8'hFF) || (t0n2 == 8'hFF);
    assign track_has_slot[1] = (t1n0 == 8'hFF) || (t1n1 == 8'hFF) || (t1n2 == 8'hFF);
    assign track_has_slot[2] = (t2n0 == 8'hFF) || (t2n1 == 8'hFF) || (t2n2 == 8'hFF);
    assign track_has_slot[3] = (t3n0 == 8'hFF) || (t3n1 == 8'hFF) || (t3n2 == 8'hFF);

    // Mux de ROM: leitura sincrona para que o Quartus infira ROM (sem porta de escrita).
    // Como frame_tick ocorre apenas 1x a cada 833333 ciclos, o registrador
    // converge para ROM[frame_counter] muitos ciclos antes do proximo tick.
    reg [3:0] chart_spawn_r;
    always @(posedge clock) begin
        case (song_id_reg)
            2'd1:    chart_spawn_r <= chart_rom1[frame_counter];
            2'd2:    chart_spawn_r <= chart_rom2[frame_counter];
            2'd3:    chart_spawn_r <= chart_rom3[frame_counter];
            default: chart_spawn_r <= chart_rom0[frame_counter];
        endcase
    end

    // Spawn determinístico: lê a ROM no frame atual e filtra por slot disponível
    wire [3:0] chart_spawn = (game_active && frame_tick) ? chart_spawn_r : 4'b0000;
    assign spawn_en = chart_spawn & track_has_slot;

    // Acertos/escapes das 4 trilhas
    wire any_hit    = |hit_pulse;
    wire any_escape = |escape_pulse;
    wire any_bad    = |bad_press;

    // ----------------------------------------------------------------
    // Barra de energia e multiplicador x2 (energia.v)
    //   So conta em PLAY; congela em PAUSE/END/IDLE; zera em COUNTDOWN.
    // ----------------------------------------------------------------
    energia u_energia (
        .clock      (clock),
        .reset      (reset),
        .zera       (db_estado == 4'b0001),
        .game_active(game_active),
        .frame_tick (frame_tick),
        .leitura_ok (leitura_ok),
        .dist_cm    (dist_cm),
        .energia    (energia_val),
        .x2_ativo   (x2_ativo),
        .ativacoes  (ativacoes),
        .movimentos (movimentos)
    );

    // ----------------------------------------------------------------
    // Score (16 bits): +100 por acerto + combo; em dobro com x2_ativo
    // ----------------------------------------------------------------
    reg  [15:0] score_reg;
    reg  [7:0]  combo_reg;  // declarado aqui: o score usa antes do bloco do combo
    wire [15:0] pontos_base = 16'd100 + {8'd0, combo_reg};
    wire [15:0] pontos_hit  = x2_ativo ? (pontos_base << 1) : pontos_base;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            score_reg <= 16'd0;
        end else if (limpaR) begin
            score_reg <= 16'd0;
        end else if (any_hit) begin
            score_reg <= score_reg + pontos_hit;
        end
    end

    assign score = score_reg;

    // ----------------------------------------------------------------
    // Misses (8 bits): incrementa apenas quando nota escapa sem ser acertada
    // ----------------------------------------------------------------
    reg [7:0] miss_reg;

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            miss_reg <= 8'd0;
        end else if (limpaR) begin
            miss_reg <= 8'd0;
        end else if (any_escape && game_active) begin
            miss_reg <= miss_reg + 8'd1;
        end
    end

    assign misses = miss_reg;
    assign perdeu = 1'b0;  // sem limite de erros

    // ----------------------------------------------------------------
    // Combo (8 bits): incrementa em acertos, zera em erros
    // ----------------------------------------------------------------
    always @(posedge clock or posedge reset) begin
        if (reset) begin
            combo_reg <= 8'd0;
        end else if (limpaR) begin
            combo_reg <= 8'd0;
        end else if (any_hit) begin
            combo_reg <= (combo_reg < 8'd255) ? combo_reg + 8'd1 : 8'd255;
        end else if (any_bad || any_escape) begin
            combo_reg <= 8'd0;
        end
    end

    // ----------------------------------------------------------------
    // Metricas da sessao: zera em COUNTDOWN (db_estado == 1)
    // ----------------------------------------------------------------
    metricas u_metricas (
        .clock       (clock),
        .reset       (reset),
        .zera        (db_estado == 4'b0001),
        .hit_pulse   (hit_pulse),
        .hit_age     ({hit_age[3], hit_age[2], hit_age[1], hit_age[0]}),
        .escape_pulse(escape_pulse),
        .combo       (combo_reg),
        .n_acertos   (met_n),
        .soma_e      (met_soma_e),
        .soma_abs    (met_soma_abs),
        .soma_e2     (met_soma_e2),
        .notas       (met_notas),
        .acertos     (met_acertos),
        .max_combo   (met_max_combo)
    );

    // ----------------------------------------------------------------
    // Metronomo (servo): periodo/fase/troca de andamento por musica
    // (bpm_params.vh, gerado por pc/gerar_hex.py). reinicia em nivel em
    // IDLE, SELECT e COUNTDOWN; o tempo anda so em PLAY (game_active).
    // ----------------------------------------------------------------
    reg [31:0] met_periodo, met_fase, met_periodo2, met_ntroca;
    always @* begin
        case (song_id_reg)
            2'd1:    begin met_periodo = `PERIODO_1; met_fase = `FASE_1; met_periodo2 = `PERIODO2_1; met_ntroca = `NTROCA_1; end
            2'd2:    begin met_periodo = `PERIODO_2; met_fase = `FASE_2; met_periodo2 = `PERIODO2_2; met_ntroca = `NTROCA_2; end
            2'd3:    begin met_periodo = `PERIODO_3; met_fase = `FASE_3; met_periodo2 = `PERIODO2_3; met_ntroca = `NTROCA_3; end
            default: begin met_periodo = `PERIODO_0; met_fase = `FASE_0; met_periodo2 = `PERIODO2_0; met_ntroca = `NTROCA_0; end
        endcase
    end

    wire met_reinicia = (db_estado == 4'b0000) || (db_estado == 4'b0110) || (db_estado == 4'b0001);

    metronomo u_metronomo (
        .clock(clock), .reset(reset),
        .rodando(game_active), .reinicia(met_reinicia),
        .modo(modo_met), .nivel_in(2'd0),
        .periodo(met_periodo), .fase(met_fase),
        .periodo2(met_periodo2), .ntroca(met_ntroca),
        .tempo_pulso(), .lado(met_lado), .nivel(met_nivel), .servo(servo_pwm)
    );

    assign combo             = combo_reg;
    assign frame_tick_out    = frame_tick;
    assign frame_counter_out = frame_counter;
    assign song_id_out       = song_id_reg;

    // ----------------------------------------------------------------
    // Registrador de jogada para debug (HEX1 no display)
    // ----------------------------------------------------------------
    registrador_4 RegBotoes (
        .clock (clock),
        .clear (limpaR),
        .enable(registraR),
        .D     (botoes_trilha),
        .Q     (s_jogada)
    );

endmodule
