"""Pornește un server fals de driver ca proces separat (pentru testele end-to-end din afara Python, ex. Dressbit).

    python3 -m tests.fake_driver_server fisco|fiscool

Scrie pe prima linie `PORT <n>`, apoi servește până e oprit. Starea și comportamentul se citesc/schimbă prin
GET /__state și POST /__control (vezi tests/fakes.py). Nu e driver real: urmează doar documentația Fisco / FisCool.
"""
import sys
import threading

from tests.fakes import FakeFiscool, FakeFisco


def main():
    tip = sys.argv[1] if len(sys.argv) > 1 else ""
    if tip not in ("fisco", "fiscool"):
        print("folosire: python3 -m tests.fake_driver_server fisco|fiscool", file=sys.stderr)
        return 2
    server = (FakeFisco() if tip == "fisco" else FakeFiscool()).start()
    print(f"PORT {server.port}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    sys.exit(main())
