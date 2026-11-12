// Panfleto do Beat by Bit: A5 paisagem, frente e verso, dobrado ao meio.
//   Pagina 1 (fora):   contracapa (esquerda) | capa (direita)
//   Pagina 2 (dentro): como jogar (esquerda) | botoes, sensor, pontos (direita)
//
// Compilar (dentro de docs/panfleto):
//   typst compile --font-path assets \
//     --font-path /usr/share/texmf-dist/fonts/opentype/adobe/sourcesanspro \
//     panfleto.typ

// ── Cores (as mesmas da interface do jogo) ──────────────────────────────────
#let fundo = rgb("#080c19")
#let painel = rgb("#0b1228")
#let linha = rgb("#1e305c")
#let principal = rgb("#ecf2ff")
#let apoio = rgb("#96b0dc")
#let dica = rgb("#7088b8")
#let laranja = rgb("#ff8200")
#let azul = rgb("#3868b8")
#let trilhas = (rgb("#ffdc00"), rgb("#00b4ff"), rgb("#00ff64"), rgb("#ffffff"))

#let W = 105mm
#let H = 148mm
#let M = 9mm

#set page(width: 2 * W, height: H, margin: 0mm, fill: fundo)
#set text(font: "Source Sans Pro", size: 8pt, fill: principal, lang: "pt", region: "br")
#set par(leading: 0.52em, spacing: 0.9em, justify: false)

#let pixel(body, size: 7pt, fill: principal) = text(
  font: "Press Start 2P", size: size, fill: fill, body,
)

// Fundo de quadradinhos, mais denso embaixo (como a animacao da tela inicial).
// Gerador congruente simples: o desenho e sempre o mesmo para a mesma semente.
#let quadradinhos(semente) = {
  let cel = 5.25mm
  let cols = 20
  let lins = 29
  let s = semente
  for l in range(lins) {
    for c in range(cols) {
      s = calc.rem(s * 1103515245 + 12345, 2147483648)
      let r = calc.rem(calc.quo(s, 65536), 1000)
      let limite = 25 + 190 * l * l / (lins * lins)
      if r < limite {
        let cor = if calc.rem(r, 5) == 0 { laranja } else { azul }
        place(
          dx: c * cel + 0.5mm, dy: l * cel + 0.5mm,
          rect(width: cel - 1mm, height: cel - 1mm, radius: 0.6mm,
               fill: cor.transparentize(90%)),
        )
      }
    }
  }
}

#let lado(semente, body) = block(width: W, height: H, clip: true, {
  place(quadradinhos(semente))
  block(width: W, height: H, inset: M, body)
})

#let titulo(body, cor: laranja) = {
  pixel(size: 8.5pt, fill: cor, body)
  v(-1.5mm)
  line(length: 12mm, stroke: 1.2pt + cor)
  v(0.5mm)
}

#let botao(cor, r: 2.6mm) = box(baseline: 22%, circle(
  radius: r, fill: cor, stroke: 0.9pt + cor.darken(45%),
))

#let cartao(body) = block(
  width: 100%, fill: painel.transparentize(8%), stroke: 0.6pt + linha,
  radius: 2mm, inset: 3.2mm, body,
)

#let captura(arquivo) = block(
  radius: 2mm, clip: true, stroke: 0.6pt + linha, image(arquivo, width: 100%),
)

// ════════════════════════════ PAGINA 1: FORA ════════════════════════════════
#grid(
  columns: (W, W),
  // ── Contracapa ────────────────────────────────────────────────────────────
  lado(7)[
    #titulo(cor: apoio)[Para quem acompanha]

    Ao fim de cada música, o Beat by Bit mostra como foi a sessão e guarda o
    histórico por perfil: três letras escolhidas pela criança, sem nenhum dado
    pessoal.

    #captura("assets/s4-fim-sessao.png")

    #set text(size: 7.6pt)
    #grid(
      columns: (1fr, 1fr), column-gutter: 4mm, row-gutter: 2mm,
      [#text(fill: laranja, weight: "bold")[Taxa de acerto e score] \
        #text(fill: apoio)[os dois números principais da sessão]],
      [#text(fill: laranja, weight: "bold")[Assincronia] \
        #text(fill: apoio)[quanto cada toque adiantou ou atrasou, em milissegundos]],
      [#text(fill: laranja, weight: "bold")[Trilhas e mãos] \
        #text(fill: apoio)[acertos por botão e por mão, esquerda e direita]],
      [#text(fill: laranja, weight: "bold")[Evolução] \
        #text(fill: apoio)[comparação com a sessão anterior do mesmo perfil]],
    )

    #v(1fr)
    #text(size: 7pt, fill: dica)[
      O jogo oferece uma atividade rítmica estruturada e mede o desempenho.
      Não diagnostica nem substitui a terapia.
    ]
    #v(2mm)
    #line(length: 100%, stroke: 0.5pt + linha)
    #v(0.5mm)
    #grid(
      columns: (19mm, 1fr), column-gutter: 4mm, align: horizon,
      image("assets/selo-poli-branco.svg", width: 19mm),
      text(size: 7pt, fill: apoio)[
        #text(fill: principal, weight: "bold")[PCS3645 · Laboratório de Projeto de Sistemas Digitais II] \
        Escola Politécnica da USP · Prof. Edson T. Midorikawa \
        Bancada A5 · Turma 1 \
        Victor Bertelli · Gabriel Zocal · Caio Nassar · Erwan P. Didelot
      ],
    )
  ],
  // ── Capa ──────────────────────────────────────────────────────────────────
  lado(31)[
    #set align(center)
    #v(1fr)
    #image("assets/logo-escuro.svg", width: 62mm)
    #v(9mm)
    #pixel(size: 6.4pt, fill: laranja)[Ritmo, atenção e coordenação]
    #v(-0.5mm)
    #pixel(size: 6.4pt, fill: principal)[Uma nota de cada vez]
    #v(1.4fr)
    #text(size: 8.5pt, fill: apoio)[
      Jogo de ritmo em FPGA para crianças autistas
    ]
    #v(1mm)
    #box(botao(trilhas.at(0), r: 1.6mm)) #h(1mm)
    #box(botao(trilhas.at(1), r: 1.6mm)) #h(1mm)
    #box(botao(trilhas.at(2), r: 1.6mm)) #h(1mm)
    #box(botao(trilhas.at(3), r: 1.6mm))
  ],
)

