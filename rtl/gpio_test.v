/*-----------------------------------------------------------------------
 * Arquivo   : gpio_test.v
 * Descricao : Modulo de teste para identificar em quais pinos GPIO
 *             os botoes estao fisicamente conectados.
 *             Mapeia os 10 pinos inferiores do GPIO_0 (JP1) para LEDR[0:9].
 *
 * Uso: Compilar com TOP_LEVEL_ENTITY = gpio_test.
 *      Pressionar cada botao e ver qual LED acende.
 *-----------------------------------------------------------------------
 */
module gpio_test (
    input  wire       clock,
    input  wire [9:0] gpio_pins,  // 10 pinos inferiores do GPIO_0
    output wire [9:0] leds
);
    assign leds = gpio_pins;
endmodule
