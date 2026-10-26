# Identidade visual — logo vetorial + template LaTeX

Status: design aprovado em conversa; aguardando revisão do documento.
Data: 2026-10-22.

## 1. Intenção

A documentação do Beat by Bit (PCS3635 — Laboratório Digital I) é entregue em
PDF, lido na tela. Hoje ela usa um visual genérico (azul `fpgablue`, listings
cinza) que não conversa com a identidade do projeto, definida nos slides
(`~/Downloads/beat_by_bit-1-1.pdf`) e no panfleto
(`~/Downloads/copia_de_0f172a.pdf`). O objetivo é que o relatório pareça da
mesma família: ao abrir o PDF, o professor reconhece o projeto já na capa.

O logo existe só como imagem raster (PNG 1024×1024 com máscara, embutido no
panfleto) e ainda é "pseudo-pixel-art": pixels de tamanhos irregulares, nota
musical inclinada e lisa, sombras e artefatos de JPEG. Por isso ele precisa ser
**redesenhado** numa grade regular, e não só traçado.

## 2. Metas, não-metas e antimetas

**Metas**
- Logo 100% pixelado em grade regular, vetorial, nítido em qualquer tamanho.
- Variante do template poliusp só para o Beat by Bit, na direção "A" (abaixo).
- Os dois documentos de `docs/` (`documentacao.tex`, `doc_incrementos.tex`)
  passam a usar a variante, sem mudar o conteúdo.

**Não-metas**
- Alterar `~/faculdade/poli_usp` (continua servindo às outras disciplinas).
- Refazer slides, panfleto ou os assets do jogo em `pc/`.
- Miolo escuro.
- Revisar ou reescrever o texto dos documentos.

**Antimetas** (fracasso mesmo que "funcione")
- Elemento que destoa: fonte ou cor fora do sistema, logo com cara de raster
  borrado ou traçado automático.
- Costuras: linhas finas entre pixels do logo em algum visualizador de PDF.
- Fonte pixel em texto corrido (ilegível em parágrafo).
- Perder conteúdo, legenda, referência cruzada ou diagrama TikZ na migração.

## 3. Restrições

- `~/beat-by-bit` **não tem git**. Antes de editar os `.tex`, copiar os
  originais para `docs/legado/` (decisão minha; nada é apagado).
- O `poliusp.sty` de origem tem alterações **não commitadas** (~235 linhas em
  2026-10-22). A variante parte da **árvore de trabalho atual**, não do HEAD.
- Motor: LuaLaTeX com `-shell-escape` (minted), como o poliusp; levar o
  `latexmkrc` junto.
- Fontes no TeX Live já usadas pelo poliusp: XCharter, XCharter Math, Fira
  Sans, Roboto Mono. Press Start 2P **não está instalada**: baixar o `.ttf` do
  Google Fonts (licença OFL) para `docs/fontes/`, como o poliusp faz com a
  Copperplate.

## 4. Paleta (amostrada dos slides e do logo)

| Papel | Cor | Origem |
|---|---|---|
| Fundo da capa / blocos de código | `#0F172A` | slides |
| Cartão (se precisar de caixa escura) | `#182032` | slides |
| Destaque principal (laranja) | `#E8682A` | slides |
| Secundária (azul) | `#3B82F6` | slides |
| Âmbar / verde (realces pontuais) | `#F59E0B` / `#22C55E` | slides |
| Vermelho (antes / aviso) | `#B83828` | logo |
| Verde para texto sobre branco | `#16A34A` | mesma família do `#22C55E` |
| Cinza secundário / terciário | `#64748B` / `#94A3B8` | slides |
| Degradê da faixa (esq.→dir.) | `#E8682A` → `#F59E0B` → `#3B82F6` → `#38BDF8` | barra do rodapé dos slides |

O logo mantém as **próprias** cores (azul `#3868B8`, azul-claro `#4888C8`,
marinho `#182858`, laranja `#F87838`, laranja-claro `#F88838`, vermelho
`#B83828`, branco `#F8F8F8`). Decisão minha: é a marca que a equipe criou; o
template usa a paleta dos slides. As duas são próximas o bastante para não
destoar.

## 5. Parte 1 — Logo pixelado

