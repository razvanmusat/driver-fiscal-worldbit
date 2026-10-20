"""Parola de admin pentru /setari: PBKDF2 (stdlib), sesiuni în memorie, blocare după încercări greșite."""
import hashlib
import hmac
import os
import secrets
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
    TTL_S = 1800

    def __init__(self):
        self._tokens = {}
        self._lock = threading.Lock()

    def create(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = time.time() + self.TTL_S
        return token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            expires = self._tokens.get(token)
            if expires is None or expires < time.time():
                self._tokens.pop(token, None)
                return False
            self._tokens[token] = time.time() + self.TTL_S
            return True

    def drop(self, token):
        with self._lock:
            self._tokens.pop(token, None)


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
