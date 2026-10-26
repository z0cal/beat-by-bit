from pathlib import Path

import gera_logo as g

MAPA = Path(__file__).with_name("logo.txt")


def area_assinada(laco):
    return sum(x0 * y1 - x1 * y0
               for (x0, y0), (x1, y1) in zip(laco, laco[1:] + laco[:1])) / 2


def area(grade, ch):
    return abs(sum(area_assinada(l) for l in g.contornos(grade, ch)))


def test_mapa_retangular():
    grade, _ = g.ler_mapa(MAPA)
    assert len(grade) == 55
    assert {len(linha) for linha in grade} == {63}


def test_so_cores_conhecidas():
    grade, cores = g.ler_mapa(MAPA)
    usados = set("".join(grade)) - {"."}
    assert usados <= set(cores)


def test_contorno_com_furo():
    assert area(["XXX", "X.X", "XXX"], "X") == 8
    assert len(g.contornos(["XXX", "X.X", "XXX"], "X")) == 2


def test_celulas_na_diagonal():
    assert area(["X.", ".X"], "X") == 2


def test_area_preservada():
    grade, cores = g.ler_mapa(MAPA)
    for ch in cores:
        assert area(grade, ch) == "".join(grade).count(ch), ch


def test_svg_sem_rect():
    grade, cores = g.ler_mapa(MAPA)
    assert "<rect" not in g.svg(grade, cores)


def test_uma_path_por_cor():
    grade, cores = g.ler_mapa(MAPA)
    s = g.svg(grade, cores)
    usadas = {cores[c] for c in set("".join(grade)) - {"."}}
    assert s.count("<path") == len(usadas)
    for cor in usadas:
        assert s.count(f'fill="{cor}"') == 1


def test_camadas_particionam_as_cores():
    grade, cores = g.ler_mapa(MAPA)
    camadas = g.camadas(grade, cores)
    proprios = [p for _, p, _ in camadas]
    assert sorted("".join(proprios)) == sorted(set("".join(grade)) - {"."})
    for i, (_, _, acima) in enumerate(camadas):
        assert set(acima) == set("".join(proprios[i + 1:]))


def test_faixa_sob_a_vizinha_de_cima():
    # Entre duas cores vizinhas, a de baixo avança uma faixa fina sob a de
    # cima: o fundo não aparece pela junta (costura). Só uma faixa, e não a
    # célula inteira, para a cor de baixo não vazar pela silhueta (franja).
    f = g.FINO
    m = g.mascara(["AB"], "A", "B")
    assert {(x, y) for x in range(f) for y in range(f)} <= m
    assert {(f, y) for y in range(f)} <= m
    assert not any(x > f for x, _ in m)


def test_mascara_dentro_da_silhueta():
    grade, cores = g.ler_mapa(MAPA)
    f = g.FINO
    silhueta = {(x * f + i, y * f + j)
                for y, linha in enumerate(grade) for x, c in enumerate(linha)
                if c != "." for i in range(f) for j in range(f)}
    for _, proprios, acima in g.camadas(grade, cores):
        assert g.mascara(grade, proprios, acima) <= silhueta


def test_claro_troca_so_letreiro():
    grade, cores = g.ler_mapa(MAPA)
    s = g.svg(grade, cores, trocas={"L": "#0F172A"})
    assert 'fill="#0F172A"' in s
    assert 'fill="#F8F8F8"' in s  # a nota continua branca


def test_png_dimensoes():
    grade, cores = g.ler_mapa(MAPA)
    assert g.png(grade, cores, 8).size == (504, 440)


def test_icone_46x39():
    grade, _ = g.ler_mapa(MAPA)
    icone = g.recorte_icone(grade)
    assert len(icone) == 39
    assert {len(linha) for linha in icone} == {46}
    assert "L" not in "".join(icone)