**Decidido pelo usuário:** tudo pixelado (ícone, nota e letreiro "BEAT by BIT")
na grade **fina**: célula = 8,6 px do original (letreiro com 10 linhas de
altura), ícone 46×39, logo completo 63×55 células.

**Ponto de partida:** rascunho automático já validado visualmente pelo usuário
(`/tmp/claude-1000/-home-zocal/07aaa00c-e0ea-4527-ade1-9423edd0a305/scratchpad/scripts/pixeliza.py`,
argumento `10`): grade ancorada no canto superior esquerdo do letreiro (x=233,
y=614 no PNG 1024×1024), cada célula recebe a cor majoritária após mapear cada
pixel à paleta do logo. O scratchpad é temporário; copiar o script para o
projeto.

**Acabamento à mão** (do builder): limpar pixels soltos e ruído de JPEG;
contorno e sombras coerentes; nota legível e com haste/bandeira de espessura
uniforme; letras com traço uniforme; "by" legível.

**Fonte da verdade:** o desenho final fica num mapa de texto (um caractere por
célula, um por cor) + um script que gera as saídas. Assim, ajustar um pixel é
editar o texto e rodar de novo.

**Saídas**, em `beat-by-bit/identidade/` (decisão minha: o logo serve ao
projeto inteiro, não só aos docs):
- `logo-escuro` (letras brancas, para fundo escuro), `logo-claro` (letras
  `#0F172A`, para fundo branco), `icone` (só o quebra-cabeça).
- Cada uma em SVG (uma `<path>` por cor, contornos unidos — **sem** um
  retângulo por pixel, que causa costuras), PDF (para o LaTeX) e PNG
  pixel-perfect em escala inteira (1×, 4×, 8×).
- Na versão clara e no ícone sobre branco, conferir que a nota branca não "vaza"
  para o fundo; se vazar, contornar.

## 6. Parte 2 — Variante do template (`beatbybit.sty`)

**Decidido pelo usuário:** variante separada; direção **A** — capa noturna,
miolo branco. Os palpites abaixo foram confirmados ("está tudo certo").

**Onde:** `beat-by-bit/docs/beatbybit.sty`, cópia modificada do `poliusp.sty`
(decisão minha: fork autocontido, para a equipe compilar sem depender de
`~/faculdade`). Mesma interface: opções (`math`, `code`, `tikz`, `full`…),
ambientes de código, `\inputtrecho`, `codigolongo`, e as macros de capa
(`\titulo`, `\subtitulo`, `\Autores`, `\departamento`, `\disciplina`, `\data`).

**Capa** (ver mockup A, `.superpowers/brainstorm/*/content/direcao.html`):
- Página inteira em `#0F172A`, faixa degradê no pé (altura ≈ 0,4 % da página,
  como nos slides).
- No topo: selo da Poli/USP pequeno, em branco (reaproveitar um vetor de
  `~/faculdade/poli_usp/logospoli/`, recolorido), "Escola Politécnica · USP" e
  a disciplina.
- Centro: `logo-escuro`; abaixo, `\titulo` em Press Start 2P laranja; abaixo,
  o lema em Press Start 2P, como no panfleto e nos slides: "Ritmo, atenção e
  coordenação" / "Uma nota de cada vez", ambas em cinza `#94A3B8`, porque o
  laranja da capa já é do título, como no mockup A aprovado; padrão
  do template e trocável por uma macro `\lema{…}{…}` (decisão minha).
- Autores e data em branco/cinza, na fonte do texto.
- Dados provisórios (o usuário corrige depois), tirados do panfleto:
  "PCS3635 · Laboratório de Projeto de Sistemas Digitais I", "Prof. Edson T.
  Midorikawa", "Bancada B7 · Turma 4", autores Victor Bertelli, Gabriel Zocal
  Santos e Caio Nassar de Souza (sem NUSP), data fixa. A capa precisa de
  campos para professor e bancada/turma (`\professor`, `\bancada`).
- Posições verticais guiadas pela proporção áurea do poliusp (`CAPA.md`), por
  coerência com o original. A Copperplate sai.

