# Robô de comemoração — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um robô sem fio (ESP32-C3 + OLED + servo da disciplina) que dança quando o `interface.py` detecta vitória.

**Architecture:** O PC roda `pc/robo.py` numa thread daemon: acha o robô por UDP (`PING`/`PONG` na porta 4210) e, na transição para `STATE_WIN`, envia `WIN`. O firmware separa a lógica pura (`logica.h/.cpp`, testada com g++ no PC) do código de hardware (`robo.ino`: Wi-Fi, UDP, servo, olhos RoboEyes). O corpo é OpenSCAD paramétrico.

**Tech Stack:** Python 3 stdlib (`socket`, `threading`) + pytest; Arduino via `arduino-cli` (core `esp32:esp32`), ESP32Servo, Adafruit SSD1306/GFX, FluxGarage RoboEyes; g++ para os testes da lógica; OpenSCAD.

**Spec:** `docs/specs/2026-10-19-robo-comemoracao-design.md`

## Global Constraints

- O repositório não tem git: **nenhum passo faz commit**. O usuário revisa e versiona depois.
- Porta UDP **4210**. Mensagens ASCII exatas: `WIN`, `PING`, `PONG`.
- PC: `WIN` enviado **3 vezes, ~50 ms de intervalo**, para o IP achado **e** por broadcast.
- PC: busca a cada **~5 s** enquanto não achar; espera `PONG` por **~1 s** antes de varrer a /24.
- Nada de rede bloqueia o laço de 60 fps de `Renderer.run()`; erros de rede viram no máximo um `print`.
- Servo: 50 Hz, pulso **1000–2000 µs** (0°→1000, 180°→2000), centro **90°**.
- Comemoração: **3000 ms**, alternando **60° / 120°** a cada **250 ms**, começando em 60°; volta a 90° no fim.
- Pinos ESP32-C3: servo **GPIO4**, OLED SDA **GPIO6**, SCL **GPIO7**, botão **GPIO10** (pull-up interno, ativo em 0). Não usar GPIO2/8/9.
- OLED SSD1306 128×64 I2C (endereço 0x3C).
- Credenciais Wi-Fi em `secrets.h`, fora do git; `secrets.example.h` versionado.
- O servo é emprestado: nenhuma cola, nenhum corte no cabo; preso por parafusos.
- MT3608 ajustado para **5,0–5,1 V** com multímetro **antes** de ligar qualquer carga.

## Review Focus

1. **Robô ausente o jogo inteiro** (desligado, outra rede, PC sem Wi-Fi): o jogo roda igual, `avisar_vitoria()` volta em < 5 ms e nada lança exceção. Testes: `test_avisar_sem_robo_nao_bloqueia`, `test_sem_rede_nao_quebra` (Task 1).
2. **`WIN` duplicado** (3 cópias × unicast + broadcast = até 6 pacotes): o robô dança uma vez só. Teste: `iniciar_ignorado_enquanto_ativa` (Task 3).
3. **Ficar muitos frames em vitória e jogar de novo**: dispara uma vez por vitória, e de novo na próxima vitória. Teste: `test_vitoria_comecou_*` (Task 1).
4. **Robô some e volta** (bateria acabou, reiniciou com outro IP): o PC esquece o IP e volta a procurar. Teste: `test_esquece_robo_que_parou` (Task 1).
5. **Pacotes estranhos no robô** (`WIN\r\n`, `win`, lixo, pacote vazio): fim de linha é tolerado; o resto é ignorado sem resposta. Teste: `interpretar_*` (Task 3).

---

### Task 0: Compras e medidas (usuário)

Ação humana; nenhum código. O resto do plano não depende das peças até a Task 5. As Tasks 1–4 podem andar em paralelo à entrega.

