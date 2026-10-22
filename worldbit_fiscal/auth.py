"""Parola de admin pentru /setari: PBKDF2 (stdlib), sesiuni în memorie, blocare după încercări greșite."""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time

ITERATIONS = 200_000


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": iterations}


def verify_password(password, admin):
    if not admin:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(admin["salt"]),
                                 int(admin["iterations"]))
    return hmac.compare_digest(digest.hex(), admin["hash"])


class Sessions:
    """Sesiunile paginii de setări. Cu `path`, se păstrează într-un fișier (în dosarul de date, accesibil doar administratorilor), ca o repornire a agentului
    (de exemplu la actualizare) să nu te delogheze. Expirarea de 30 de minute rămâne."""
    TTL_S = 1800

    def __init__(self, path=None):
        self._tokens = {}
        self._lock = threading.Lock()
        self._path = path
        self._load()

    def _load(self):
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                stored = json.load(f)
            acum = time.time()
            self._tokens = {t: float(e) for t, e in stored.items() if float(e) > acum}
        except (OSError, ValueError, AttributeError):
            self._tokens = {}

    def _save(self):
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._path) or ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._tokens, f)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError:
            pass          # sesiunile rămân oricum în memorie

    def create(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = time.time() + self.TTL_S
            self._save()
        return token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            expires = self._tokens.get(token)
            if expires is None or expires < time.time():
                if self._tokens.pop(token, None) is not None:
                    self._save()
                return False
            self._tokens[token] = time.time() + self.TTL_S
            return True

    def drop(self, token):
        with self._lock:
            if self._tokens.pop(token, None) is not None:
                self._save()


class LoginThrottle:
    """După 5 parole greșite la rând, autentificarea e blocată 60 de secunde (global: agentul are un singur admin)."""
    MAX_FAILURES = 5
    LOCK_S = 60

    def __init__(self):
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def blocked(self):
        with self._lock:
            return time.time() < self._locked_until

    def failed(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.MAX_FAILURES:
                self._locked_until = time.time() + self.LOCK_S
                self._failures = 0

    def succeeded(self):
        with self._lock:
            self._failures = 0