**Miolo** (papel branco):
- Títulos de seção e subseção em Press Start 2P; números em laranja `#E8682A`.
- Texto em XCharter; legendas, rótulos e cabeçalho em Fira, como no poliusp.
- Cabeçalho: título curto em Press Start 2P pequeno, cinza, à esquerda; `icone`
  à direita; filete fino em degradê embaixo.
- Rodapé: número da página + faixa degradê fina no pé de **toda** página.
- Cores: o azul Poli sai; o laranja vira a cor principal (números, rótulos de
  legenda, links); o azul é secundário; cinzas puxam para o ardósia.
- Blocos de código: fundo `#0F172A`, texto claro, realce de sintaxe refeito
  para fundo escuro na paleta dos slides (variantes claras: azul `#60A5FA`,
  laranja `#FB923C`, âmbar `#FACC15`, verde `#4ADE80`, comentários `#64748B`).
  Código inline (`\texttt`) continua claro.

## 7. Parte 3 — Migração dos documentos

- `documentacao.tex` (hoje pdfLaTeX, `listings`, capa própria, cores
  `fpgablue`/`pygreen`/`uartred`…, 4 TikZ, 2 `mdframed`) e
  `doc_incrementos.tex` (hoje `fontspec`, `listings`) passam a carregar
  `beatbybit.sty`.
- `lstlisting` → ambientes do template (`verilogcode`, `pythoncode`,
  `bashcode`/`terminal`), conservando as legendas.
