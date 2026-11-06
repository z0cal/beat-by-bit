/*-----------------------------------------------------------------------
 * Arquivo   : cfg_jogo.v
 * Projeto   : Beat by Bit - Semestre 3
 *-----------------------------------------------------------------------
 * Descricao : Registradores da pagina "Jogo" da tela de configuracoes
 *             (mesmo padrao do eq_config.v). Habilitado so em SETTINGS;
 *             zera so no reset (os valores sobrevivem a SETTINGS -> IDLE).
 *
 *   pagina   : 0 = atenuador (eq_config), 1 = jogo. O pulso `confirma`
 *              (gesto da mao) alterna a pagina.
 *   modo_met : modo do metronomo (0 desligado, 1 fixo). Na pagina 1,
 *              pulso[0] alterna 0 <-> 1 (o 2, adaptativo, entra na S4).
 *   sensorial: pagina 1, pulso[1] liga/desliga o perfil sensorial.
 *   sem_som  : pagina 1, pulso[2] liga/desliga o modo sem som.
 *   pulso[3] fica reservado. Na pagina 0 os botoes nao fazem nada aqui.
 *-----------------------------------------------------------------------
 */

module cfg_jogo (
    input  wire       clock,
    input  wire       reset,
    input  wire       habilita,    // ativo so no estado SETTINGS
    input  wire       confirma,    // gesto_detector: troca de pagina
    input  wire [3:0] pulso,       // btn_pulse das trilhas
    output reg        pagina,
    output reg  [1:0] modo_met,
    output reg        sensorial,
    output reg        sem_som
);

    always @(posedge clock or posedge reset) begin
        if (reset) begin
            pagina    <= 1'b0;
            modo_met  <= 2'd0;
            sensorial <= 1'b0;
            sem_som   <= 1'b0;
        end else if (habilita) begin
            if (confirma) pagina <= ~pagina;
            if (pagina) begin
                if (pulso[0]) modo_met  <= {1'b0, ~modo_met[0]};
                if (pulso[1]) sensorial <= ~sensorial;
                if (pulso[2]) sem_som   <= ~sem_som;
            end
        end
    end

endmodule
