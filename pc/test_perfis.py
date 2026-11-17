"""
Testes dos perfis salvos na tela NOME: sugestoes, evolucao e render headless.

Uso:
  SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run python pc/test_perfis.py
  (BBB_SCREENSHOTS=<pasta> muda onde as capturas sao salvas)
"""
import csv
import os
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pygame  # noqa: E402
import interface as I  # noqa: E402

SCREENSHOT_DIR = os.environ.get(
    "BBB_SCREENSHOTS", os.path.join(tempfile.gettempdir(), "beat_by_bit_perfis"))

# (data_hora, pseudonimo, taxa_pct, notas, desvio, combo)
_LINHAS = [
    ("2026-11-01T09:00:00", "ANA", "60", "67", "70.0", "4"),
    ("2026-11-02T09:00:00", "ANA", "72", "67", "61.5", "7"),
    ("2026-11-02T10:00:00", "", "50", "67", "80.0", "3"),
    ("2026-11-03T09:00:00", "AN-", "90", "57", "40.0", "12"),
    ("2026-11-03T11:00:00", "BIA", "", "0", "", "0"),  # sessao sem notas
    ("2026-11-04T09:00:00", "ANA", "81", "57", "52.0", "11"),
    ("2026-11-05T09:00:00", "LEO", "88", "86", "45.0", "15"),
    ("2026-11-06T09:00:00", "ABC", "70", "86", "55.0", "9"),
]


def _grava_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=I.SESSION_FIELDS, restval="")
        w.writeheader()
        for when, nome, taxa, notas, desvio, combo in _LINHAS:
            w.writerow({"data_hora": when, "pseudonimo": nome, "musica": "Pokemon",
                        "taxa_pct": taxa, "notas": notas, "assinc_desvio_ms": desvio,
                        "maior_combo": combo})


def _rows():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "s.csv")
        _grava_csv(p)
        return I.SessionLog(p).rows()


def test_rows_arquivo_ausente():
    assert I.SessionLog("/nao/existe/s.csv").rows() == []
    assert I.SessionLog("/nao/existe/s.csv").previous("ANA") is None


def test_profile_index():
    idx = I.profile_index(_rows())
    assert set(idx) == {"ANA", "AN-", "BIA", "LEO", "ABC"}  # sem pseudonimo fica fora
    assert idx["ANA"] == {"n": 3, "last": "2026-11-04T09:00:00"}


def test_match_profiles():
    idx = I.profile_index(_rows())
    # "---": os mais recentes primeiro
    assert I.match_profiles(idx, "---") == ["ABC", "LEO", "ANA", "BIA", "AN-"]
    assert I.match_profiles(idx, "---", limit=2) == ["ABC", "LEO"]
    # '-' e curinga; ordem: a mais recente
    assert I.match_profiles(idx, "A--") == ["ABC", "ANA", "AN-"]
    assert I.match_profiles(idx, "AN-") == ["AN-", "ANA"]  # exato primeiro
    assert I.match_profiles(idx, "ANA") == ["ANA"]
    assert I.match_profiles(idx, "Z--") == []


def test_profile_history():
    rows = _rows()
    h = I.profile_history(rows, "ANA")
    assert [x["taxa"] for x in h] == [60.0, 72.0, 81.0]  # antiga -> recente
    assert h[-1]["desvio_ms"] == 52.0 and h[-1]["maior_combo"] == 11.0
    assert [x["taxa"] for x in I.profile_history(rows, "ANA", limit=2)] == [72.0, 81.0]
    assert I.profile_history(rows, "BIA") == []  # sem notas: fora do grafico
    assert I.profile_history(rows, None) == []
    assert I.short_date("2026-11-04T09:00:00") == "04/11"


def salvar_screenshots():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "s.csv")
        _grava_csv(path)
        r = I.Renderer(sim_mode=True, scale=1.0, history_path=path)
        r.fpga_score, r.fpga_misses, r._session_live = 9000, 3, True
        r.fpga_metrics = {
            "max_combo": 14, "n_acertos": 60, "soma_e": -20, "soma_abs": 120,
            "soma_e2": 300, "notas": [17, 17, 17, 16], "acertos": [16, 15, 15, 14],
            "movimentos": 40, "ativacoes_x2": 1,
        }
        for nome, cursor, arq in (("---", 0, "nome_sem_perfil.png"),
                                  ("A--", 1, "nome_sugestoes.png"),
                                  ("ZED", 2, "nome_perfil_novo.png"),
                                  ("ANA", 2, "nome_evolucao.png")):
            r._dash = None
            r.fpga_state = I.STATE_NAME
            r.fpga_nome, r.fpga_cursor = nome, cursor
            r._update_session(I.STATE_NAME)
            assert r._dash["profile"] == I.profile_of(nome)
            r._draw_name()
            p = os.path.join(SCREENSHOT_DIR, arq)
            pygame.image.save(r.screen, p)
            assert os.path.getsize(p) > 0
            out.append(p)
        # a sessao anterior do perfil continua vindo do CSV
        r._dash = None
        r.fpga_nome = "ANA"
        r._update_session(I.STATE_NAME)
        assert r._dash["prev"]["data_hora"] == "2026-11-04T09:00:00"
        assert "taxa" in r._dash["deltas"]
        r._dash = None  # nao grava a sessao no CSV temporario
    print(f"[OK] screenshots em {SCREENSHOT_DIR}:")
    for p in out:
        print(f"     - {p}")
    return out


def main():
    test_rows_arquivo_ausente()
    test_profile_index()
    test_match_profiles()
    test_profile_history()
    salvar_screenshots()
    print("=== TODOS OS TESTES DE PERFIS PASSARAM ===")


if __name__ == "__main__":
    main()
