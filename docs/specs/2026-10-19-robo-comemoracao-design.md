# Robô de comemoração — Design

Preparar as OS, definir recursos e prioridades, programar os reparos, informar as equipes, solucionar impedimentos, revisar OS e liberar recursos dos reparos concluídos.
Preparar o veículo, os equipamentos e os materiais designados; percorrer o itinerário e executar os reparos programados.
Receber a programação e registrar a conclusão de cada reparo, sua data, duração e recursos utilizados, ou os motivos que impediram sua execução.
Definir as necessidades de informação gerencial, solicitar relatórios e analisar seus resultados.

Status: aguardando aprovação.

## 1. Intenção

Um mini robô que fica ao lado do jogo e comemora quando a música acaba e a
pessoa ganha. É um detalhe de carisma para a demo do Beat by Bit 2.0: o robô
tem um rosto animado numa tela pequena e, na vitória, dança girando sobre a
base. Ele não interfere no jogo.

O caminho escolhido é o mais simples. Não importa para a avaliação que o sinal
saia da FPGA, então quem avisa o robô é o PC, que já recebe da FPGA o estado
do jogo.

## 2. Objetivos

- Na transição para vitória (`STATE_WIN = 4`), o robô comemora: o rosto fica
  feliz e o corpo gira de um lado para o outro por alguns segundos, depois
  volta ao normal.
- Fora da comemoração, o rosto fica "vivo": pisca e olha em volta.
- Robô sem fio e com bateria recarregável por USB.
- Funciona tanto com a FPGA (`--port`) quanto no modo `--sim`, para testar sem
  a placa.

## 3. Não objetivos

- Reagir a outros eventos (acerto, erro, combo, derrota, pausa).
- Receber placar, nome da música ou qualquer dado além de "ganhou".
- Controle pela FPGA ou por Verilog. O servo não usa o `circuito_pwm` da
  disciplina; o PWM sai do microcontrolador do robô.
- Mais de um servo ou outros movimentos.

## 4. Anti-objetivos (falha, mesmo que "funcione")

- O jogo travar, perder FPS ou atrasar o áudio por causa do robô. O envio é
  não bloqueante e o PC nunca espera resposta.
- O jogo depender do robô: com o robô desligado ou fora da rede, tudo segue
  igual, sem erro na tela e sem pausa.
- Precisar de um passo de configuração no dia da demo além de ligar o robô.
- O tranco do servo reiniciar o microcontrolador bem na hora da comemoração.

## 5. Restrições

- **Servo:** o da disciplina (PCS3645, roteiro "Controle de um Servomotor").
  Alimentação de 5 V, sinal PWM de 50 Hz (período de 20 ms), pulso de 1 a 2 ms,
  fios marrom (−), vermelho (+) e laranja (sinal). O modelo não está no roteiro:
  pelo conector parece um micro servo tipo SG90, a confirmar na bancada.
- **Rede:** sem internet. Segundo o spec do 2.0, o PC usa o hotspot do celular;
  o robô entra na mesma rede.
- **Corpo:** impresso em 3D (há acesso a impressora).
- **PC:** `pc/interface.py`, laço principal em ``, com o estado
  atual em `self.fpga_state` nos dois modos (serial e `--sim`).

## 6. Decidido com o usuário

- Sem fio, com bateria.
- Comemora só quando a pessoa ganha; o movimento é simples, com um servo.
- O servo é o disponibilizado na disciplina.
- O rosto é dinâmico, numa tela OLED pequena.
- O PC dispara a comemoração (caminho mais simples).
- Corpo impresso em 3D.
- Movimento: o robô inteiro gira sobre a base, de um lado para o outro, como
  uma dancinha.
- Nenhuma peça foi comprada ainda: tudo da lista de §7.1 entra na compra.
- O servo da disciplina pode ficar montado no robô durante o desenvolvimento e
  a demo, mas **será devolvido no fim**. Ele precisa sair inteiro: preso por
  parafusos (sem cola), sem cortar nem emendar o cabo e o conector originais.

