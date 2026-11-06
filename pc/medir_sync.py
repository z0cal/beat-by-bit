"""
Mede a sincronia nota x som do Beat by Bit (prioridade numero 1 do projeto).

Uso:  uv run python pc/medir_sync.py [--segundos N] [--musicas 0,1,2,3] [--so-chart]

(a) Chart: para cada nota do .hex (bits 0-3; o gesto nao tem som), o instante em que ela
    chega ao alvo (quadro f + HIT_AGE contado do quadro 0 do PLAY) contra o onset mais
    proximo do MIDI (canais != 9) no instante em que o som correspondente SAI, segundo o
    modelo: o som do instante t da musica sai em ancora + t + CHART_LEAD_FRAMES/60.
    Mostra mediana e p90 do |erro| em ms (negativo = a nota chega ANTES do som).
(b) Jogo: roda o Renderer em --sim headless (SDL dummy), entra em cada musica, intercepta
    pygame.mixer.music.play e mede o que realmente foi pedido ao mixer: pos pedido, erro do
    pos contra o ideal no instante da chamada e o erro nota-som COMPLETO (chart + inicio
    real da musica, com o buffer de audio de 4096/44100 s), mais o tempo de quadro.
(c) Motor: confere, passo a passo no SimulationEngine, que a nota do chart[f] chega a
    age HIT_AGE no passo f + HIT_AGE depois do passo com frame_counter = 1.

Funciona em qualquer revisao (le CHART_LEAD_FRAMES ou o antigo AUDIO_LATENCY_FRAMES).
"""
import argparse
import os
import statistics
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mido
import pygame

import interface as I

LEAD = getattr(I, "CHART_LEAD_FRAMES", None) or getattr(I, "AUDIO_LATENCY_FRAMES")
BUF_S = getattr(I, "AUDIO_BUFFER_S", 4096 / 44100)
FPS = I.FPS


def onsets(midi_path):
    t, ons = 0.0, []
    for m in mido.MidiFile(midi_path):
        t += m.time
        if m.type == "note_on" and m.velocity > 0 and getattr(m, "channel", 0) != 9:
            ons.append(t)
    return sorted(set(ons))


def chart_notes(song):
    return [f for f, v in enumerate(int(x, 16) for x in open(song["hex"]).read().split()) if v & 0x0F]


def stats(errs_ms):
    a = sorted(abs(e) for e in errs_ms)
    return (statistics.median(errs_ms), a[int(0.9 * (len(a) - 1))], a[-1])


def erros_nota_som(song, shift_s):
    """erro (ms) = chegada da nota ao alvo - saida do som do onset mais proximo.
    shift_s: atraso real da saida do som da musica em relacao ao modelo (0 = ideal)."""
    ons = onsets(song["midi"])
    errs = []
    for f in chart_notes(song):
        chega = (f + I.HIT_AGE) / FPS  # desde o quadro 0 do PLAY
        sai = lambda t: t + LEAD / FPS + shift_s
        o = min(ons, key=lambda t: abs(sai(t) - chega))
        errs.append((chega - sai(o)) * 1000)
    return errs


def checa_motor(song_idx, n_notas=12):
    """(c) a nota do chart[f] chega a age HIT_AGE no passo (fc=1) + f + HIT_AGE?"""
    e = I.SimulationEngine()
    F = [False] * 5
    e.step([False] * 4 + [True])
    e.step(F)
    e.step([i == song_idx for i in range(4)] + [False])
    pkt = None
    while e.state != I.STATE_PLAY:
        pkt = e.step(F)
    e.misses = -10**6  # ninguem joga: sem isso, as notas perdidas levam a LOSE
    chart = e._chart
    quadros = [f for f, v in enumerate(chart) if v & 0x0F]
    limite = quadros[n_notas - 1] + I.HIT_AGE + 2
    esperado = {}
    for f in (f for f in quadros if f + I.HIT_AGE <= limite):
        esperado.setdefault(f + I.HIT_AGE, set()).update(t for t in range(4) if chart[f] >> t & 1)
    n, ok = 0, True  # n = passos desde o passo com frame_counter = 1 (inclusive 0)
    assert pkt["frame_counter"] == 1, pkt["frame_counter"]
    while n < limite:
        n += 1
        e.step(F)
        pegou = {t for t, tr in enumerate(e._ages) if I.SimulationEngine.HIT_AGE in tr}
        if pegou != esperado.get(n, set()):
            ok = False
    return ok, quadros


