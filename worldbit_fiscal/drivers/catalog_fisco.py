"""Codurile și statusurile Fisco, copiate din manualul oficial (https://fisco.ro/manual-fisco/, secțiunile 2, 2.1, 2.2).

Manualul nu are un tabel de coduri numerice ale imprimantei: acelea vin în `error.message` (ex. `#ErrorCode (-112201) : ...`) și se
transportă neschimbate. Aici sunt doar codurile API documentate și motivele de respingere documentate.
"""

COD_API = {
    "INVALID_JSON": "Body-ul requestului nu este JSON valid.",
    "EMPTY_BODY": "Body-ul requestului este gol.",
    "CONVERSION_ERROR": "Comenzile nu au putut fi procesate.",
    "PRINTER_NOT_READY": "Imprimanta sau driverul nu este pregătit.",
    "QUEUE_ERROR": "Fisco nu a putut pune jobul în coada de procesare.",
    "JOB_NOT_FOUND": "Jobul nu a fost găsit.",
    "PRINT_FAILED": "Jobul a fost procesat, dar printarea a eșuat.",
    "METHOD_NOT_ALLOWED": "Metoda HTTP folosită este greșită.",
    "SETTINGS_ERROR": "O setare Fisco nu a putut fi citită.",
    "INTERNAL_ERROR": "Eroare internă neașteptată.",
    "LAST_RECEIPT_NOT_FOUND": "Nu există niciun job HTTP finalizat.",
}

STATUS_JOB = {
    "queued": "Jobul a fost acceptat și este în așteptare pentru procesare.",
    "processing": "Jobul este în curs de procesare.",
    "printed": "Bonul sau raportul a fost procesat cu succes.",
    "failed": "Jobul a fost procesat, dar printarea a eșuat.",
    "not_found": "Jobul nu a fost găsit.",
}

# „Motive posibile” documentate pentru o cerere respinsă înainte de punerea jobului în coadă (manual 2.2).
MOTIVE_RESPINS = (
    "Fiscal Driver processing is off",
    "Lipsa conexiune cu imprimanta fiscala",
    "Depasire 24h Raport Z imprimanta fiscala",
    "Printer status is not known yet",
)

_REZOLVARE_24H = ("Raportul Z trebuie tipărit în maximum 24 de ore de la prima vânzare fiscală de după ultimul raport Z; "
                  "altfel imprimanta nu mai permite bonuri fiscale până la tipărirea raportului Z (manualul Fisco, 4.8).")


def descriere_cod(cod):
    return COD_API.get(cod)


def rezolvare(mesaj):
    """Rezolvarea DOCUMENTATĂ pentru un mesaj de respingere; None dacă manualul nu dă una."""
    if mesaj and "Depasire 24h Raport Z" in mesaj:
        return _REZOLVARE_24H
    return None
