/*-----------------------------------------------------------------------
 * Testbench : tb_note_track.v
 * Modulo    : note_track
 *-----------------------------------------------------------------------
 * Testa todos os cenarios de jogo de uma trilha:
 *
 *   1. Slots vazios apos reset
 *   2. Spawn e incremento de age
 *   3. Bad press fora da janela (age=10)
 *   4. Acerto na borda inferior (age=119)
 *   5. Acerto no centro (age=130)
 *   6. Acerto na borda superior (age=141)
 *   7. Nota escapa (age>=151) => escape_pulse
 *   8. Multiplas notas em 3 slots
 *   9. 4o spawn ignorado (todos slots cheios)
 *  10. game_active=0 congela tudo
 *
 * Timing das saidas registradas:
 *   - Pulso (hit/bad/escape) e visivel na saida 1 ciclo APOS o evento.
 *   - Apos a task 'press' retornar (sem wait extra), os pulsos ainda
 *     estao validos (serao limpos na proxima posedge).
 *-----------------------------------------------------------------------
 */
`timescale 1ns/1ps

module tb_note_track;

    localparam CLK_PERIOD = 10;

    reg  clock       = 0;
    reg  reset       = 1;
    reg  frame_tick  = 0;
    reg  game_active = 0;
    reg  spawn_en    = 0;
    reg  btn_press   = 0;

    wire [7:0] note0, note1, note2;
    wire hit_pulse, escape_pulse, bad_press;

    note_track dut (
        .clock       (clock),
        .reset       (reset),
        .frame_tick  (frame_tick),
        .game_active (game_active),
        .spawn_en    (spawn_en),
        .btn_press   (btn_press),
        .note0       (note0),
        .note1       (note1),
        .note2       (note2),
        .hit_pulse   (hit_pulse),
        .escape_pulse(escape_pulse),
        .bad_press   (bad_press)
    );

    always #(CLK_PERIOD/2) clock = ~clock;

    // ── Tasks ──────────────────────────────────────────────────────────────────

    // Gera 1 frame_tick. Retorna apos o posedge onde o DUT processou o tick.
    task tick;
        begin
            @(negedge clock); // garante que estamos entre posedges
            frame_tick = 1;
            @(posedge clock); #1; // DUT processa; saidas validas aqui
            frame_tick = 0;
        end
    endtask

    task ticks;
        input integer n;
        integer k;
        begin
            for (k = 0; k < n; k = k+1) tick;
        end
    endtask

    // Spawn: envia spawn_en junto com frame_tick.
    // Retorna apos o posedge; nota esta no slot com age=0.
    task spawn;
        begin
            @(negedge clock);
            spawn_en   = 1;
            frame_tick = 1;
            @(posedge clock); #1;
            spawn_en   = 0;
            frame_tick = 0;
        end
    endtask

    // Pressiona botao por 1 ciclo.
    // Retorna imediatamente apos o posedge onde DUT processa btn_press.
    // hit_pulse / bad_press estao VALIDOS apos esta task retornar.
    // Eles serao limpos na proxima posedge (chame @(posedge clock) para limpar).
    task press;
        begin
            @(negedge clock);
            btn_press = 1;
            @(posedge clock); #1; // DUT processa; pulsos validos aqui
            btn_press = 0;
            // Nao aguarda mais um posedge — deixa o chamador checar os pulsos
        end
    endtask

    // ── Verificacao ────────────────────────────────────────────────────────────

    integer erros = 0;
    integer i;
    reg [7:0] age0_before, age1_before, age2_before;

    `define CHECK(cond, msg) \
        if (!(cond)) begin $display("[FAIL] %s  (t=%0t)", msg, $time); erros = erros + 1; end \
        else $display("[OK]  %s", msg);

    // ── Teste principal ────────────────────────────────────────────────────────

    initial begin
        $dumpfile("tb_note_track.vcd");
        $dumpvars(0, tb_note_track);

        reset = 1;
        repeat(4) @(posedge clock);
        reset = 0;
        game_active = 1;
        @(posedge clock); #1;

        $display("=== TB note_track ===");

        // ── Teste 1: Slots vazios apos reset ──────────────────────────────────
        `CHECK(note0 === 8'hFF && note1 === 8'hFF && note2 === 8'hFF,
               "Apos reset: todos slots vazios (0xFF)")

        // ── Teste 2: Spawn e movimento ────────────────────────────────────────
        spawn;
        `CHECK(note0 === 8'd0, "Spawn: note0.age = 0")
        tick;
        `CHECK(note0 === 8'd1, "1 tick: note0.age = 1")
        ticks(9);
        `CHECK(note0 === 8'd10, "10 ticks: note0.age = 10")

        // ── Teste 3: Bad press fora da janela (age=10, janela=119-141) ────────
        press;
        `CHECK(bad_press  === 1'b1, "Bad press com age=10")
        `CHECK(hit_pulse  === 1'b0, "Sem hit com age=10")
        @(posedge clock); #1; // pulso limpo
        `CHECK(bad_press  === 1'b0, "bad_press limpo no ciclo seguinte")

        // ── Teste 4: Acerto na borda inferior (age=119) ───────────────────────
        // age=10, precisamos de 109 ticks para chegar em 119
        ticks(109);
        `CHECK(note0 === 8'd119, "note0.age = 119 (borda inferior)")
        press;
        `CHECK(hit_pulse  === 1'b1, "Acerto na borda inferior (age=119)")
        `CHECK(bad_press  === 1'b0, "Sem bad_press no acerto")
        `CHECK(note0      === 8'hFF, "Nota removida (slot=0xFF)")
        @(posedge clock); #1;
        `CHECK(hit_pulse  === 1'b0, "hit_pulse limpo")

        // ── Teste 5: Acerto no centro (age=130) ───────────────────────────────
        spawn;
        ticks(130);
        `CHECK(note0 === 8'd130, "note0.age = 130 (centro)")
        press;
        `CHECK(hit_pulse === 1'b1, "Acerto no centro (age=130)")
        `CHECK(note0     === 8'hFF, "Nota removida no centro")
        @(posedge clock); #1;

        // ── Teste 6: Acerto na borda superior (age=141) ───────────────────────
        spawn;
        ticks(141);
        `CHECK(note0 === 8'd141, "note0.age = 141 (borda superior)")
        press;
        `CHECK(hit_pulse === 1'b1, "Acerto na borda superior (age=141)")
        `CHECK(note0     === 8'hFF, "Nota removida na borda superior")
        @(posedge clock); #1;

        // ── Teste 7: Nota escapa (age>=151) => escape_pulse ───────────────────
        // Spawn, depois 152 ticks: age vai de 0 a 150, no 152o frame age=151>=MISS_AGE
        // Nota: no frame do spawn age=0. Cada tick incrementa.
        // Tick 1: age=1, ... Tick 151: age=151 >= 151 => escape no tick 152
        // (age vai ate 150 no tick 150, no tick 151 age=150 ainda <151,
        //  no tick 152 age=151 >=151 => escape)
        // Na verdade: tick 151 age=150->151? Vamos usar 152 ticks para garantir.
        spawn;
        ticks(152);
        `CHECK(escape_pulse === 1'b1, "Nota escapou (age>=151): escape_pulse=1")
        `CHECK(note0        === 8'hFF, "Slot liberado apos escape")
        @(posedge clock); #1;
        `CHECK(escape_pulse === 1'b0, "escape_pulse limpo")

        // ── Teste 8: 3 notas simultaneas ─────────────────────────────────────
        spawn;
        spawn;
        spawn;
        `CHECK(note0 !== 8'hFF, "Slot 0 ocupado")
        `CHECK(note1 !== 8'hFF, "Slot 1 ocupado")
        `CHECK(note2 !== 8'hFF, "Slot 2 ocupado")

        // ── Teste 9: 4o spawn ignorado (todos slots cheios) ───────────────────
        age0_before = note0;
        age1_before = note1;
        age2_before = note2;
        spawn; // deve ser ignorado
        // Apos spawn+tick: slots incrementados, mas nenhum novo slot criado
        `CHECK(note0 === age0_before + 1,
               "4o spawn: slot0 so incrementou (nao foi resetado)")
        `CHECK(note1 === age1_before + 1,
               "4o spawn: slot1 so incrementou")
        `CHECK(note2 === age2_before + 1,
               "4o spawn: slot2 so incrementou")

        // ── Teste 10: game_active=0 congela notas e spawn ─────────────────────
        reset = 1;
        @(posedge clock); #1;
        reset      = 0;
        game_active = 0;
        @(posedge clock); #1;
        spawn; // deve ser ignorado (game_active=0)
        `CHECK(note0 === 8'hFF, "game_active=0: spawn ignorado")
        // btn_press tambem deve ser ignorado
        press;
        `CHECK(hit_pulse  === 1'b0, "game_active=0: btn_press ignorado (sem hit)")
        `CHECK(bad_press  === 1'b0, "game_active=0: btn_press ignorado (sem bad)")
        @(posedge clock); #1;

        // ── Resultado ─────────────────────────────────────────────────────────
        $display("---");
        if (erros == 0)
            $display("=== PASSOU: %0d erros ===", erros);
        else
            $fatal(1, "=== FALHOU: %0d erros ===", erros);

        $finish;
    end

    initial begin
        #1000000;
        $display("[TIMEOUT] Simulacao excedeu limite");
        $finish;
    end

endmodule