class Medidor:
    K = staticmethod(lambda k: pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="", scancode=0))

    def __init__(self):
        self.r = I.Renderer(sim_mode=True)
        self.calls = []
        orig = pygame.mixer.music.play

        def play(*a, **k):
            r = self.r
            info = getattr(r, "_play_info", None) or {}
            self.calls.append({
                "t": time.perf_counter(),
                "pos": info.get("pos", k.get("start", a[1] if len(a) > 1 else 0.0)),
                "fc": r.fpga_frame_counter, "steps": r._sim_steps, "loops": k.get("loops", 0),
                "ancora": info.get("anchor"),
            })
            return orig(*a, **k)

        pygame.mixer.music.play = play

    def frame(self, evs=()):
        r = self.r
        t0 = time.perf_counter()
        r._process_sim(list(evs))
        s = r.fpga_state
        r._update_music(s)
        r._update_session(s)
        if s == I.STATE_PLAY:
            r._draw_play(r.fpga_notes, r.fpga_score, r.fpga_misses, r.fpga_combo)
        elif s == I.STATE_COUNTDOWN:
            r._draw_countdown(r.fpga_countdown)
        elif s == I.STATE_SONG_SELECT:
            r._draw_song_select()
        else:
            r._draw_idle()
        r.screen.blit(r.vignette_surf, (0, 0))
        r._present()
        dt = time.perf_counter() - t0
        r.clock.tick(FPS)
        return dt, s

    def esperar(self, n):
        for _ in range(n):
            self.frame()

    def volta_ao_menu(self):
        r = self.r
        while r.fpga_state in (I.STATE_PLAY, I.STATE_COUNTDOWN):
            self.frame([self.K(pygame.K_SPACE)])
            self.esperar(2)
        if r.fpga_state == I.STATE_PAUSE:
            self.frame([self.K(pygame.K_d)])
        self.esperar(3)

    def musica(self, idx, segundos):
        r = self.r
        self.volta_ao_menu()
        if r.fpga_state == I.STATE_IDLE:
            self.frame([self.K(pygame.K_SPACE)])
        self.esperar(3)
        self.calls.clear()
        self.frame([self.K((pygame.K_d, pygame.K_f, pygame.K_j, pygame.K_k)[idx])])
        dts, anc = [], None
        t_end = time.perf_counter() + 3.0 + segundos + 2.0
        while time.perf_counter() < t_end:
            dt, s = self.frame()
            if s == I.STATE_PLAY:
                if anc is None and r.fpga_frame_counter >= 1:
                    # ancora IDEAL: instante ideal do passo que levou o contador a 1
                    anc = r._sim_t0 + (r._sim_steps - (r.fpga_frame_counter - 1)) / FPS
                if self.calls:
                    dts.append(dt)
                    if time.perf_counter() - self.calls[0]["t"] > segundos:
                        break
        return anc, dts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segundos", type=float, default=5.0, help="tempo de PLAY medido por musica")
    ap.add_argument("--musicas", default="0,1,2,3")
    ap.add_argument("--so-chart", action="store_true", help="so (a) e (c), sem rodar o Renderer")
    args = ap.parse_args()
    idxs = [int(x) for x in args.musicas.split(",")]
    songs = [s for s in I.SONGS if s["available"]]

    print(f"revisao: CHART_LEAD={LEAD} quadros ({LEAD / FPS * 1000:.1f} ms), buffer de audio "
          f"{BUF_S * 1000:.1f} ms, HIT_AGE={I.HIT_AGE}")
    print("\n(a) chart: erro nota-som no modelo ideal (negativo = nota ANTES do som), ms")
    print(f"{'musica':26s}{'notas':>6s}{'mediana':>9s}{'p90|e|':>8s}{'max|e|':>8s}")
    for i in idxs:
        e = erros_nota_som(songs[i], 0.0)
        m, p90, mx = stats(e)
        print(f"{songs[i]['title'][:25]:26s}{len(e):6d}{m:+9.1f}{p90:8.1f}{mx:8.1f}")

    print("\n(c) motor: nota do chart[f] em age HIT_AGE no passo f + HIT_AGE apos fc=1?")
    for i in idxs:
        ok, q = checa_motor(i)
        print(f"  {songs[i]['title'][:25]:26s}{'OK' if ok else 'DIVERGE'} (frames {q[:4]}...)")
    if args.so_chart:
        return

    print("\n(b) Renderer --sim headless")
    med = Medidor()
    print(f"{'musica':26s}{'pos pedido':>11s}{'erro pos':>10s}{'som/ideal':>10s}{'mediana':>9s}{'p90|e|':>8s}"
          f"{'quadro med':>11s}{'p95':>7s}{'max':>7s}")
    for i in idxs:
        anc, dts = med.musica(i, args.segundos)
        c = [x for x in med.calls if x["loops"] == 0]
        if not c or anc is None:
            print(f"  {songs[i]['title'][:25]:26s}play() nao chamado")
            continue
        c = c[0]
        # som da musica t=0 sai em T + buffer - pos ; ideal: ancora + LEAD/60
        shift = (c["t"] + BUF_S - c["pos"]) - (anc + LEAD / FPS)
        pos_ideal = (c["t"] - anc) - LEAD / FPS + BUF_S
        erro_pos = (c["pos"] - pos_ideal) * 1000  # >0: musica adiantada demais
        e = erros_nota_som(songs[i], shift)
        m, p90, _ = stats(e)
        q = sorted(dts)
        print(f"{songs[i]['title'][:25]:26s}{c['pos'] * 1000:9.1f}ms{erro_pos:+8.2f}ms{shift * 1000:+8.2f}ms"
              f"{m:+9.1f}{p90:8.1f}{statistics.median(q) * 1000:9.1f}ms{q[int(.95 * (len(q) - 1))] * 1000:6.1f}"
              f"{q[-1] * 1000:6.1f}")
    print("\nerro pos = pos pedido - pos ideal no instante da chamada (antes: pos=0, o erro e o atraso "
          "do inicio).\nsom/ideal = atraso do som de t=0 contra o modelo (+ = som tarde, a nota chega antes).")


if __name__ == "__main__":
    main()
