`timescale 1ms/100us 

module tb_uc_ganha;

    reg clock, reset, start, jogada, fim_contagem, fim_tempo, perdeu;
    wire registraR, limpaR, zera_timer, conta_timer;
    wire [3:0] db_estado;

    unidade_controle uut (
        .clock(clock),
        .reset(reset),
        .start(start), 
        .jogada(jogada), 
        .fim_contagem(fim_contagem),
        .fim_tempo(fim_tempo),
        .perdeu(perdeu),
        .registraR(registraR), 
        .limpaR(limpaR),     
        .zera_timer(zera_timer),
        .conta_timer(conta_timer),
        .db_estado(db_estado)
    );

    always #10 clock = ~clock;
    initial begin
        $display("Simulando UC com Registro de Jogadas...");
        clock = 0; reset = 1; start = 0; jogada = 0;
        fim_contagem = 0; fim_tempo = 0; perdeu = 0;
        
        #40; reset = 0; #40;

        // Iniciar e ir para PLAY
        start = 1; #20; start = 0; #20; // IDLE -> COUNTDOWN
        #40; 
        fim_contagem = 1; #20; fim_contagem = 0; #40; // -> PLAY

        // Testar registro de jogada no estado PLAY
        $display("Estado PLAY. Simulando acerto de nota...");
        jogada = 1; #20; jogada = 0; #20; 

        // Pausa e retorno
        start = 1; #20; start = 0; #20; #40; // -> PAUSE 
        start = 1; #20; start = 0; #20; #40; // -> PLAY 

        // Simular Vitoria
        fim_tempo = 1; #20; fim_tempo = 0; #40; // -> END_LOSE 
        
        $display("Fim da simulacao da UC.");
        $stop;
    end
endmodule