## 7. Escolhas do Claude (podem ser revistas)

### 7.0 Ordem e prazo

- O prazo não foi informado. Suposição: a demo segue o cronograma do spec do
  2.0 (12 semanas a partir de 2026-10-01, ou seja, início de janeiro de 2027).
- A compra é a primeira tarefa. Lojas físicas de eletrônica entregam no mesmo
  dia; importação leva semanas. Enquanto as peças não chegam, dá para adiantar
  o lado do PC (§7.4) e o modelo 3D com medidas de datasheet.
- O primeiro teste com as peças é a rede (§9): ESP32-C3 no hotspot do celular
  da demo.

### 7.1 Peças

| Item | Qtd | Observação |
|---|---|---|
| ESP32-C3 SuperMini | 2 | Wi-Fi, pequeno, PWM e I2C. Alimentado pelo pino 5V. O segundo é reserva e serve de ponte no plano B de rede (§9). |
| OLED SSD1306 0,96" 128×64, I2C | 1 | 4 fios. Alternativa maior: SH1106 1,3". |
| Servo da disciplina | 1 | Ver §5. Emprestado: devolver no fim. |
| Bateria 18650 + suporte | 1 | |
| Módulo TP4056 **com proteção** (USB-C) | 1 | Carga pela USB. |
| Elevador MT3608 (ajustado para 5,0–5,1 V) | 1 | Sem desligamento automático em carga baixa, ao contrário de power banks e de alguns módulos IP5306. |
| Chave liga/desliga | 1 | Entre a bateria e o elevador. |
| Capacitor eletrolítico 470–1000 µF, ≥10 V | 1 | Nos 5 V, perto do servo. |
| Botão táctil | 1 | Comemoração manual (plano B). |
| Fios, conector fêmea de 3 pinos (para o servo), parafusos M2/M3 | — | O cabo do servo encaixa num conector; nada é soldado nele. |

Consumo: o SG90 pode puxar ~0,7 A travado; o MT3608 aguenta com folga. Se o
servo for um MG90S, o pico é maior: conferir se o elevador e a bateria
seguram o tranco.

### 7.2 Ligações

```
18650 ─ TP4056(prot.) ─ chave ─ MT3608 ─ 5V ─┬─ servo (+)
                                             ├─ ESP32-C3 (5V)
                                             └─ capacitor
ESP32-C3 3V3 ─ OLED VCC        GND comum a tudo
GPIO4  ─ servo sinal (3,3 V; servos de hobby aceitam)
GPIO6  ─ OLED SDA
GPIO7  ─ OLED SCL
GPIO10 ─ botão ─ GND (pull-up interno)
```

Pinos escolhidos para evitar os de strapping do C3 (GPIO2, 8 e 9).

### 7.3 Firmware do robô

- Arduino (PlatformIO ou arduino-cli, a critério do construtor), com as
  bibliotecas ESP32Servo, Adafruit SSD1306/GFX e FluxGarage RoboEyes.
- SSID e senha do hotspot num `secrets.h` fora do git, com um
  `secrets.example.h` versionado.
- Escuta UDP na porta **4210**. Ao receber `WIN`, comemora; ao receber `PING`,
  responde `PONG` para quem enviou (descoberta, §7.4).
- Comemoração (~3 s): olhos em humor feliz e servo alternando entre 60° e 120°
  (centro em 90°) a cada ~250 ms; no fim, volta ao centro e ao humor normal.
  Usa pulso de 1 a 2 ms, como no roteiro.
- Pedidos que chegam durante uma comemoração são ignorados.
- Repouso: RoboEyes com piscar automático e olhar ocioso.
- Sem rede: os olhos ficam "cansados" e um ícone pequeno indica a falta de
  Wi-Fi, para se ver na hora da demo. Ao conectar, mostra o IP por ~3 s.
