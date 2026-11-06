"""
Testes automatizados da barra de energia, multiplicador x2, CSV e renderizacao headless.

Uso:
  SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run python pc/test_energia.py
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

SCREENSHOT_DIR = (
    "/tmp/claude-1000/-home-zocal-labdig-beat-by-bit/"
    "a28844d6-8bf6-4b57-820b-5b44837e0806/scratchpad/energia"
)


def _monta_pacote_conhecido():
    pkt = bytearray(I.PACKET_SIZE)
    pkt[0] = I.PKT_HEADER
    # state = PLAY (2), eq = [1, 2, 3, 0]
    pkt[1] = (2 << 6) | (1 << 4) | I.STATE_PLAY
    pkt[2] = (0 << 6) | (3 << 4) | 0
    for i in range(3, 15):
        pkt[i] = I.EMPTY_SLOT
    pkt[3] = I.HIT_AGE  # trilha 0, slot 0 em HIT_Y
    pkt[6] = 65         # trilha 1, slot 0 no meio da pista
    # score = 4660 (0x1234), misses = 2, combo = 7
    pkt[15], pkt[16] = 0x12, 0x34
    pkt[17] = 2
    pkt[18] = 7
    # song_id = 2, frame_counter = 0x0512 (1298)
    pkt[19] = (2 << 6) | 0x05
    pkt[20] = 0x12
    # metricas: max_combo = 12, n_acertos = 15, soma_e = -5 (complemento de 2 em 24b)
    pkt[21] = 12
    pkt[22], pkt[23] = 0x00, 15
    soma_e_u24 = (-5) & 0xFFFFFF
    pkt[24], pkt[25], pkt[26] = (soma_e_u24 >> 16) & 0xFF, (soma_e_u24 >> 8) & 0xFF, soma_e_u24 & 0xFF
    pkt[27], pkt[28], pkt[29] = 0, 0, 9
    pkt[30], pkt[31], pkt[32] = 0, 0, 13
    # notas/acertos por trilha: (5, 4), (4, 4), (4, 4), (3, 3)
    for t, (nt, ac) in enumerate(((5, 4), (4, 4), (4, 4), (3, 3))):
        v = (nt << 12) | ac
        pkt[33 + 3 * t] = (v >> 16) & 0xFF
        pkt[34 + 3 * t] = (v >> 8) & 0xFF
        pkt[35 + 3 * t] = v & 0xFF
    # nome = "LEO" (L=12, E=5, O=15), cursor = 1
    pkt[45] = (1 << 6) | 12
    pkt[46] = 5
    pkt[47] = 15
    # bytes da Semana 3: dist=18 cm, energia=191, x2_ativo=1, ativacoes=5, movimentos=300 (0x012C)
    pkt[48] = 18
    pkt[49] = 191
    pkt[50] = (1 << 7) | 5
    pkt[51] = 0x01
    pkt[52] = (6 << 5) | (1 << 4) | (0 << 3) | (1 << 2) | 1
    pkt[53] = (1 << 2) | 0
    pkt[54] = 0x2C
    pkt[55] = I.PKT_FOOTER
    return bytes(pkt)


def _entra_em_play(sim, song_idx=0):
    falso = [False] * 5
    if sim.state != I.STATE_IDLE:
        sim.state = I.STATE_IDLE
    sim.step([False, False, False, False, True])  # IDLE -> SELECT
    sim.step(falso)
    sim.step([i == song_idx for i in range(4)] + [False])  # SELECT -> COUNTDOWN
    while sim.state != I.STATE_PLAY:
        sim.step(falso)
    sim.misses = -10**6  # evita encerrar por misses durante ensaios longos


def test_parse_packet_56_bytes():
    raw = b"\x00\xFF\x55" + _monta_pacote_conhecido() + b"\xAA"
    p, usados = I.find_packet(raw)
    assert p is not None, "Pacote valido nao foi encontrado"
    assert usados == 3 + I.PACKET_SIZE
    assert p["state"] == I.STATE_PLAY
    assert p["eq"] == [1, 2, 3, 0]
    assert p["notes"][0] == [I.HIT_Y]
    assert p["score"] == 0x1234
    assert p["misses"] == 2
    assert p["combo"] == 7
    assert p["song_id"] == 2
    assert p["frame_counter"] == 0x0512
    assert p["nome"] == "LEO" and p["cursor"] == 1
    assert p["dist_cm"] == 18
    assert p["energia"] == 191
    assert p["x2_ativo"] is True
    assert p["ativacoes"] == 5
    assert p["movimentos"] == 300
    assert p["modo_met"] == 1
    assert p["sensorial"] is True
    assert p["sem_som"] is False
    assert p["pagina"] == 1
    assert p["progresso"] == 6
    assert p["met_nivel"] == 0 and p["met_lado"] == 1
    m = p["metrics"]
    assert m["max_combo"] == 12
    assert m["n_acertos"] == 15
    assert m["soma_e"] == -5
    assert m["movimentos"] == 300
    assert m["ativacoes_x2"] == 5
    print("[OK] parse_packet (56 bytes): dist=18, energia=191, x2=1, ativ=5, mov=300")


def test_sim_enchimento_g_e_h():
    sim = I.SimulationEngine()
    falso = [False] * 5

    # 1. Tecla G (mao parada a 15 cm): ~15,3 s (918 quadros), 0 movimentos
    _entra_em_play(sim, 0)
    quadros_g = 0
    pkt = None
    while quadros_g < 1200:
        pkt = sim.step(falso, mao=True, mov=False)
        quadros_g += 1
        if pkt["x2_ativo"]:
            break
    tempo_g = quadros_g / I.FPS
    assert pkt["x2_ativo"] is True, "x2 nao ativou com G"
    assert pkt["energia"] == 255
    assert pkt["ativacoes"] == 1
    assert pkt["movimentos"] == 0, f"Mao parada contou movimento: {pkt['movimentos']}"
    assert pkt["dist_cm"] == 15
    assert 14.5 <= tempo_g <= 16.0, f"Tempo G fora de ~15 s: {tempo_g:.2f} s"

    # Soltar G e H deve emitir dist_cm = 0 (sem eco) na proxima leitura
    for _ in range(5):
        pkt_solto = sim.step(falso, mao=False, mov=False)
    assert pkt_solto["dist_cm"] == 0, f"Soltar teclas devia dar sem eco (0), deu {pkt_solto['dist_cm']}"

    # 2. Nova partida zera no COUNTDOWN; tecla H (vai-e-vem 10 <-> 20 cm): ~5,17 s
    _entra_em_play(sim, 0)
    assert sim._energia == 0 and sim._movimentos == 0 and sim._ativacoes == 0
    quadros_h = 0
    dists_vistas = set()
    while quadros_h < 600:
        pkt = sim.step(falso, mao=False, mov=True)
        quadros_h += 1
        if pkt["dist_cm"] > 0:
            dists_vistas.add(pkt["dist_cm"])
        if pkt["x2_ativo"]:
            break
    tempo_h = quadros_h / I.FPS
    assert pkt["x2_ativo"] is True, "x2 nao ativou com H"
    assert dists_vistas == {10, 20}, f"H devia alternar 10 e 20 cm, viu {dists_vistas}"
    assert pkt["energia"] == 255
    assert pkt["ativacoes"] == 1
    assert pkt["movimentos"] == 85, f"Esperado 85 movimentos ate encher, deu {pkt['movimentos']}"
    assert 4.8 <= tempo_h <= 5.5, f"Tempo H fora de ~5 s: {tempo_h:.2f} s"
    print(f"[OK] enchimento --sim: G={tempo_g:.2f}s ({quadros_g} q, mov=0), "
          f"H={tempo_h:.2f}s ({quadros_h} q, mov={pkt['movimentos']})")


def test_sim_x2_duracao_esvaziamento_e_pausa():
    sim = I.SimulationEngine()
    falso = [False] * 5
    _entra_em_play(sim, 0)

    # Enche com H ate ligar x2
    while True:
        pkt = sim.step(falso, mao=False, mov=True)
        if pkt["x2_ativo"]:
            break
    assert pkt["energia"] == 255

    # Avanca 240 quadros (4,0 s) ainda mantendo mao parada: nao pode reencher
    for _ in range(240):
        pkt = sim.step(falso, mao=True, mov=False)
    assert pkt["x2_ativo"] is True
    assert pkt["energia"] == 127, f"Metade do x2 devia ser 127, deu {pkt['energia']}"

    # Pausa no meio do x2: congela esvaziamento e movimentos
    mov_antes = pkt["movimentos"]
    sim.step([False, False, False, False, True], mao=False, mov=True)  # PLAY -> PAUSE
    assert sim.state == I.STATE_PAUSE
    for _ in range(60):
        pkt_pausa = sim.step(falso, mao=False, mov=True)
    assert pkt_pausa["x2_ativo"] is True and pkt_pausa["energia"] == 127
    assert pkt_pausa["movimentos"] == mov_antes
    sim.step([False, False, False, False, True])  # PAUSE -> PLAY (ja roda 1 quadro de PLAY: 241o)
    assert sim.state == I.STATE_PLAY

    # Faltam 239 quadros: apos +238 (479 no total) ainda ativo; no +1 (480 = 8,0 s) desliga e zera
    for _ in range(238):
        pkt = sim.step(falso, mao=True, mov=False)
    assert pkt["x2_ativo"] is True and pkt["energia"] == 0
    pkt_fim = sim.step(falso, mao=True, mov=False)
    assert pkt_fim["x2_ativo"] is False and pkt_fim["energia"] == 0
    assert pkt_fim["ativacoes"] == 1

    # Volta a encher normalmente depois que o x2 termina
    for _ in range(10):
        pkt_pos = sim.step(falso, mao=True, mov=False)
    assert pkt_pos["energia"] >= 2 and pkt_pos["x2_ativo"] is False
    print("[OK] duracao do x2: 480 quadros (8,00 s), esvaziamento linear, congela em PAUSE e retoma")


def _joga_acertando_notas(usar_h, max_quadros=900):
    sim = I.SimulationEngine()
    _entra_em_play(sim, 0)
    acertos_x1, acertos_x2 = 0, 0
    for _ in range(max_quadros):
        # Aperta o botao de qualquer trilha que tenha nota exatamente em HIT_AGE - 1
        # (no step atual _advance_ages leva a nota para HIT_AGE antes de _process_buttons)
        btn = [any(age == I.HIT_AGE - 1 for age in sim._ages[t]) for t in range(4)] + [False]
        estava_x2 = sim._x2_ativo
        sc_antes = sim.score
        sim.step(btn, mao=False, mov=usar_h)
        ganho = (sim.score - sc_antes) & 0xFFFF
        if ganho > 0:
            combo_ant = sim.combo - 1
            esperado = (2 if estava_x2 else 1) * (100 + combo_ant)
            assert ganho == esperado, f"Ganho {ganho} != esperado {esperado} (x2={estava_x2})"
            if estava_x2:
                acertos_x2 += 1
            else:
                acertos_x1 += 1
    return sim.score, acertos_x1, acertos_x2


def test_sim_score_dobrado_no_x2():
    score_sem, n1_sem, n2_sem = _joga_acertando_notas(usar_h=False, max_quadros=800)
    score_com, n1_com, n2_com = _joga_acertando_notas(usar_h=True, max_quadros=800)
    assert n2_sem == 0 and n1_sem > 0
    assert n2_com >= 5, f"Poucos acertos durante x2: {n2_com}"
    assert score_com > score_sem
    print(f"[OK] score dobrado no x2: sem sensor={score_sem} ({n1_sem} notas), "
          f"com H={score_com} ({n1_com} normais + {n2_com} em x2)")


def test_csv_migracao_aditiva():
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "sessoes.csv")
        campos_antigos = [
            "data_hora", "pseudonimo", "musica", "song_id", "resultado", "score",
            "erros", "acertos", "notas", "taxa_pct", "gestos_apresentados", "gestos_acertados",
        ]
        with open(caminho, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos_antigos)
            w.writeheader()
            w.writerow({
                "data_hora": "2026-11-05T10:00:00", "pseudonimo": "ANA",
                "musica": "Concerning Hobbits", "song_id": 0, "resultado": "vitoria",
                "score": 4500, "erros": 1, "acertos": 40, "notas": 42, "taxa_pct": 95,
                "gestos_apresentados": 4, "gestos_acertados": 3,
            })

        log = I.SessionLog(caminho)
        ant = log.previous("ANA")
        assert ant is not None and ant["gestos_acertados"] == "3"

        met = I.empty_metrics()
        met["n_acertos"] = 50
        met["notas"] = [15, 13, 12, 12]
        met["acertos"] = [15, 12, 12, 11]
        met["max_combo"] = 28
        met["movimentos"] = 94
        met["ativacoes_x2"] = 2
        nova = I.session_row(met, "ANA", 0, True, 12400, 2, when="2026-11-05T22:00:00")
        ok, _ = log.append(nova)
        assert ok

        with open(caminho, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            colunas = list(rd.fieldnames)
            linhas = list(rd)

        assert "gestos_apresentados" in colunas and "gestos_acertados" in colunas
        assert "movimentos" in colunas and "ativacoes_x2" in colunas
        assert len(linhas) == 2
        assert linhas[0]["gestos_apresentados"] == "4" and linhas[0]["gestos_acertados"] == "3"
        assert linhas[1]["movimentos"] == "94" and linhas[1]["ativacoes_x2"] == "2"
    print("[OK] CSV: migracao aditiva preserva colunas antigas e grava movimentos/ativacoes_x2")


def salvar_screenshots():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        r = I.Renderer(sim_mode=True, scale=1.0, history_path=os.path.join(tmp, "s.csv"))

        notas_demo = [
            [I. _py(190), I._py(430)],
            [I._py(280)],
            [I._py(140), I._py(360)],
            [I._py(240), I._py(490)],
        ]

        # 1. Play com a barra enchendo (com movimento na faixa)
        r.fpga_state = I.STATE_PLAY
        r.fpga_sensorial = False
        r.fpga_modo_met = 1
        r.fpga_met_nivel = 0
        r.fpga_met_lado = 1
        r.fpga_frame_counter = 850
        r.fpga_dist = 18
        r.fpga_energia = 162
        r.fpga_x2_ativo = False
        r.fpga_ativacoes = 0
        r.fpga_movimentos = 48
        r._mov_boost = 12
        r.fpga_score = 2450
        r.fpga_misses = 1
        r.fpga_combo = 14
        r._hit_effects.clear()
        r._draw_play(notas_demo, r.fpga_score, r.fpga_misses, r.fpga_combo)
        r.screen.blit(r.vignette_surf, (0, 0))
        p1 = os.path.join(SCREENSHOT_DIR, "play_enchendo.png")
        pygame.image.save(r.screen, p1)

        # 2. Play com x2 ativo (trilhas douradas + selo x2 + burst)
        r.fpga_energia = 198  # ~6,2 s restantes
        r.fpga_x2_ativo = True
        r.fpga_ativacoes = 1
        r.fpga_movimentos = 85
        r._mov_boost = 0
        r.fpga_dist = 15
        r.fpga_score = 6820
        r.fpga_combo = 24
        r._hit_effects.clear()
        r._spawn_hit_effect(0, I.TRACK_X[0], I.HIT_Y)
        r._draw_play(notas_demo, r.fpga_score, r.fpga_misses, r.fpga_combo)
        r.screen.blit(r.vignette_surf, (0, 0))
        p2 = os.path.join(SCREENSHOT_DIR, "play_x2_ativo.png")
        pygame.image.save(r.screen, p2)

        # 3. Versao calma no perfil sensorial (sem brilho/particulas, so cor e barra)
        r.fpga_sensorial = True
        r.fpga_energia = 175
        r.fpga_x2_ativo = True
        r.fpga_dist = 15
        r.fpga_score = 7240
        r.fpga_combo = 25
        r._hit_effects.clear()
        r._spawn_hit_effect(1, I.TRACK_X[1], I.HIT_Y)
        r._draw_play(notas_demo, r.fpga_score, r.fpga_misses, r.fpga_combo)
        r.screen.blit(r.vignette_surf, (0, 0))
        p3 = os.path.join(SCREENSHOT_DIR, "play_sensorial.png")
        pygame.image.save(r.screen, p3)

        # 4. Dashboard de fim de sessao com movimentos e ativacoes do x2
        r.fpga_sensorial = False
        r.fpga_state = I.STATE_WIN
        r.fpga_nome = "LEO"
        r.fpga_cursor = 2
        r.fpga_score = 14860
        r.fpga_misses = 2
        r._session_live = True
        r.fpga_metrics = {
            "max_combo": 34,
            "n_acertos": 72,
            "soma_e": -18,
            "soma_abs": 96,
            "soma_e2": 210,
            "notas": [19, 18, 19, 18],
            "acertos": [19, 17, 18, 18],
            "movimentos": 142,
            "ativacoes_x2": 2,
        }
        r._dash = None
        r._update_session(I.STATE_WIN)
        r._draw_dashboard()
        r.screen.blit(r.vignette_surf, (0, 0))
        p4 = os.path.join(SCREENSHOT_DIR, "dashboard.png")
        pygame.image.save(r.screen, p4)

    for p in (p1, p2, p3, p4):
        assert os.path.exists(p) and os.path.getsize(p) > 0, f"Screenshot vazia: {p}"
    print(f"[OK] screenshots salvas em {SCREENSHOT_DIR}:")
    for p in (p1, p2, p3, p4):
        print(f"     - {p}")
    return [p1, p2, p3, p4]


def test_screenshots_headless():
    caminhos = salvar_screenshots()
    assert len(caminhos) == 4


def main():
    test_parse_packet_56_bytes()
    test_sim_enchimento_g_e_h()
    test_sim_x2_duracao_esvaziamento_e_pausa()
    test_sim_score_dobrado_no_x2()
    test_csv_migracao_aditiva()
    salvar_screenshots()
    print("=== TODOS OS TESTES DE ENERGIA PASSARAM ===")


if __name__ == "__main__":
    main()
