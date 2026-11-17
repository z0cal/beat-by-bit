# Robô do Beat by Bit (ESP32 + ST7789V)

O robô fica **feliz** quando o jogador acerta, **triste** quando erra e **comemora** no
fim da música, qualquer que seja o resultado. A FPGA não muda: o PC já recebe tudo isso
no pacote que lê, e só manda um byte para a ESP32 por USB.

| Byte | Quando | Origem no pacote |
|---|---|---|
| `H` | acerto | `metrics.n_acertos` aumentou, em PLAY |
| `S` | erro | `misses` aumentou (nota perdida), em PLAY |
| `C` | fim da música | estado passou a WIN ou LOSE |
| `N` | volta ao neutro | saiu da tela do nome |

## Ligação (tela 2,4" ST7789V, 240x320)

| Tela | ESP32 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| CS | GPIO5 |
| RESET | GPIO17 |
| DC / RS | GPIO16 |
| SDI / MOSI | GPIO23 |
| SCK | GPIO18 |
| LED / BL | 3V3 |
| SDO / MISO | não ligar |

Confirme que o módulo é mesmo ST7789V: muitos de 2,4" são ILI9341, e aí o `-DST7789_DRIVER`
do `platformio.ini` precisa ser trocado por `-DILI9341_DRIVER`.

## ESP32 (PlatformIO)

```
pio run -t upload
pio device monitor      # digite H, S, C ou N para testar sem o PC; ? responde "BBB-ROBO OK"
```

Se as cores saírem invertidas ou trocadas, ative uma das linhas comentadas no
`platformio.ini` (`-DTFT_INVERSION_ON=1` ou `-DTFT_RGB_ORDER=TFT_BGR`). Imagem de
cabeça para baixo: `-DROTATION=3`.

## PC

```
pip install pyserial
python pc/aplicar_robo.py pc/interface.py      # 6 edições; guarda interface.py.bak
python pc/interface.py --port COM3 --robo-port COM7
```

`--port` é a FPGA (CP2102, 921600 baud) e `--robo-port` é a ESP32 (115200 baud). Sem
`--robo-port` o jogo roda como antes; com `--sim` o robô também reage. Se a ESP32 sumir ou a
porta falhar, só o robô desliga, e o jogo segue. Teste do lado do PC: `python pc/test_robo.py`.

## Verificação feita

O firmware foi compilado no PC com um stub do TFT_eSPI (sem avisos) e os quadros foram
desenhados e conferidos (`host/montagem.png`). **Não foi testado na placa nem com o
TFT_eSPI real.**
