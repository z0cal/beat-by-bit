# Identidade visual — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** logo pixelado vetorial + `beatbybit.sty` (fork do poliusp, direção A) + os dois documentos de `docs/` migrados.

**Architecture:** o logo é um mapa de texto (1 caractere = 1 célula) que um script Python converte em SVG/PDF/PNG; o template é uma cópia autocontida do `poliusp.sty` em `docs/`, com capa, cores, títulos, cabeçalho e código refeitos; os `.tex` só trocam o preâmbulo e os ambientes de código.

**Tech Stack:** Python 3 + Pillow + numpy (gerador), rsvg-convert/inkscape (SVG→PDF), LuaLaTeX + minted + fontspec, Press Start 2P (OFL).

**Spec:** `docs/specs/2026-10-22-identidade-visual-design.md`

## Global Constraints

- `~/beat-by-bit` não tem git: sem commits; originais dos `.tex` copiados para `docs/legado/` antes de qualquer edição; nenhum `rm` sem aprovação do usuário.
- `~/faculdade/poli_usp` é só leitura.
- Paleta e grade exatamente como na spec §4–§5 (célula = 8,6 px do original, logo 63×55, ícone 46×39).
- Motor LuaLaTeX `-shell-escape` via `latexmkrc`.
- Verificação visual = `pdftoppm` + inspeção das páginas antes de afirmar qualquer coisa.

## Review Focus

- Glifos ausentes na Press Start 2P (`ç ã õ é ê í ó ú — _`) em títulos → devem aparecer, não sumir calados (checar `Missing character` no log).
- `\texttt{…}` dentro de `\section`/`\subsection` → continua mono, sem quebrar o título.
- Costuras no logo em zoom alto → não pode haver (SVG sem `<rect>`; uma `<path>` por cor).
- Título longo de subseção em fonte pixel larga → quebra de linha sem estourar a margem.
- Bloco de código longo (`codigolongo`) quebrando página → fundo escuro contínuo nas duas páginas.

---

### Task 1: Logo pixelado

**Files:**
- Create: `identidade/logo.txt` (mapa 63×55; legenda de cores no topo do arquivo)
- Create: `identidade/gera_logo.py`
- Create: `identidade/test_gera_logo.py`
- Create (gerados): `identidade/{logo-escuro,logo-claro,icone}.{svg,pdf}`, `identidade/png/*-{1,4,8}x.png`

**Interfaces:**
- Produces: `identidade/logo-escuro.pdf`, `identidade/logo-claro.pdf`, `identidade/icone.pdf` (usados pelas Tasks 2–3).
- `gera_logo.py`: `ler_mapa(caminho) -> (grade: list[str], cores: dict[str, str])`; `contornos(grade, ch) -> list[list[tuple[int,int]]]` (polígonos ortogonais unidos da cor `ch`); `svg(grade, cores, trocas={}) -> str`; `main()` gera tudo.

- [ ] **Step 1:** Gerar o rascunho do mapa a partir de `pixeliza.py` (argumento 10) e salvar como `logo.txt`; letras do letreiro numa cor própria (`L`), para a variante clara trocar só elas.
- [ ] **Step 2: Testes** em `test_gera_logo.py`: `test_mapa_retangular` (todas as linhas com 63 colunas, 55 linhas), `test_so_cores_conhecidas`, `test_svg_sem_rect` (`'<rect' not in svg`), `test_uma_path_por_cor`, `test_area_preservada` (soma das áreas dos polígonos da cor = nº de células da cor), `test_png_dimensoes` (8× → 504×440), `test_icone_46x39`, `test_claro_troca_so_letreiro` (cor `L` vira `#0F172A`; branco da nota continua `#F8F8F8`).
- [ ] **Step 3:** Rodar `python -m pytest identidade -q` → falham.
- [ ] **Step 4:** Implementar `gera_logo.py` (contornos: arestas de fronteira entre células de cor diferente, encadeadas em laços; `fill-rule="evenodd"`). PDF via `rsvg-convert -f pdf`.
- [ ] **Step 5:** Testes passam.
- [ ] **Step 6: Acabamento à mão** no `logo.txt` (spec §5): pixels soltos, contorno, nota, letras, "by". Rodar de novo; render 8× sobre `#0F172A` e sobre branco; inspecionar.
- [ ] **Step 7:** Abrir `logo-escuro.pdf` com zoom alto (sioyek/pdftoppm -r 1200) e confirmar ausência de costuras.

### Task 2: `beatbybit.sty` — miolo

