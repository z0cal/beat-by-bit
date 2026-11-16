"""Testes do robo sem hardware: python pc/test_robo.py (ou pytest)."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import robo  # noqa: E402


def pkt(state, hits=0, misses=0):
    return {"state": state, "misses": misses, "metrics": {"n_acertos": hits}}


def run(seq):
    ev = robo.RobotEvents()
    return [ev.update(p) for p in seq]


def test_acerto_e_erro_em_play():
    out = run([pkt(2), pkt(2, hits=1), pkt(2, hits=1), pkt(2, hits=1, misses=1)])
    assert out == [["N"], ["H"], [], ["S"]]


def test_so_conta_em_play():
    # contadores zeram no COUNTDOWN: nada de reacao fora de PLAY->PLAY
    out = run([pkt(2, hits=5, misses=3), pkt(3, hits=5, misses=3), pkt(1), pkt(2)])
    assert out[1:] == [[], [], []]


def test_fim_independe_do_resultado():
    for fim in (4, 5):
        out = run([pkt(2), pkt(fim), pkt(8), pkt(0)])
        assert out == [["N"], ["C"], [], ["N"]], (fim, out)


def test_sincroniza_ao_abrir():
    assert run([pkt(0)])[0] == ["N"]
    assert run([pkt(4)])[0] == ["C"]
    assert run([pkt(8)])[0] == ["C"]


def test_acerto_e_erro_no_mesmo_pacote():
    assert run([pkt(2), pkt(2, hits=1, misses=1)])[1] == ["H", "S"]


class FakeSerial:
    opened = []

    def __init__(self):
        self.written = bytearray()
        self.dtr = self.rts = True
        FakeSerial.opened.append(self)

    def open(self):
        pass

    def write(self, b):
        self.written += b

    def close(self):
        pass


class FakeModule:
    Serial = FakeSerial


def test_link_envia_comandos_e_ignora_boot():
    robo.serial, robo.SERIAL_AVAILABLE = FakeModule, True
    FakeSerial.opened.clear()
    link = robo.RobotLink("COMX", boot_wait=0.3)
    for c in "HSH":  # chegam durante o boot: so o ultimo vale
        link.send(c)
    time.sleep(0.6)
    link.send("C")
    time.sleep(0.5)
    link.close()
    time.sleep(0.4)
    ser = FakeSerial.opened[0]
    assert ser.dtr is False and ser.rts is False
    assert bytes(ser.written) == b"HC", ser.written


def test_link_sem_porta_nao_derruba():
    class Broken:
        class Serial:
            def __init__(self):
                pass

            def open(self):
                raise OSError("sem porta")

    robo.serial, robo.SERIAL_AVAILABLE = Broken, True
    link = robo.RobotLink("COMX", boot_wait=0)
    time.sleep(0.2)
    link.send("H")  # nao pode lancar
    assert link.connected is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