- [ ] **Step 1:** Comprar a lista do spec §7.1 (2× ESP32-C3 SuperMini, OLED SSD1306 0,96" I2C, 18650 + suporte, TP4056 com proteção USB-C, MT3608, chave, capacitor 470–1000 µF ≥10 V, botão táctil, conector fêmea de 3 pinos, fios, parafusos M2/M3).
- [ ] **Step 2:** Na bancada, anotar do servo da disciplina: modelo escrito na etiqueta, dimensões do corpo, distância entre os furos das abas, altura do eixo e o tipo de horn. Essas medidas alimentam a Task 6.
- [ ] **Step 3:** Confirmar no celular da demo que o hotspot pode ficar em **2,4 GHz** com WPA2. Se não puder, parar e voltar ao spec (§9, plano B).

---

### Task 1: `pc/robo.py` (descoberta e aviso)

**Files:**
- Create: `pc/robo.py`
- Test: `pc/tests/test_robo.py`

**Interfaces:**
- Produces:
  - `PORTA: int = 4210`
  - `enderecos_broadcast(ip_local: str | None) -> list[str]`
  - `enderecos_varredura(ip_local: str | None) -> list[str]`
  - `ip_local() -> str | None`
  - `vitoria_comecou(anterior: int | None, atual: int, estado_vitoria: int = 4) -> bool`
  - `class Robo(porta: int = PORTA, intervalo_busca: float = 5.0, timeout_pong: float = 1.0, alvos_broadcast: Callable[[], list[str]] | None = None, alvos_varredura: Callable[[], list[str]] | None = None)` com `iniciar() -> None`, `parar() -> None`, propriedade `ip -> str | None`, `avisar_vitoria() -> None`.
  - Execução direta: `python pc/robo.py [--timeout SEGUNDOS]`.

Os parâmetros `alvos_*` existem para os testes apontarem para `127.0.0.1`; o padrão usa `ip_local()`.

- [ ] **Step 1: Escrever os testes**

Fixture `robo_falso`: thread com socket UDP em `127.0.0.1`, porta 0 (efêmera), que guarda cada mensagem recebida numa lista e responde `PONG` ao remetente de cada `PING`. Expõe `.porta`, `.mensagens` e `.parar()`. Os `Robo` dos testes usam `porta=robo_falso.porta`, `intervalo_busca=0.2` e `timeout_pong=0.1`.

```python
def test_enderecos_broadcast():
    assert enderecos_broadcast(None) == ["255.255.255.255"]
    assert enderecos_broadcast("192.168.43.10") == ["255.255.255.255", "192.168.43.255"]

def test_enderecos_varredura():
    assert enderecos_varredura(None) == []
    v = enderecos_varredura("192.168.43.10")
    assert len(v) == 253 and "192.168.43.10" not in v
    assert v[0] == "192.168.43.1" and v[-1] == "192.168.43.254"

def test_vitoria_comecou_so_na_transicao():
    assert vitoria_comecou(2, 4) is True
    assert vitoria_comecou(4, 4) is False
    assert vitoria_comecou(None, 4) is True
    assert vitoria_comecou(4, 0) is False

def test_vitoria_comecou_de_novo_apos_sair():
    seq = [2, 4, 4, 4, 0, 6, 1, 2, 4]
    disparos = [vitoria_comecou(a, b) for a, b in zip([None] + seq, seq)]
    assert disparos.count(True) == 2

def test_acha_robo_por_broadcast(robo_falso):
    # alvos_broadcast=lambda: ["127.0.0.1"], alvos_varredura=lambda: []
    # ate 2 s: r.ip == "127.0.0.1"

def test_acha_robo_pela_varredura(robo_falso):
    # alvos_broadcast=lambda: [], alvos_varredura=lambda: ["127.0.0.1"]
    # ate 2 s: r.ip == "127.0.0.1"

def test_avisar_envia_win_tres_vezes(robo_falso):
    # robo achado; avisar_vitoria(); apos 0,5 s:
    # robo_falso.mensagens.count(b"WIN") >= 3

def test_avisar_sem_robo_nao_bloqueia():
    # Robo(porta=<porta sem ninguem>, alvos de 127.0.0.1) iniciado
    # t0 = perf_counter(); avisar_vitoria(); assert perf_counter() - t0 < 0.005

def test_sem_rede_nao_quebra():
    # alvos_broadcast=lambda: ["203.0.113.1"] (TEST-NET, inalcancavel), alvos_varredura=lambda: []
    # iniciar(); avisar_vitoria(); sleep(0.5); parar() -> nenhuma excecao; r.ip is None

def test_esquece_robo_que_parou(robo_falso):
    # robo achado; robo_falso.parar(); ate 2 s: r.ip is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd ~/beat-by-bit && python -m pytest pc/tests/test_robo.py -q`
Expected: FAIL (`ModuleNotFoundError: robo`). `pc/tests/conftest.py` põe `pc/` no `sys.path`.

- [ ] **Step 3: Implementar `pc/robo.py`**

Só stdlib. Um socket UDP com `SO_BROADCAST`, `settimeout` curto, numa thread daemon com o laço:
- Sem IP: envia `PING` aos `alvos_broadcast()`, espera `PONG` até `timeout_pong`. Sem resposta, envia `PING` aos `alvos_varredura()` e espera de novo. Guarda o IP de quem respondeu. Sem nada, dorme `intervalo_busca`.
- Com IP: a cada `intervalo_busca`, `PING` para ele; sem `PONG` em `timeout_pong`, `ip = None`.
- Fila (`queue.Queue`) de pedidos de vitória: a cada pedido, envia `WIN` 3× com 50 ms para `[ip] + alvos_broadcast()`.
- `ip_local()`: socket UDP `connect(("10.255.255.255", 1))` + `getsockname()`; `None` se der `OSError` ou começar com `127.`.
- Todo `OSError` é capturado; imprime `[robo] <erro>` uma vez por tipo de erro.
- `__main__`: `argparse` com `--timeout` (padrão 5), espera achar o robô, imprime o IP ou "robô não encontrado", chama `avisar_vitoria()`, dorme 0,3 s, sai com 0 se achou e 1 se não.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd ~/beat-by-bit && python -m pytest pc/tests/test_robo.py -q`
Expected: todos passam.

---

### Task 2: Gancho no `interface.py`

**Files:**
- Modify: `pc/interface.py` (`Renderer.__init__`, `Renderer.run()` por volta da linha 1440, bloco `__main__`)

**Interfaces:**
- Consumes: `Robo`, `vitoria_comecou` (Task 1); `STATE_WIN` e `self.fpga_state`, já existentes.

- [ ] **Step 1: Ligar o robô no `Renderer`**

Em `__init__`: `from robo import Robo, vitoria_comecou`; `self.robo = Robo(); self.robo.iniciar(); self._estado_anterior = None`. Em `run()`, logo depois de `s = self.fpga_state`: se `vitoria_comecou(self._estado_anterior, s, STATE_WIN)`, chamar `self.robo.avisar_vitoria()`; depois, `self._estado_anterior = s`. No `pygame.QUIT`, chamar `self.robo.parar()`.

- [ ] **Step 2: Fumaça sem robô na rede**

Run: `cd / && SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy timeout 8 python ~/beat-by-bit/pc/interface.py --sim; echo rc=$?`
Expected: `rc=124` (rodou até o timeout), sem traceback, no máximo uma linha `[robo]`.

- [ ] **Step 3: Vitória no `--sim` com o robô falso**

Rodar `python -c` com um robô falso igual ao da fixture, na porta 4210 em `0.0.0.0`, em outro terminal. Jogar uma música no `--sim` até vencer (ou reduzir `duration` de uma música temporariamente, sem salvar). Expected: o robô falso recebe exatamente um lote de `WIN` (3 a 6 pacotes) por vitória.

---

### Task 3: Lógica do firmware (testada no PC)

**Files:**
- Create: `robo/firmware/robo/logica.h`, `robo/firmware/robo/logica.cpp`
- Test: `robo/firmware/test/test_logica.cpp`
- Create: `robo/firmware/Makefile` (alvo `test`)

**Interfaces:**
- Produces (C++17, sem nada do Arduino; só `<cstdint>`/`<cstddef>`):
  - `enum class Mensagem { Win, Ping, Desconhecida };`
  - `Mensagem interpretarMensagem(const char* dados, size_t tamanho);`
  - `uint16_t anguloParaPulsoUs(int anguloGraus);`
  - `class Comemoracao` com `static constexpr uint32_t DURACAO_MS = 3000, PASSO_MS = 250;`, `static constexpr int CENTRO = 90, ESQUERDA = 60, DIREITA = 120;`, `bool iniciar(uint32_t agoraMs);` (false se já ativa), `bool ativa(uint32_t agoraMs) const;`, `int angulo(uint32_t agoraMs) const;`

- [ ] **Step 1: Escrever os testes** (asserts simples com contador de falhas; `main` retorna 1 se algum falhar)

```cpp
// interpretar_*
interpretarMensagem("WIN", 3)      == Mensagem::Win
interpretarMensagem("WIN\r\n", 5)  == Mensagem::Win
interpretarMensagem("PING\n", 5)   == Mensagem::Ping
interpretarMensagem("win", 3)      == Mensagem::Desconhecida
interpretarMensagem("WINNER", 6)   == Mensagem::Desconhecida
interpretarMensagem("", 0)         == Mensagem::Desconhecida
// pulso
anguloParaPulsoUs(0) == 1000;  anguloParaPulsoUs(90) == 1500;  anguloParaPulsoUs(180) == 2000
anguloParaPulsoUs(-10) == 1000; anguloParaPulsoUs(200) == 2000
// comemoracao
Comemoracao c;  c.angulo(0) == 90;  c.ativa(0) == false
c.iniciar(1000) == true
c.angulo(1000) == 60;  c.angulo(1249) == 60;  c.angulo(1250) == 120;  c.angulo(1500) == 60
c.ativa(3999) == true;  c.ativa(4000) == false;  c.angulo(4000) == 90
// iniciar_ignorado_enquanto_ativa
c.iniciar(5000); c.iniciar(5100) == false; c.ativa(7999) == true  // nao reiniciou em 5100
// virada do millis()
Comemoracao w; w.iniciar(0xFFFFFF00u); w.ativa(0x00000064u) == true; w.angulo(0x00000064u) == 60
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `make -C ~/beat-by-bit/robo/firmware test`
Expected: erro de compilação (`logica.h` não existe). O `Makefile` faz `g++ -std=c++17 -Wall -Wextra -Irobo test/test_logica.cpp robo/logica.cpp -o build/test_logica && build/test_logica`.

- [ ] **Step 3: Implementar `logica.h/.cpp`**

`interpretarMensagem` ignora `\r`, `\n` e espaços no fim e compara exato, com diferença de maiúsculas. `anguloParaPulsoUs` limita a 0..180 e faz `1000 + angulo * 1000 / 180`. `Comemoracao` guarda `inicio`, usa `agora - inicio` em `uint32_t` (o que cobre a virada) e alterna pelo índice `(decorrido / PASSO_MS) % 2`.

- [ ] **Step 4: Rodar e ver passar**

Run: `make -C ~/beat-by-bit/robo/firmware test`
Expected: `OK` e código de saída 0.

---

### Task 4: Firmware do robô (compila sem hardware)

**Files:**
- Create: `robo/firmware/robo/robo.ino`, `robo/firmware/robo/secrets.example.h`
- Modify: `robo/firmware/Makefile` (alvos `deps`, `build`, `upload PORT=...`, `monitor PORT=...`)
- Modify: `.gitignore` (`robo/firmware/robo/secrets.h`, `robo/firmware/build/`)

**Interfaces:**
- Consumes: `interpretarMensagem`, `anguloParaPulsoUs`, `Comemoracao` (Task 3).
- Produces: `secrets.h` com `#define WIFI_SSID "..."` e `#define WIFI_SENHA "..."`.

- [ ] **Step 1: Toolchain**

Alvo `deps`: `arduino-cli core install esp32:esp32` e `arduino-cli lib install ESP32Servo "Adafruit SSD1306" "FluxGarage RoboEyes"` (confirmar o nome exato com `arduino-cli lib search roboeyes`). FQBN: `esp32:esp32:esp32c3:CDCOnBoot=cdc`.
Run: `make -C ~/beat-by-bit/robo/firmware deps`
Expected: core e bibliotecas instalados.

- [ ] **Step 2: Implementar `robo.ino`**

- `setup`: servo em GPIO4 com `attach(4, 1000, 2000)` e `write(90)` logo em seguida; `Wire.begin(6, 7)`; OLED 0x3C; RoboEyes com piscar automático e olhar ocioso; botão GPIO10 com `INPUT_PULLUP`; `WiFi.begin(WIFI_SSID, WIFI_SENHA)` sem bloquear o `loop`; `WiFi.setSleep(false)`.
- `loop` (sem `delay` longo):
  - Wi-Fi caiu: humor `TIRED`.
  - Wi-Fi acabou de conectar: abre o UDP na 4210 e mostra o IP em texto por 3 s (RoboEyes pausado nesse tempo).
  - Pacote UDP recebido: `Ping` → responde `PONG` a `remoteIP()/remotePort()`; `Win` → `comemoracao.iniciar(millis())`.
  - Botão: borda de descida com debounce de 50 ms → `iniciar`.
  - Durante a comemoração: humor `HAPPY` e `servo.writeMicroseconds(anguloParaPulsoUs(comemoracao.angulo(millis())))`. No fim: humor padrão e 90°.
  - `roboEyes.update()` a cada volta, exceto nos 3 s do IP.
- Ícone de sem Wi-Fi: se o RoboEyes não permitir desenhar por cima, alternar a cada 2 s entre os olhos `TIRED` e uma tela com o texto "sem Wi-Fi".

- [ ] **Step 3: Compilar**

Run: `cp robo/firmware/robo/secrets.example.h robo/firmware/robo/secrets.h && make -C ~/beat-by-bit/robo/firmware build`
Expected: compila para ESP32-C3 sem erros.

---

### Task 5: Bancada (precisa das peças da Task 0)

Montagem solta, sem caixa. Verificação manual; registrar os resultados em `robo/README.md`.

- [ ] **Step 1: Alimentação.** Bateria → TP4056 → chave → MT3608 **sem carga**; girar o trimpot até o multímetro ler 5,0–5,1 V. Soldar o capacitor nos 5 V, respeitando a polaridade.
- [ ] **Step 2: Gravar.** ESP32-C3 pelo USB: preencher `secrets.h` com o hotspot do celular da demo em 2,4 GHz, depois `make upload PORT=/dev/ttyACM0` e `make monitor`. Expected: OLED com olhos piscando; o IP aparece por 3 s ao conectar.
- [ ] **Step 3: Rede.** PC no mesmo hotspot: `python pc/robo.py`. Expected: imprime o IP do robô, o robô dança uma vez e o comando sai com código 0. Se não achar o robô: testar `--timeout 15`. Se o hotspot isolar os clientes, parar e seguir o plano B do spec §9 (novo plano: ponte ESP-NOW).
- [ ] **Step 4: Servo na bateria.** Servo no conector, tudo alimentado pela bateria (sem USB). Disparar 10 comemorações seguidas pelo botão. Expected: nenhum reinício do ESP (os olhos não "piscam do zero"). Repetir com a bateria perto de 3,5 V.
- [ ] **Step 5: Sem rede.** Desligar o hotspot. Expected: olhos cansados/"sem Wi-Fi"; ao religar, o robô reconecta sozinho e `python pc/robo.py` volta a achá-lo.

---

### Task 6: Caixa em OpenSCAD

**Files:**
- Create: `robo/case/robo.scad` (módulos `base()`, `corpo()`, `tampa_base()`, selecionados pela variável `parte`)
- Create: `robo/case/Makefile` (alvo `stl`, que gera `build/{base,corpo,tampa_base}.stl`)

- [ ] **Step 1: Instalar o OpenSCAD** (o usuário roda `! sudo pacman -S openscad`).
- [ ] **Step 2: Modelar** com as medidas reais da Task 0 em variáveis no topo, cada uma com um comentário da origem (medida na bancada ou datasheet):
  - **Base:** suporte da 18650, TP4056 com o USB numa janela externa, MT3608, chave e botão acessíveis.
  - **Servo:** encaixe preso pelos parafusos das abas, eixo para cima e centralizado; furo central para 4 fios (5V, GND, sinal do servo, botão).
  - **Corpo e cabeça:** janela do OLED na frente, com encaixe pelo lado de dentro e furos M2; espaço para o ESP32-C3, com o USB acessível para regravar; encaixe do horn por baixo, parafusado no horn.
  - **Dimensões gerais:** ~6–8 cm de largura e 10–12 cm de altura; base mais larga que o corpo.
- [ ] **Step 3: Gerar STL**
  Run: `make -C ~/beat-by-bit/robo/case stl`
  Expected: 3 STLs gerados sem `WARNING`/`ERROR` no log do OpenSCAD.
- [ ] **Step 4: Teste de encaixe.** Imprimir só a placa do servo e a janela do OLED em rascunho (camada alta). Expected: o servo entra e é parafusado sem forçar; o OLED encaixa na janela. Corrigir as variáveis e só então imprimir as peças completas.

---

### Task 7: Montagem final e ensaio da demo

**Files:**
- Create: `robo/README.md` (peças, ligações do spec §7.2, como gravar, como testar, como **desmontar e devolver o servo**)
- Modify: `README.md` da raiz (uma linha apontando para `robo/README.md`)

- [ ] **Step 1: Montar** na caixa. O servo fica só parafusado e o cabo dele só encaixado no conector.
- [ ] **Step 2: Ponta a ponta com a FPGA.** Robô ligado, PC e robô no hotspot, `python pc/interface.py --port /dev/ttyUSB0`, jogar até vencer. Expected: o robô dança uma vez por vitória; o jogo não muda de ritmo.
- [ ] **Step 3: Ensaio "robô desligado".** Mesmo roteiro com o robô desligado. Expected: o jogo idêntico, sem erro na tela.
- [ ] **Step 4: Desmontagem de teste.** Tirar o servo da caixa seguindo o `robo/README.md`. Expected: sai inteiro, com cabo e conector originais.
