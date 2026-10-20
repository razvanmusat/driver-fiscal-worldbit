"""Starea locală a joburilor (sqlite3, stdlib). Supraviețuiește închiderii browserului și repornirii agentului."""
import contextlib
import json
import sqlite3
import threading
import time

_JSON_COLUMNS = ("state", "pos", "result", "error")
_COLUMNS = ("kind", "status", "attempt", "payload", "fiscal_job_id", "state", "pos", "result", "error",
            "last_resolve", "started_at", "updated_at")


class Store:
    def __init__(self, path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    external_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL,
                    fiscal_job_id TEXT,
                    state TEXT NOT NULL DEFAULT '{}',
                    pos TEXT,
                    result TEXT,
                    error TEXT,
                    last_resolve REAL NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL DEFAULT 0,
                    payment_key TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )""")
            # bazele create înainte de termenul de așteptare nu au coloana `started_at`
            coloane = {r[1] for r in self._db.execute("PRAGMA table_info(jobs)")}
            if "started_at" not in coloane:
                self._db.execute("ALTER TABLE jobs ADD COLUMN started_at REAL NOT NULL DEFAULT 0")
                self._db.execute("UPDATE jobs SET started_at = created_at")
            if "payment_key" not in coloane:
                self._db.execute("ALTER TABLE jobs ADD COLUMN payment_key TEXT")
            self._db.execute("CREATE INDEX IF NOT EXISTS jobs_payment_key ON jobs (payment_key)")
            self._db.commit()

    @contextlib.contextmanager
    def atomic(self):
        """Secțiune critică pentru operații în mai mulți pași (căutare + creare) pe aceeași plată."""
        with self._lock:
            yield

    def create(self, external_id, kind, payload, payment_key=None, status="received", state=None):
        """Întoarce (rând, creat). Dacă external_id există deja, întoarce rândul existent (idempotență)."""
        with self._lock:
            row = self.get(external_id)
            if row is not None:
                return row, False
            now = time.time()
            self._db.execute(
                "INSERT INTO jobs (external_id, kind, status, payload, state, payment_key, started_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (external_id, kind, status, payload, json.dumps(state or {}), payment_key, now, now, now))
            self._db.commit()
            return self.get(external_id), True

    def primary_for_payment_key(self, payment_key, exclude_external_id):
        """Jobul care chiar execută plata cu acest payment_key (nu o oglindă); cel mai vechi, dacă din greșeală sunt mai multe."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM jobs WHERE payment_key = ? AND kind = 'receipt' AND status != 'alias' AND external_id != ?"
                " ORDER BY created_at LIMIT 1", (payment_key, exclude_external_id)).fetchone()
        return self._decode(row)

    def get(self, external_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE external_id = ?", (external_id,)).fetchone()
        return self._decode(row)

    def update(self, external_id, **fields):
        for key in fields:
            if key not in _COLUMNS:
                raise KeyError(key)
        fields["updated_at"] = time.time()
        encoded = {k: (json.dumps(v, ensure_ascii=False) if k in _JSON_COLUMNS and v is not None else v)
                   for k, v in fields.items()}
        assignments = ", ".join(f"{k} = ?" for k in encoded)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {assignments} WHERE external_id = ?",
                             (*encoded.values(), external_id))
            self._db.commit()

    def job_id_in_use(self, fiscal_job_id, exclude_external_id):
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM jobs WHERE fiscal_job_id = ? AND external_id != ?",
                (fiscal_job_id, exclude_external_id)).fetchone()
        return row is not None

    def unfinished(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status IN ('received', 'running')").fetchall()
        return [self._decode(r) for r in rows]

    def close(self):
        with self._lock:
            self._db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        out = dict(row)
        for key in _JSON_COLUMNS:
            out[key] = json.loads(out[key]) if out[key] is not None else None
        if out["state"] is None:
            out["state"] = {}
        return out