- O botão dispara a mesma comemoração.
- Servo parado em 90° ao ligar, para o corpo não dar um tranco.

### 7.4 Lado do PC

- Novo `pc/robo.py`, pequeno, com tudo numa thread daemon. Nada roda no laço
  de 60 fps além de pôr um pedido numa fila.
- **Descoberta sem configuração:** ao iniciar, e a cada ~5 s enquanto o robô
  não for achado, envia `PING` por broadcast (`255.255.255.255` e o broadcast
  da sub-rede). Se ninguém responder em ~1 s, varre a sub-rede /24 do PC com
  `PING` unicast (254 pacotes). O IP de quem responder `PONG` fica guardado. Se
  o robô parar de responder, volta a procurar.
- `avisar_vitoria()`: põe o pedido na fila; a thread envia `WIN` três vezes,
  com ~50 ms de intervalo, para o IP guardado e também por broadcast. Erros de
  rede são engolidos (no máximo um `print`).
- Em `Renderer.run()`, chamar `avisar_vitoria()` só na **transição** de
  qualquer estado para `STATE_WIN` (guardar o estado anterior), nos dois modos.
- `python pc/robo.py` sozinho acha o robô e envia um `WIN`, para testar sem o
  jogo.
- Integração no `interface.py` atual. Quando o renderizador do 2.0 chegar, o
  gancho continua sendo a transição para vitória. No perfil Calmo, que não tem
  derrota, o robô comemora toda música terminada. Se o 2.0 ganhar a tela de
  verificação ao iniciar, o robô aparece nela como item opcional (achado ou
  não), sem bloquear o jogo.

### 7.5 Corpo (impressão 3D)

- OpenSCAD paramétrico, para ajustar as medidas reais das peças.
- **Base:** caixa com a 18650, o TP4056 (USB acessível por fora), o MT3608, a
  chave e o servo fixo com o eixo para cima. Botão acessível.
- **Corpo e cabeça:** uma peça presa ao braço (horn) do servo, com janela
  retangular para o OLED na "cara" e o ESP32 dentro.
- Os fios do corpo para a base passam pelo centro, perto do eixo, com folga
  para ±30° de giro.
- Tamanho aproximado: 6–8 cm de largura e 10–12 cm de altura.
- Base pesada ou larga o suficiente para não tombar com o giro.
- O servo é preso pelas abas, com os parafusos dele, num encaixe da base, e o
  corpo é parafusado no horn: tudo sai sem ferramenta especial e sem cola,
  para devolver o servo.

## 8. Fica a critério do construtor

- Organização dos arquivos (sugestão: `robo/firmware/` e `robo/case/` no repo).
- Detalhes da animação e do ritmo exato do giro, dentro do descrito em §7.3.
- Forma estética do robô.
- Ferramenta de build do firmware.

## 9. Riscos

- **Hotspot do celular em 5 GHz ou só WPA3.** O ESP32-C3 só usa 2,4 GHz.
  Ainda não se sabe qual celular fará o hotspot. Primeiro teste com as peças:
  deixar o hotspot em 2,4 GHz (a maioria dos Android permite) e conectar o
  robô.
- **Hotspot bloquear broadcast.** A varredura unicast da sub-rede (§7.4)
  cobre isso sem configuração.
- **Hotspot isolar os clientes** (nem unicast passa) ou não aceitar 2,4 GHz.
  Plano B: o segundo ESP32-C3 fica no USB do PC como ponte serial e fala com o
  robô por ESP-NOW, sem depender do hotspot. Muda só o transporte em
  `pc/robo.py` (serial no lugar de UDP); o firmware ganha um modo ESP-NOW.
- **Tranco do servo derrubar o ESP32.** O capacitor e os 5 V do elevador
  mitigam; testar com a bateria já meio descarregada.
- **Modelo do servo** diferente do esperado (consumo, tamanho do horn e da
  furação na base): medir antes de modelar a base.
