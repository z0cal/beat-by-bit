"""
Robo do Beat by Bit: tela ST7789V numa ESP32, ligada ao PC por cabo USB.

O PC so manda comandos de 1 byte; a ESP32 (firmware em robo/src/main.cpp) cuida
da animacao. Nada aqui altera a FPGA nem o jogo: os eventos saem do mesmo pacote
que a interface ja le (acertos e erros da sessao, e o estado da FPGA).

  H  acerto            S  erro (nota perdida)
  C  fim da musica     N  volta ao neutro
"""

import os
import queue
import threading
import time

try:
    import serial

    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False

CMD_FELIZ, CMD_TRISTE, CMD_FESTA, CMD_NEUTRO = "H", "S", "C", "N"

# Estados da FPGA (iguais a STATE_* de interface.py)
_PLAY, _WIN, _LOSE, _NAME = 2, 4, 5, 8
_FIM = (_WIN, _LOSE, _NAME)


class RobotEvents:
    """Traduz a sequencia de pacotes em comandos para o robo."""

    def __init__(self):
        self._state = None
        self._hits = 0
        self._misses = 0

    def update(self, pkt):
        """Recebe um pacote (como parse_packet) e devolve a lista de comandos."""
        st = pkt["state"]
        hits = pkt["metrics"]["n_acertos"]  # acertos em botao (zera no COUNTDOWN)
        misses = pkt["misses"]  # notas perdidas (zera no COUNTDOWN)
        prev, out = self._state, []
        if prev is None:  # primeiro pacote: sincroniza o robo com o jogo
            out.append(CMD_FESTA if st in _FIM else CMD_NEUTRO)
        elif st == _PLAY and prev == _PLAY:
            if hits > self._hits:
                out.append(CMD_FELIZ)
            if misses > self._misses:
                out.append(CMD_TRISTE)
        elif st in (_WIN, _LOSE) and prev not in _FIM:
            out.append(CMD_FESTA)  # fim da musica, vitoria ou derrota
        elif st not in _FIM and prev in _FIM:
            out.append(CMD_NEUTRO)  # saiu da tela do nome
        self._state, self._hits, self._misses = st, hits, misses
        return out


# Conversores USB-serial comuns em placas ESP32 (CP210x, CH340, FTDI)
_USB_UART_VIDS = {0x10C4, 0x1A86, 0x0403}


def find_robot_port(fpga_port=None):
    """
    Porta da ESP32 quando nao foi informada: o primeiro conversor USB-serial que
    nao e a porta da FPGA. Devolve None se nao houver nenhum.
    """
    if not SERIAL_AVAILABLE:
        return None
    from serial.tools import list_ports

    def real(p):
        return os.path.realpath(p) if p and os.path.exists(p) else p

    skip = real(fpga_port)
    for info in sorted(list_ports.comports(), key=lambda i: i.device):
        if info.vid in _USB_UART_VIDS and real(info.device) != skip:
            return info.device
    return None


class RobotLink(threading.Thread):
    """
    Serial para a ESP32, em thread propria: abrir a porta ou escrever nunca trava
    o desenho a 60 fps, e qualquer falha so desliga o robo (o jogo segue).
    """

    def __init__(self, port, baud=115200, boot_wait=2.0):
        super().__init__(daemon=True)
        self.port, self.baud, self.boot_wait = port, baud, boot_wait
        self.connected = False
        self._q = queue.Queue()
        self._stop_evt = threading.Event()
        self.start()

    def send(self, cmd):
        if cmd:
            self._q.put(cmd)

    def close(self):
        self._stop_evt.set()

    def run(self):
        if not SERIAL_AVAILABLE:
            print("[robo] pyserial ausente: robo desligado")
            return
        try:
            ser = serial.Serial()
            ser.port, ser.baudrate, ser.write_timeout = self.port, self.baud, 0.2
            ser.dtr = False  # evita reiniciar a ESP32 ao abrir (nem sempre basta)
            ser.rts = False
            ser.open()
        except Exception as e:
            print(f"[robo] Nao foi possivel abrir {self.port}: {e}")
            return
        self.connected = True
        print(f"[robo] Conectado em {self.port} @ {self.baud} baud")

        # A ESP32 pode reiniciar ao abrir a porta: espera o boot e manda so o ultimo
        end = time.monotonic() + self.boot_wait
        while time.monotonic() < end and not self._stop_evt.is_set():
            time.sleep(0.05)
        last = None
        while True:
            try:
                last = self._q.get_nowait()
            except queue.Empty:
                break
        if last:
            self._write(ser, last)

        while not self._stop_evt.is_set() and self.connected:
            try:
                cmd = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._write(ser, cmd)
        try:
            ser.close()
        except Exception:
            pass

    def _write(self, ser, cmd):
        try:
            ser.write(cmd.encode("ascii"))
        except Exception as e:
            print(f"[robo] Erro na serial, robo desligado: {e}")
            self.connected = False