**Files:**
- Create: `docs/beatbybit.sty` (cópia de `~/faculdade/poli_usp/poliusp.sty`, árvore de trabalho atual)
- Create: `docs/latexmkrc` (cópia do poliusp)
- Create: `docs/fontes/PressStart2P-Regular.ttf` (+ `OFL.txt`)
- Create: `docs/teste-template.tex` (documento de teste com todos os recursos)

**Interfaces:**
- Produces: `\usepackage[<opções>]{beatbybit}` com a mesma interface do poliusp + cores `bblaranja`, `bbazul`, `bbambar`, `bbverde`, `bbverdetexto`, `bbvermelho`, `bbnoite`, `bbcartao`, `bbcinza`, `bbcinzaclaro`, `bbceu`; família `\fontePixel`; `\bbFaixa{<altura>}` (degradê horizontal).

- [ ] **Step 1:** Baixar Press Start 2P (google/fonts, `ofl/pressstart2p`) para `docs/fontes/`.
- [ ] **Step 2:** Escrever `teste-template.tex`: `\usepackage[full]{beatbybit}`, seções/subseções com acentos, `—` e `\texttt{uart\_tx.v}` no título, título longo, figura, tabela, `verilogcode`, `pythoncode`, `terminal`, `diff`, `codigolongo` de 80 linhas, `\cref`, sumário.
- [ ] **Step 3:** Compilar com `latexmk` → falha (pacote inexistente).
- [ ] **Step 4:** Copiar o sty, renomear (`\ProvidesPackage{beatbybit}`), e trocar: cores (spec §4), links/legendas/teoremas → laranja; `\titleformat` de seção/subseção → `\fontePixel`, números laranja; cabeçalho = título curto em `\fontePixel` cinza à esquerda + `icone.pdf` à direita + filete degradê; rodapé = página + faixa degradê no pé de toda página (eso-pic `AddToShipoutPictureBG`); minted com `bgcolor=bbnoite`, texto claro, paleta escura da spec §6, números de linha `bbcinza`; remover Copperplate.
- [ ] **Step 5:** Compilar sem erro; `grep -c "Missing character" teste-template.log` = 0; renderizar e inspecionar (Review Focus 1, 2, 4, 5).

### Task 3: `beatbybit.sty` — capa e página de abertura

**Files:**
- Modify: `docs/beatbybit.sty` (seções 3, 9, 10)
- Modify: `docs/teste-template.tex`

**Interfaces:**
- Produces: macros `\professor{}`, `\bancada{}`, `\lema{<linha1>}{<linha2>}` (padrão: "Ritmo, atenção e coordenação" / "Uma nota de cada vez"); ambiente `ficha` (cartão branco, filete laranja no topo) e `\paginaabertura{<conteúdo>}` (página sem número após a capa).

- [ ] **Step 1:** Capa: fundo `bbnoite` página inteira; topo = selo Poli recolorido em branco + "Escola Politécnica · USP" + disciplina; centro = `logo-escuro.pdf`; `\titulo` em `\fontePixel` laranja; `\subtitulo` (linha nova); lema em cinza; autores/professor/bancada/data; faixa degradê no pé. Alturas na grade áurea do poliusp.
- [ ] **Step 2:** `ficha` + `\paginaabertura`.
- [ ] **Step 3:** Compilar o teste, renderizar a capa e a abertura, inspecionar contra o mockup A.

### Task 4: Migrar `documentacao.tex`

**Files:**
- Create: `docs/legado/documentacao.tex` (cópia do original)
- Modify: `docs/documentacao.tex`

- [ ] **Step 1:** Compilar o original (pdflatex) e anotar nº de páginas, figuras, listagens, `\ref`.
- [ ] **Step 2:** Trocar o preâmbulo por `beatbybit`; capa própria → campos + `\paginaabertura` (resumo + ficha); `lstlisting` → `verilogcode`/`bashcode`/`terminal` (com `listing`+`\caption` onde havia caption); cores locais → paleta (spec §7).
- [ ] **Step 3:** Compilar sem erro e sem `??`; mesma contagem de figuras/listagens/seções; renderizar e inspecionar os 4 TikZ e os blocos de código.

### Task 5: Migrar `doc_incrementos.tex`

Igual à Task 4 para `doc_incrementos.tex` (inclui `\antes`/`\depois`/`\novo`/`\alterado` e `destaque` remapeados conforme spec §7).

### Task 6: Fechamento

- [ ] Atualizar `docs/CONTEXT.md` (onde fica o template e o logo, como compilar).
- [ ] Revisão final do conjunto contra a spec.
