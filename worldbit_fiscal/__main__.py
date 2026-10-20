"""python -m worldbit_fiscal run | init"""
import argparse
import getpass
import logging
import logging.handlers
import os
import sys
import uuid

from . import auth, config as config_mod
from .drivers import DRIVERS
from .runner import Runner
from .server import App, make_server
from .state import Store


def _setup_logging(home):
    os.makedirs(home, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(os.path.join(home, "driver.log"), maxBytes=2_000_000,
                                                     backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


def cmd_run(args):
    home = config_mod.home_dir()
    _setup_logging(home)
    cfg = config_mod.load(home)
    if not cfg["admin"]:
        print("Agentul nu e inițializat. Rulează: python -m worldbit_fiscal init", file=sys.stderr)
        return 1
    if not cfg.get("agent_id"):
        cfg["agent_id"] = uuid.uuid4().hex   # identitate stabilă a acestui calculator; se generează o singură dată
        config_mod.save(cfg, home)
    store = Store(os.path.join(home, "jobs.sqlite3"))
    runner = Runner(cfg, store)
    runner.recover()
    app = App(cfg, runner, home)
    server = make_server(app)
    logging.getLogger("worldbit_fiscal").info(
        "Driver fiscal Worldbit pornit: driver=%s, 127.0.0.1:%s", cfg["driver"], cfg["port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_init(args):
    """Alegerea driverului (și a parolei de admin) se face aici, la instalare."""
    home = config_mod.home_dir()
    cfg = config_mod.load(home)
    cfg["driver"] = args.driver
    cfg["port"] = args.port
    if args.origin:
        cfg["allowed_origins"] = list(args.origin)
    password = os.environ.get("WORLDBIT_ADMIN_PASSWORD") or getpass.getpass("Parola de admin pentru /setari: ")
    if len(password) < 8:
        print("Parola trebuie să aibă minim 8 caractere.", file=sys.stderr)
        return 1
    cfg["admin"] = auth.hash_password(password)
    if not cfg.get("agent_id"):
        cfg["agent_id"] = uuid.uuid4().hex
    config_mod.save(cfg, home)
    print(f"Configurație scrisă în {config_mod.config_path(home)}")
    return 0


def cmd_chrome_policy(args):
    """Politica Chrome care pre-aprobă originea aplicației (fără prompt „Access other apps and services on this device"). Doar pe Windows, ca administrator."""
    from . import chrome_policy
    if sys.platform != "win32":
        print("Politica Chrome se scrie doar pe Windows.", file=sys.stderr)
        return 2
    try:
        registru = chrome_policy.RegistruWindows()
        if args.actiune == "add":
            rezultat = chrome_policy.adauga_origine(registru, args.origin)
        elif args.actiune == "remove":
            rezultat = chrome_policy.scoate_origine(registru, args.origin)
        else:
            rezultat = chrome_policy.stare(registru, args.origin)
    except chrome_policy.OrigineInvalida as e:
        print(str(e), file=sys.stderr)
        return 1
    except PermissionError:
        print("Lipsesc drepturile de administrator.", file=sys.stderr)
        return 1
    for politica, valoare in rezultat.items():
        print(f"{politica}: {valoare}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="worldbit_fiscal")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="pornește agentul")
    init = sub.add_parser("init", help="alege driverul și setează parola de admin (la instalare)")
    init.add_argument("--driver", choices=sorted(DRIVERS), required=True)
    init.add_argument("--port", type=int, default=config_mod.DEFAULT_PORT)
    init.add_argument("--origin", action="append", help="originea aplicației web permise (repetabil)")
    politica = sub.add_parser("chrome-policy", help="pre-aprobă originea aplicației în Chrome și Edge (Windows, administrator)")
    politica.add_argument("actiune", choices=["add", "remove", "status"])
    politica.add_argument("--origin", required=True, help="originea aplicației, ex. https://test.worldbit.ro")
    args = parser.parse_args(argv)
    return {"run": cmd_run, "init": cmd_init, "chrome-policy": cmd_chrome_policy}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