// ════════════════════════════ PAGINA 2: DENTRO ══════════════════════════════
#let passo(n, nome, body) = grid(
  columns: (7mm, 1fr), column-gutter: 2mm,
  pixel(size: 11pt, fill: laranja)[#n],
  [#text(weight: "bold", size: 8.6pt)[#nome] \ #text(fill: apoio)[#body]],
)

#grid(
  columns: (W, W),
  // ── Como jogar ────────────────────────────────────────────────────────────
  lado(53)[
    #titulo[Como jogar]

    #passo(1, [Comece])[Aperte START na tela inicial.]
    #passo(2, [Escolha a música])[Cada música tem um botão: aperte o da cor
      dela.]
    #passo(3, [Espere a contagem])[3, 2, 1 e a música começa sozinha.]
    #passo(4, [Toque no ritmo])[As notas descem por quatro trilhas. Aperte o
      botão da cor quando a nota chegar à linha de acerto. O alvo acende
      quando você acerta.]
    #passo(5, [Salve a sessão])[No fim, aperte START, escolha as três letras
      do seu perfil e aperte START de novo.]

    #v(1fr)
    #captura("assets/s4-jogo.png")
    #v(-1.5mm)
    #text(size: 6.8pt, fill: dica)[
      À esquerda, o score e a barra de energia; no centro, as quatro trilhas
      e a linha de acerto.
    ]
  ],
  // ── Botoes, sensor, pontuacao e ajustes ───────────────────────────────────
  lado(88)[
    #titulo[Os botões]

    #grid(
      columns: (1fr, 1fr, 1fr, 1fr), column-gutter: 1.5mm, align: center,
      ..(
        (0, [Amarelo], [Concerning Hobbits]),
        (1, [Azul], [Song of Storms]),
        (2, [Verde], [Super Patrulha Delta]),
        (3, [Branco], [Pokémon, abertura]),
      ).map(((i, cor, musica)) => [
        #botao(trilhas.at(i), r: 3.6mm) \
        #v(-1mm)
        #text(weight: "bold", size: 7.6pt)[#cor] \
        #text(size: 6.8pt, fill: apoio)[#musica]
      ])
    )
    #v(-0.5mm)
    #text(size: 7.4pt, fill: apoio)[
      #text(fill: laranja, weight: "bold")[START] inicia, pausa e confirma.
      Na pausa, o botão amarelo volta à tela inicial.
    ]

    #v(1.5mm)
    #titulo[A mão no sensor]
    #cartao[
      Deixe a mão de #text(weight: "bold")[5 a 30 cm] acima do sensor para
      encher a barra de energia; mexer a mão enche mais rápido. Barra cheia
      liga o #text(weight: "bold")[x2] por 8 segundos: cada acerto vale o
      dobro. Não usar o sensor nunca tira pontos.
    ]

    #v(1.5mm)
    #titulo[Pontuação]
    #set text(size: 7.6pt)
    #grid(
      columns: (auto, 1fr), column-gutter: 3mm, row-gutter: 1.8mm,
      text(weight: "bold")[Acerto], text(fill: apoio)[100 pontos mais o combo atual],
      text(weight: "bold")[Combo], text(fill: apoio)[cresce a cada acerto seguido],
      text(weight: "bold")[x2 ligado], text(fill: apoio)[cada acerto vale o dobro],
      text(weight: "bold")[Fora de hora], text(fill: apoio)[zera o combo, mas não tira pontos],
    )

    #v(1fr)
    #cartao[
      #text(fill: laranja, weight: "bold")[Ajustes.] Na tela inicial, qualquer
      botão colorido abre as configurações: volume de graves, médios, agudos e
      brilho, metrônomo, modo sem som e o
      #text(weight: "bold")[perfil sensorial], com menos efeitos e som mais
      baixo.
    ]
  ],
)
