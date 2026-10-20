"""Erorile pe care le pot ridica driverele. Runner-ul le transformă în starea jobului.

Regula din Sellbit se păstrează: se retrimite DOAR cu dovadă negativă. De aceea fiecare eroare spune
explicit dacă o retrimitere e sigură (`retry_safe`) sau nu.

Pe lângă mesaj, o eroare poartă TOT ce a spus driverul (`extra`): codul lui, numele din documentație, descrierea și rezolvarea
documentate, chitanța de refuz (FisCool) și răspunsul brut — ajung neschimbate până în Dressbit.
"""

EXTRA_CHEI = ("driver_code", "driver_name", "driver_description", "resolution", "receipt", "driver_response")


class DriverError(Exception):
    code = "ERROR"
    retry_safe = False
    # Eșec trecător, DOCUMENTAT ca atare de driver (casă ocupată, conexiune încă neridicată): agentul reîncearcă singur, fără operator.
    transient = False

    def __init__(self, message="", code=None, retry_safe=None, transient=None, **extra):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retry_safe is not None:
            self.retry_safe = retry_safe
        if transient is not None:
            self.transient = transient
        self.extra = {k: v for k, v in extra.items() if k in EXTRA_CHEI and v is not None}

    def to_dict(self):
        return {"code": self.code, "message": self.message, "retry_safe": self.retry_safe, **self.extra}


class NotConnected(DriverError):
    """Aparatul/driverul nu răspunde: nimic nu a ajuns la el, retrimiterea e sigură."""
    code = "NOT_CONNECTED"
    retry_safe = True


class NothingSent(DriverError):
    """S-a dovedit că aparatul nu a primit comanda: retrimiterea e sigură."""
    code = "NOT_SENT"
    retry_safe = True


class Rejected(DriverError):
    """Aparatul a respins comanda înainte să creeze un job: retrimiterea e sigură."""
    code = "REJECTED"
    retry_safe = True


class PrintFailed(DriverError):
    """Aparatul a confirmat explicit eșecul. `retry_safe` depinde de ce garantează driverul."""
    code = "PRINT_FAILED"
    retry_safe = True


class ConfigError(DriverError):
    """Setare lipsă sau greșită, descoperită înainte de orice trimitere."""
    code = "CONFIG"
    retry_safe = True


class Unsupported(DriverError):
    code = "UNSUPPORTED"
    retry_safe = False


class Uncertain(DriverError):
    """Nu se știe dacă s-a tipărit. Jobul rămâne în așteptare, fără retrimitere automată."""
    code = "UNCERTAIN"
    retry_safe = False


class PosDeclined(DriverError):
    """POS-ul a respins plata: nimic nu s-a tipărit, plata nu s-a făcut."""
    code = "POS_DECLINED"
    retry_safe = True
