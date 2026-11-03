"""
Diagnostico do link serial FPGA -> PC.

Le a porta por alguns segundos em cada baud pedido e mostra:
  - quantos bytes chegaram (0 = fio/pino/reset/porta errada)
  - quantos pacotes validos (0x55 ... 0xAA) foram achados
      bytes > 0 e pacotes = 0 -> baud diferente entre FPGA e conversor
      pacotes ~60/s           -> serial OK; o problema esta em outro ponto
  - os estados da FSM vistos (aperte START durante a leitura: 0 -> 6)
Responde ACK a cada pacote valido, entao o LEDR3 deve acender.

Uso:
  uv run pc/diag_serial.py --port COM3
  uv run pc/diag_serial.py --port /dev/ttyUSB0 --baud 921600 115200
"""

import argparse
import os
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serial  # noqa: E402

from interface import ACK_BYTE, PACKET_SIZE, find_packet  # noqa: E402

STATE_NAMES = {0: "IDLE", 1: "COUNTDOWN", 2: "PLAY", 3: "PAUSE", 4: "WIN",
               5: "LOSE", 6: "SELECT", 7: "SETTINGS"}


def testa(port, baud, segundos):
    print(f"\n=== {port} @ {baud} baud, {segundos:.0f} s ===")
    try:
        ser = serial.Serial(port, baud, timeout=0.05)
    except Exception as e:
        print(f"  NAO ABRIU: {e}")
        return
    buf = bytearray()
    total, pacotes = 0, 0
    primeiros = bytearray()
    estados = []
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        data = ser.read(max(1, ser.in_waiting))
        if not data:
            continue
        total += len(data)
        if len(primeiros) < 64:
            primeiros += data[: 64 - len(primeiros)]
        buf += data
        while len(buf) >= PACKET_SIZE:
            pkt, usados = find_packet(buf)
            del buf[:usados]
            if pkt is None:
                break
            pacotes += 1
            ser.write(bytes([ACK_BYTE]))
            if not estados or estados[-1] != pkt["state"]:
                estados.append(pkt["state"])
    ser.close()

    print(f"  bytes recebidos : {total} ({total / segundos:.0f}/s; esperado ~{60 * PACKET_SIZE}/s)")
    print(f"  pacotes validos : {pacotes} ({pacotes / segundos:.1f}/s; esperado ~60/s)")
    print(f"  bytes 0x55      : {primeiros.count(0x55)} nos primeiros {len(primeiros)}")
    print(f"  primeiros bytes : {primeiros[:32].hex(' ')}")
    if estados:
        print("  estados vistos  : " + " -> ".join(STATE_NAMES.get(s, str(s)) for s in estados))
    if total == 0:
        print("  >> nada chegou: confira GND comum, TX da FPGA (PIN_N16) no RXD do conversor,")
        print("     reset solto (HEX0 aceso) e se a porta e a certa")
    elif pacotes == 0:
        print("  >> chegam bytes mas nenhum pacote: baud da FPGA diferente do conversor")
    elif pacotes / segundos < 50:
        print("  >> pacotes perdidos: fio longo/mau contato ou baud no limite")
    else:
        print("  >> serial OK")


def main():
    ap = argparse.ArgumentParser(description="Diagnostico do link serial FPGA -> PC")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, nargs="+", default=[921_600, 115_200])
    ap.add_argument("--segundos", type=float, default=4.0)
    args = ap.parse_args()
    print(f"Pacote de {PACKET_SIZE} bytes. Aperte START na placa durante a leitura.")
    for b in args.baud:
        testa(args.port, b, args.segundos)


if __name__ == "__main__":
    main()
