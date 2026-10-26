# Beat by Bit

Jogo de ritmo estilo Guitar Hero para a FPGA DE0-CV (Laboratório Digital).
A FPGA roda a lógica do jogo e envia o estado por UART; o PC desenha a tela e
toca o áudio.

## Estrutura

```
rtl/             Verilog do jogo e charts .hex ($readmemh)
tb/              testbenches (fora da síntese)
constraints/     .sdc
scripts/         check_timing.tcl
quartus/de0_cv/  .qpf/.qsf: pinagem e assignments
pc/              interface.py (renderizador Pygame + modo --sim), geradores de chart, assets
songs/           MIDI (fonte do chart) + WAV (áudio tocado) de cada música
docs/            documentação LaTeX, CONTEXT.md e specs do Beat by Bit 2.0
```

Projeto no layout do `fpga-new`: a lista de fontes do `.qsf` é regenerada a
partir de `rtl/` por `make sync`; pinos e demais assignments ficam no `.qsf`.

## Hardware

```
make check-all            # todas as testbenches de tb/
make check TB=tb_note_track
make waves                # VCD mais recente no GTKWave
make yosys                # valida a síntese sem o Quartus
make figura-fsm MOD=unidade_controle
make                      # Quartus: síntese incremental até o .sof
make timing / program / gui / qar
make help                 # lista completa
```

Os `.hex` ficam em `rtl/`. O `$readmemh` de `fluxo_dados.v` usa `rtl/...` (Icarus
e yosys rodam na raiz) ou `../../rtl/...` quando a macro `QUARTUS` está definida,
o que o `.qsf` faz (`VERILOG_MACRO "QUARTUS=1"`), porque o Quartus roda em
`quartus/de0_cv/`.

## PC

```bash
# pygame, mido; pyserial só para o modo FPGA
python pc/interface.py --sim                 # sem FPGA
python pc/interface.py --port /dev/ttyUSB0   # com FPGA
python pc/gerar_hex.py                       # regera rtl/condado_curta.hex
```

Detalhes de arquitetura, protocolo UART e músicas: [`docs/CONTEXT.md`](docs/CONTEXT.md).
