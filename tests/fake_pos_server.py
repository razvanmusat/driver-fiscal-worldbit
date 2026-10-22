"""Pornește un POS ECR fals pe un port virtual (pty) ca proces separat, pentru testele end-to-end din afara Python (ex. Dressbit).

    python3 -m tests.fake_pos_server

Prima linie de pe stdout: `PATH <calea portului>` (agentul îl deschide ca pe un port COM). Apoi, pe stdout, fiecare mesaj de plată/anulare/decontare primit:
`RECV <mesaj>`. De pe stdin citește comenzi, câte una pe linie: `mode approve|decline|silent|no_start|no_result`, `void_mode <la fel>|none`,
`decline_code <cod>`, `delay <secunde>`. Nu e un POS real: urmează doar documentul ECR Hypercom T7/FIP11.
"""
import sys
import threading
import time

from tests.fake_pos import FakeEcrPos


def main():
    pos = FakeEcrPos().start()
    print(f"PATH {pos.path}", flush=True)

    def raporteaza():
        vazute = 0
        while True:
            primite = [e[1] for e in list(pos.log) if e[0] == "recv"]
            for mesaj in primite[vazute:]:
                print(f"RECV {mesaj}", flush=True)
            vazute = len(primite)
            time.sleep(0.03)

    threading.Thread(target=raporteaza, daemon=True).start()
    for linie in sys.stdin:
        parti = linie.split()
        if len(parti) < 2:
            continue
        comanda, valoare = parti[0], parti[1]
        if comanda == "mode":
            pos.mode = valoare
        elif comanda == "void_mode":
            pos.void_mode = None if valoare == "none" else valoare
        elif comanda == "decline_code":
            pos.decline_code = valoare
        elif comanda == "delay":
            pos.delay_result = float(valoare)
        print(f"OK {linie.strip()}", flush=True)


if __name__ == "__main__":
    main()
