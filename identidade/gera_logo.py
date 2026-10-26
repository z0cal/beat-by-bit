"""Gera o logo do Beat by Bit (SVG, PDF e PNG) a partir do mapa logo.txt.

O mapa é a fonte da verdade: um caractere por célula, cores declaradas no
cabeçalho. Para mudar um pixel, edite o mapa e rode `python gera_logo.py`.
"""
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageColor

AQUI = Path(__file__).resolve().parent
NOITE = "#0F172A"  # letreiro da versão clara
FINO = 8  # subdivisão da célula usada nas juntas entre cores


def ler_mapa(caminho):
    """Devolve (grade, cores): linhas do desenho e {caractere: '#RRGGBB'}."""
    grade, cores = [], {}
    for linha in Path(caminho).read_text().splitlines():
        m = re.match(r"# (\S) (#[0-9A-Fa-f]{6})\b", linha)
        if m:
            cores[m[1]] = m[2].upper()
        elif linha and not linha.startswith("#"):
            grade.append(linha)
    return grade, cores


def contornos(grade, ch):
    """Laços (listas de vértices) que delimitam as células cujo caractere
    está em `ch`. Furos saem com orientação oposta à do contorno externo."""
    dentro = {(x, y) for y, linha in enumerate(grade)
              for x, c in enumerate(linha) if c in ch}
    # Arestas de fronteira, no sentido horário da tela (y para baixo):
    # o interior fica sempre à direita de quem percorre a aresta.
    saidas = {}
    for x, y in dentro:
        lados = (((x, y), (x + 1, y), (x, y - 1)),
                 ((x + 1, y), (x + 1, y + 1), (x + 1, y)),
                 ((x + 1, y + 1), (x, y + 1), (x, y + 1)),
                 ((x, y + 1), (x, y), (x - 1, y)))
        for a, b, vizinho in lados:
            if vizinho not in dentro:
                saidas.setdefault(a, []).append(b)
    lacos = []
    while saidas:
        inicio = next(iter(saidas))
        laco, a, anterior = [inicio], inicio, None
        while True:
            opcoes = saidas[a]
            # Vértice com duas saídas (células que só se tocam na diagonal):
            # vira para a direita, o que separa os laços em vez de cruzá-los.
            b = opcoes[0] if len(opcoes) == 1 or anterior is None else \
                min(opcoes, key=lambda p: _giro(anterior, a, p))
            opcoes.remove(b)
            if not opcoes:
                del saidas[a]
            if b == inicio:
                break
            laco.append(b)
            anterior, a = a, b
        lacos.append(_sem_colineares(laco))
    return lacos


def _giro(p, q, r):
    """0 para curva à direita, 1 para seguir reto, 2 para curva à esquerda."""
    d1 = (q[0] - p[0], q[1] - p[1])
    d2 = (r[0] - q[0], r[1] - q[1])
    vetorial = d1[0] * d2[1] - d1[1] * d2[0]  # > 0: direita na tela
    return 0 if vetorial > 0 else (1 if vetorial == 0 else 2)


def _sem_colineares(laco):
    n = len(laco)
    return [laco[i] for i in range(n)
            if _giro(laco[i - 1], laco[i], laco[(i + 1) % n]) != 1]


def camadas(grade, cores, trocas=None):
    """[(cor, caracteres da cor, caracteres das camadas seguintes)], na
    ordem de pintura."""
    final = {c: (trocas or {}).get(c, cor) for c, cor in cores.items()}
    usados = [c for c in cores if c in set("".join(grade))]
    ordem = list(dict.fromkeys(final[c] for c in usados))
    proprios = ["".join(c for c in usados if final[c] == cor) for cor in ordem]
    return [(cor, proprios[i], "".join(proprios[i + 1:]))
            for i, cor in enumerate(ordem)]


def mascara(grade, proprios, acima):
    """Subcélulas (grade FINO vezes mais fina) pintadas por uma camada: as
    próprias células e uma faixa de 1/FINO de célula sob cada vizinha de uma
    camada seguinte. A faixa fecha a junta entre as duas cores (sem costura)
    sem levar a cor de baixo até a silhueta (sem franja)."""
    f = FINO
    m = set()
    for y, linha in enumerate(grade):
        for x, c in enumerate(linha):
            if c not in proprios:
                continue
            m |= {(x * f + i, y * f + j) for i in range(f) for j in range(f)}
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                vx, vy = x + dx, y + dy
                if 0 <= vy < len(grade) and 0 <= vx < len(grade[vy]) \
                        and grade[vy][vx] in acima:
                    # faixa no lado da vizinha que encosta nesta célula
                    i0 = 0 if dx > 0 else (f - 1 if dx < 0 else None)
                    j0 = 0 if dy > 0 else (f - 1 if dy < 0 else None)
                    for k in range(f):
                        i = i0 if i0 is not None else k
                        j = j0 if j0 is not None else k
                        m.add((vx * f + i, vy * f + j))
    return m


def svg(grade, cores, trocas=None):
    largura, altura = len(grade[0]), len(grade)
    paths = []
    for cor, proprios, acima in camadas(grade, cores, trocas):
        m = mascara(grade, proprios, acima)
        fina = ["".join("X" if (x, y) in m else "." for x in range(largura * FINO))
                for y in range(altura * FINO)]
        d = "".join("M" + "L".join(f"{x / FINO:g} {y / FINO:g}" for x, y in laco) + "Z"
                    for laco in contornos(fina, "X"))
        paths.append(f'<path fill="{cor}" fill-rule="evenodd" d="{d}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{largura}" height="{altura}" '
            f'viewBox="0 0 {largura} {altura}">\n'
            + "\n".join(paths) + "\n</svg>\n")


def png(grade, cores, escala, trocas=None):
    final = {c: (trocas or {}).get(c, cor) for c, cor in cores.items()}
    img = Image.new("RGBA", (len(grade[0]), len(grade)), (0, 0, 0, 0))
    for y, linha in enumerate(grade):
        for x, c in enumerate(linha):
            if c != ".":
                img.putpixel((x, y), ImageColor.getrgb(final[c]) + (255,))
    return img.resize((img.width * escala, img.height * escala),
                      Image.Resampling.NEAREST)


def recorte_icone(grade):
    """Só o quebra-cabeça: linhas acima do letreiro, sem bordas vazias."""
    fim = next(y for y, linha in enumerate(grade) if "L" in linha)
    linhas = [l for l in grade[:fim] if l.strip(".")]
    cols = [x for x in range(len(linhas[0])) if any(l[x] != "." for l in linhas)]
    return [l[cols[0]:cols[-1] + 1] for l in linhas]


def main():
    grade, cores = ler_mapa(AQUI / "logo.txt")
    variantes = {
        "logo-escuro": (grade, None),
        "logo-claro": (grade, {"L": NOITE}),
        "icone": (recorte_icone(grade), None),
    }
    (AQUI / "png").mkdir(exist_ok=True)
    for nome, (g, trocas) in variantes.items():
        caminho = AQUI / f"{nome}.svg"
        caminho.write_text(svg(g, cores, trocas))
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(AQUI / f"{nome}.pdf"),
                        str(caminho)], check=True)
        for escala in (1, 4, 8):
            png(g, cores, escala, trocas).save(AQUI / "png" / f"{nome}-{escala}x.png")


if __name__ == "__main__":
    main()
