# Makefile — projeto beat_by_bit (DE0-CV / Cyclone V). Gerado por fpga-new.
# Preservado ao importar ZIPs; atualizacao explicita com --force cria Makefile.bak.
#
# Cadeia unica de verdade:  arquivos em rtl/  ->  make sync  ->  .qsf  ->  make
# A simulacao varre rtl/ e tb/ direto do disco (nao existe files.f para manter).
#
#   make check     iverilog: compila + roda; o TB precisa verificar os resultados
#   make check-all roda todas as testbenches de tb/ e resume quais falharam
#   make yosys     valida sintese Intel ALM (Cyclone V), sem gerar bitstream
#   make sync      re-varre rtl/ e reescreve a lista de fontes do .qsf
#   make           sintese incremental ate o .sof
#   make qar       empacota a entrega da experiencia

PROJECT     := beat_by_bit
TOP         := beat_by_bit
QUARTUS_DIR := quartus/de0_cv
QSF         := $(QUARTUS_DIR)/$(PROJECT).qsf
SDC         := $(firstword $(wildcard constraints/*.sdc) $(QUARTUS_DIR)/$(PROJECT).sdc)

# Testbench: vazio = descoberta automatica em tb/. Fixe com `make check TB=meu_tb`.
TB          ?=

# Os testbenches gravam tb_<nome>.vcd na raiz; `make waves` abre o mais recente.
VCD         ?= $(firstword $(shell ls -t *.vcd 2>/dev/null))
TBS         := $(basename $(notdir $(wildcard tb/*.v tb/*.sv)))

HDL_BIN     ?= $(HOME)/.local/bin
YOSYS       ?= yosys
QUARTUS_SH  ?= quartus_sh
QUARTUS_MAP ?= quartus_map
QUARTUS_FIT ?= quartus_fit
QUARTUS_ASM ?= quartus_asm
QUARTUS_STA ?= quartus_sta
QUARTUS_PGM ?= quartus_pgm
QUARTUS_GUI ?= quartus
JTAGCONFIG  ?= jtagconfig
CABLE       ?= USB-Blaster

MASON_BIN      ?= $(HOME)/.local/share/nvim/mason/bin
VERIBLE_LINT   ?= $(MASON_BIN)/verible-verilog-lint
VERIBLE_FORMAT ?= $(MASON_BIN)/verible-verilog-format

# Entrega: exp<N>_txbyy.qar (x = turma, yy = bancada), em minusculas,
# conforme o roteiro. Ex.: exp5_t1ba5.qar
TURMA   ?= 1
BANCADA ?= A5
TXBYY   := $(shell printf 't%sb%s' '$(TURMA)' '$(BANCADA)' | tr '[:upper:]' '[:lower:]')
QAR     ?= $(PROJECT)_$(TXBYY).qar

# As fontes de sintese saem do proprio .qsf: o make nunca diverge do que o
# Quartus compila. Quem alimenta o .qsf e o `make sync`.
SRC := $(patsubst %,$(QUARTUS_DIR)/%,$(shell sed -n 's/^set_global_assignment -name \(VERILOG\|SYSTEMVERILOG\|VHDL\)_FILE  *//p' $(QSF) 2>/dev/null))
SIM_SRC := $(shell find rtl tb \( -name '*.v' -o -name '*.sv' \) 2>/dev/null)
RTL_SRC := $(shell find rtl \( -name '*.v' -o -name '*.sv' \) 2>/dev/null)

# Figuras auxiliares por yosys/graphviz; nao equivalem aos viewers do Quartus.
# Se o roteiro exigir uma captura do Quartus, use o viewer correspondente.
FIGS ?= docs/figuras
MOD  ?= $(TOP)
# Sem MOD: todas as FSMs na hierarquia do top. SAIDAS=1 anota saidas por estado.
FSM_SRC ?= $(RTL_SRC)
SAIDAS  ?= 0

OUT := $(QUARTUS_DIR)/output_files
MAP := $(OUT)/$(PROJECT).map.rpt
FIT := $(OUT)/$(PROJECT).fit.rpt
ASM := $(OUT)/$(PROJECT).asm.rpt
STA := $(OUT)/$(PROJECT).sta.rpt
SOF := $(OUT)/$(PROJECT).sof

.PHONY: all help check check-all waves sync yosys figura-rtl figura-fsm lint format-check \
        format gui fpga timing jtag program qar pins report clean-sim clean-cache clean-all
.DELETE_ON_ERROR:

all: $(SOF) $(STA)

help:
	@printf '%s\n' \
	  'make check          iverilog: compila + roda (rapido, sem GUI)' \
	  'make check-all      roda todas as testbenches de tb/' \
	  'make yosys          valida sintese Intel ALM (Cyclone V); log/JSON em .yosys/' \
	  'make waves          abre o VCD no GTKWave' \
	  'make sync           re-varre rtl/ e atualiza a lista de fontes do .qsf' \
	  'make figura-rtl MOD=x   esquematico do modulo x (default: o top)' \
	  'make figura-fsm         FSMs do top; MOD=x seleciona UC; SAIDAS=1 anota saidas' \
	  'make lint           lint estatico com Verible' \
	  'make format         formata rtl/ e tb/' \
	  'make                sintese incremental ate o .sof' \
	  'make gui            abre o projeto no Quartus (Pin Planner)' \
	  'make timing         slack em todos os cantos' \
	  'make pins           pinagem efetiva depois do fit' \
	  'make report         resumo de area e timing' \
	  'make jtag           lista o USB-Blaster e a cadeia JTAG' \
	  'make program        grava o .sof na placa' \
	  'make qar            empacota a entrega ($(QAR))' \
	  'make clean-sim      remove artefatos de simulacao' \
	  'make clean-all      remove tudo que e gerado'

# --- simulacao --------------------------------------------------------------

check:
	$(HDL_BIN)/check $(TB)

# Uma testbench por vez (o modulo tem o nome do arquivo); log em .sim/<tb>.log.
check-all:
	@mkdir -p .sim; falhas=""; \
	 for tb in $(TBS); do \
	   if $(HDL_BIN)/check "$$tb" > ".sim/$$tb.log" 2>&1; then \
	     printf '  ok      %s\n' "$$tb"; \
	   else \
	     printf '  FALHOU  %s  (.sim/%s.log)\n' "$$tb" "$$tb"; falhas="$$falhas $$tb"; \
	   fi; \
	 done; \
	 [ -z "$$falhas" ] || { printf 'falharam:%s\n' "$$falhas" >&2; exit 1; }

waves:
	$(HDL_BIN)/waves $(VCD)

# --- projeto ----------------------------------------------------------------

sync:
	$(HDL_BIN)/fpga-sync

# Valida as mesmas fontes do Quartus, rodando de $(QUARTUS_DIR) e com a macro
# QUARTUS do .qsf: os $readmemh resolvem igual. O .sof continua no Quartus.
yosys: $(SRC) $(QSF)
	@mkdir -p .yosys
	@rm -f .yosys/$(TOP).json
	cd $(QUARTUS_DIR) && $(YOSYS) -Q -T -l ../../.yosys/yosys.log -p "read_verilog -sv -DQUARTUS $(patsubst $(QUARTUS_DIR)/%,%,$(SRC)); synth_intel_alm -family cyclonev -top $(TOP); check -assert; write_json ../../.yosys/$(TOP).json; stat"

figura-rtl:
	@mkdir -p $(FIGS)
	@yosys -q -p "read_verilog -sv $(RTL_SRC); hierarchy -top $(MOD); proc; opt_clean; \
	   show -format svg -prefix $(FIGS)/rtl_$(MOD) -viewer none $(MOD)"
	@dot -Tpdf $(FIGS)/rtl_$(MOD).dot -o $(FIGS)/rtl_$(MOD).pdf
	@echo "$(FIGS)/rtl_$(MOD).svg  $(FIGS)/rtl_$(MOD).pdf"

figura-fsm:
	@"$(HDL_BIN)/fsm-diagrama" $(FSM_SRC) --top "$(MOD)" --out "$(FIGS)/fsm_$(MOD)" \
	  --formato svg --formato pdf $(if $(filter 1,$(SAIDAS)),--saidas)

lint:
	$(VERIBLE_LINT) --rules=-no-trailing-spaces,-no-tabs,-line-length $(SIM_SRC)

format-check:
	@for source in $(SIM_SRC); do $(VERIBLE_FORMAT) --verify "$$source" || exit 1; done

format:
	$(VERIBLE_FORMAT) --inplace $(SIM_SRC)

gui:
	@cd $(QUARTUS_DIR) && $(QUARTUS_GUI) $(PROJECT).qpf >/dev/null 2>&1 &

# --- sintese incremental ----------------------------------------------------

$(MAP): $(SRC) $(QSF)
	cd $(QUARTUS_DIR) && $(QUARTUS_MAP) $(PROJECT)

$(FIT): $(MAP) $(SDC)
	cd $(QUARTUS_DIR) && $(QUARTUS_FIT) $(PROJECT)

$(ASM) $(SOF) &: $(FIT)
	cd $(QUARTUS_DIR) && $(QUARTUS_ASM) $(PROJECT)

$(STA): $(FIT) $(SDC)
	cd $(QUARTUS_DIR) && $(QUARTUS_STA) $(PROJECT)

fpga: $(SOF)

timing: $(SOF)
	cd $(QUARTUS_DIR) && $(QUARTUS_STA) -t ../../scripts/check_timing.tcl $(PROJECT)

pins: $(FIT)
	@awk -F: '$$3 ~ /input|output|bidir/ && $$4 ~ /[A-Za-z0-9]/ \
	    { printf "%-16s %-6s %-7s %s\n", $$1, $$2, $$3, $$4 }' $(OUT)/$(PROJECT).pin

report: $(SOF) $(STA)
	@cat $(OUT)/$(PROJECT).fit.summary
	@echo
	@cat $(OUT)/$(PROJECT).sta.summary

# --- placa ------------------------------------------------------------------

jtag:
	$(JTAGCONFIG)

program: $(SOF)
	@$(JTAGCONFIG) | grep -q 'USB-Blaster' || \
	  { printf '%s\n' 'ERRO: USB-Blaster nao encontrado. Ligue a placa e rode make jtag.' >&2; exit 1; }
	$(QUARTUS_PGM) -c "$(CABLE)" -m jtag -o "p;$(SOF)"

# Entrega. O arquivador varre a pasta do projeto, entao o .tcl auxiliar e
# escrito FORA dela para nao entrar no .qar.
qar: $(SRC) $(QSF) $(SDC)
	@t=$$(mktemp /tmp/qar-XXXXXX.tcl); \
	 trap 'rm -f "$$t"' EXIT; \
	 printf 'package require ::quartus::project\nproject_open %s\nproject_archive %s -overwrite\nproject_close\n' \
	     "$(PROJECT)" "$(QAR)" > "$$t"; \
	 cd $(QUARTUS_DIR) && $(QUARTUS_SH) -t "$$t"
	@ls -l $(QUARTUS_DIR)/$(QAR)

# --- limpeza ----------------------------------------------------------------

clean-sim:
	$(HDL_BIN)/cleanhdl

clean-cache:
	rm -rf -- $(QUARTUS_DIR)/db $(QUARTUS_DIR)/incremental_db $(QUARTUS_DIR)/greybox_tmp

clean-all: clean-sim clean-cache
	rm -rf -- $(OUT) $(QUARTUS_DIR)/simulation .yosys