- A capa própria vira os campos da capa do template. Os subtítulos atuais
  ("Documentação Técnica — Semestre 2", "Jogo de Ritmo em FPGA — Placa
  DE0-CV", "Semestre 2 — Revisão de Código") vão para `\subtitulo`, que ganha
  uma linha na capa entre o título e o lema (decisão minha).
- O que não cabe na capa — tabela de ficha técnica e caixa "Resumo das
  mudanças" — vai para uma **página de abertura** sem número logo após a capa,
  com um ambiente novo do template (`ficha`) no estilo dos cartões dos slides:
  caixa branca com filete laranja no topo (decisão minha; nada se perde).
  As linhas de pé ("Laboratório de Circuitos Digitais"…) são substituídas
  pelo campo `\disciplina`.
- Cores locais nos TikZ, `mdframed` e macros → remapeadas para a paleta (§4),
  preservando o significado (decisão minha):
  - vermelho (`uartred`, `\antes`, `oldcolor`, aviso, strings) → vermelho do
    logo `#B83828`;
  - verde (`pygreen`, `\depois`, `\novo`) → verde `#22C55E` em traço/fundo e
    `#16A34A` em texto sobre branco (mesma família, contraste legível);
  - laranja (`\alterado`) → `#E8682A`;
  - azul (`fpgablue` etc.) → `#3B82F6`;
  - amarelo de fundo (`destaque`, `notecolor`) → âmbar `#F59E0B` a ~12 %.
  Legendas que citam cor ("destacados em azul", "verde tracejado")
  continuam verdadeiras.
- O texto não muda. Antes e depois, os dois compilam; conferir sumário,
  referências cruzadas e número de figuras/listagens.

## 8. Verificação

Renderizar (pdftoppm) e inspecionar: capa, uma página com cabeçalho e
seção/subseção, um bloco de código, o sumário, uma figura TikZ. Conferir na
fonte pixel os glifos usados nos títulos: `ç ã õ é ê í ó ú —` e o
`\texttt{uart\_tx.v}` dentro de título. Abrir o PDF do logo com zoom alto em
dois visualizadores (sioyek + outro) para checar costuras.

## 9. Fica a critério do builder

Tamanhos exatos das fontes de título, espaçamentos, estilo do sumário, a
escolha do vetor do selo da Poli, o fallback de glifos ausentes na Press
Start 2P, a ferramenta para unir os pixels em `<path>`, e a ordem de trabalho.

## 10. Revisão estética (2026-10-22, aprovada pelo usuário)

Depois da primeira versão, uma revisão comparando com o poliusp mudou, com
aprovação do usuário:

- **Código:** sai o fundo `#0F172A` (pesava como buraco preto na página
  branca). Bloco no papel com filete laranja de 1,5 pt à esquerda, realce com
  4 cores da paleta legíveis sobre branco, números de linha em Roboto Mono
  cinza-claro pendurados à esquerda do filete.
- **Cabeçalho:** filete **neutro** (cinza fino), decisão do usuário; o degradê
  fica só na faixa do pé. Ícone apoiado na linha de base; número da página em
  pixel cinza.
- **Capa:** escada áurea completa (selo H/φ⁵, disciplina H/φ⁴, marca H/φ²,
  título H/φ, autores H(1−1/φ³), meta H(1−1/φ⁴)); logo a L/φ² (61,1 mm) com o
  lema logo abaixo; ornamento de 4 "pixels" nas cores da faixa sobre os
  autores; só pixel + Fira na capa.
- **Títulos pixel:** seção 11 pt, subseção 8 pt, espaço entre palavras pela
  metade, ponto da numeração apertado, recuo fixo dos números.
- **Links e sumário:** laranja escurecido para texto; sumário em Fira escura com
  número laranja.
- **Abertura, ficha e notas:** página de abertura com cabeçalho; ficha sem
  vazamento do filete; ambientes `nota` (âmbar) e `aviso` (vermelho) no template,
  substituindo os `mdframed` dos documentos.

## 11. Capa nova (2026-10-23, aprovada pelo usuário)

Substitui a capa das seções 6 e 10. Motivo: a escada áurea da capa antiga
aplicava φ ao centro de sete blocos pequenos no mesmo eixo; a proporção ficava
invisível e só a marca tinha peso. Exploradas e descartadas: capa clara com eixo
à esquerda, retângulo de Fibonacci, corte noturno no topo, cartões e colunas no
pé, placar de jogo no topo.

- **Fundo** `#0F172A` com a textura do panfleto: quadrados de 4,2 mm numa grade
  de 5,4 mm, de 140 mm até o pé, mais densos para baixo; semente fixa (Lua);
  área do filete livre.
- **Cabeçalho** (margem W/φ⁵ = 18,9 mm, também no topo): selo Poli/USP branco
  com altura visível H/φ⁶; à direita dele, escola, `\departamento` (se houver)
  e nome da disciplina em Fira cinza; no canto direito, o código da disciplina
  em pixel laranja. O nome da disciplina foi para a esquerda, e não para baixo
  do código, porque à direita colidia com a linha da escola.
- **Marca** `logo-escuro` centrada em H/φ², largura W/φ².
- **Título** como nos slides: Press Start 2P 18 pt branco, palavra marcada com
  `\realce{…}` em laranja; primeira linha em H/φ, as demais descem. Rótulo
  `\subtitulo` em pixel laranja 7 pt, 6,6 mm acima do topo do título, crescendo
  para cima.
- **Créditos** em bloco único no eixo (bloco de créditos de cartaz): autores em
  Fira 11 pt branca separados por " · " (linha de base em H(1−1/φ⁴)); abaixo,
  "Prof. · Bancada · Turma · data" em versaletes espaçados. Filete de 0,4 pt
  `#334155`: largura = a do título (ou a dos créditos + 2 × 8,5 mm, se maior),
  altura = largura/φ⁴.
- **Campos**: `\disciplina[código]{nome}`, `\professor{nome}` (a capa escreve
  "Prof."), `\bancada{B7}` e `\turma{4}` (a capa escreve os rótulos),
  `\realce{…}` no `\titulo`. `\lemacapa` continua aceito, sem uso na capa.
  `\destaque` não serve: `doc_incrementos.tex` já tem um ambiente com esse nome.
- Fira da capa em corpo nominal (`\bbFiraCapa`), sem o `Scale = MatchLowercase`
  do miolo.
- Variante `logo-claro` com contorno de adesivo (1 célula `#0F172A` no ícone)
  foi prototipada para capas claras e não entrou; o protótipo não foi guardado
  no projeto.
- **Fontes LaTeX movidas** (2026-10-23, pedido do usuário) para
  `~/projetos/overleaf/beat-by-bit/`, projeto autocontido na estrutura do
  `pcs3645-projeto`: `main.tex` + `capa.tex` + `preambulo.tex` + `secoes/` +
  `figures/`, com o template, `identidade/` (cópias dos PDFs do logo) e
  `fontes/`. Os documentos `documentacao.tex` e `doc_incrementos.tex` eram
  conteúdo de teste e foram descartados; o documento recomeça do zero.
