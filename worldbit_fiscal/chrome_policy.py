"""Politica Chrome și Edge care pre-aprobă permanent accesul site-ului la agentul local, ca browserul să NU mai ceară permisiunea
(„Access other apps and services on this device"). Edge (Chromium) are aceleași politici, dar în propriul registru
(`SOFTWARE\\Policies\\Microsoft\\Edge`); fără ele, Edge blochează tăcut cererile paginii către agent (verificat pe Edge 155: nicio cerere nu ajunge la agent).

Chrome cere permisiune oricărui site public care apelează ceva de pe propriul calculator (aici: agentul de la 127.0.0.1). Politica o dă dinainte:
  - Chrome 142-145: `LocalNetworkAccessAllowedForUrls`;
  - Chrome 146+ (permisiunea s-a împărțit în „local" și „loopback"): `LoopbackNetworkAllowedForUrls`.
Se scriu ambele (cea nepotrivită versiunii e ignorată). Sursa numelor și versiunilor: documentația Okta și pagina Google a politicii loopback; vezi README.

Format pe Windows: HKLM\\SOFTWARE\\Policies\\Google\\Chrome\\<Politica> este o cheie cu valori-șir numerotate „1", „2", …, fiecare cu un tipar de URL
(aici originea aplicației, ex. `https://test.worldbit.ro`). Instalarea adaugă doar originea dată și nu atinge intrările altora.
"""
import re

POLITICI = ("LocalNetworkAccessAllowedForUrls", "LoopbackNetworkAllowedForUrls")
CALE_CHROME = r"SOFTWARE\Policies\Google\Chrome"
CALE_EDGE = r"SOFTWARE\Policies\Microsoft\Edge"
# (prefix în rezultat, cale de bază): pentru Chrome cheile rezultatului rămân numele politicii, pentru Edge au prefixul „Edge."
BROWSERE = (("", CALE_CHROME), ("Edge.", CALE_EDGE))
_ORIGINE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


class OrigineInvalida(ValueError):
    pass


def valideaza_origine(origine):
    """Doar o origine (schemă + gazdă [+ port]), fără cale și fără metacaractere: politica nu trebuie să deschidă mai mult decât aplicația."""
    if not isinstance(origine, str) or not _ORIGINE.match(origine):
        raise OrigineInvalida(f"Origine invalidă: {origine!r} (formă: https://gazda[:port], fără cale)")
    return origine


def _tinte():
    """(cheia din rezultat, calea din registru) pentru fiecare politică a fiecărui browser."""
    return [(f"{prefix}{politica}", f"{baza}\\{politica}") for prefix, baza in BROWSERE for politica in POLITICI]


def adauga_origine(registru, origine):
    """Adaugă originea în fiecare politică, dacă nu e deja. Întoarce {politica: 'adaugata' | 'exista'}."""
    valideaza_origine(origine)
    rezultat = {}
    for cheie, cale in _tinte():
        valori = registru.citeste(cale)
        if origine in valori.values():
            rezultat[cheie] = "exista"
            continue
        numere = [int(n) for n in valori if n.isdigit()]
        registru.scrie(cale, str(max(numere, default=0) + 1), origine)
        rezultat[cheie] = "adaugata"
    return rezultat


def scoate_origine(registru, origine):
    """Scoate DOAR originea dată; intrările altora rămân. Cheia goală se șterge. Întoarce {politica: 'scoasa' | 'lipsa'}."""
    valideaza_origine(origine)
    rezultat = {}
    for cheie, cale in _tinte():
        valori = registru.citeste(cale)
        gasite = [nume for nume, valoare in valori.items() if valoare == origine]
        for nume in gasite:
            registru.sterge(cale, nume)
        if gasite and len(gasite) == len(valori):
            registru.sterge_cheie(cale)
        rezultat[cheie] = "scoasa" if gasite else "lipsa"
    return rezultat


def stare(registru, origine):
    """{politica: True/False} — originea e aprobată în politică (pentru Chrome și, cu prefixul „Edge.", pentru Edge)."""
    valideaza_origine(origine)
    return {cheie: origine in registru.citeste(cale).values() for cheie, cale in _tinte()}


class RegistruWindows:
    """Registrul real (HKLM, vederea pe 64 de biți, unde citește Chrome). Doar pe Windows, cu drepturi de administrator."""

    def __init__(self):
        import winreg
        self._w = winreg
        self._acces = winreg.KEY_WOW64_64KEY

    def citeste(self, cale):
        w = self._w
        try:
            with w.OpenKey(w.HKEY_LOCAL_MACHINE, cale, 0, w.KEY_READ | self._acces) as cheie:
                valori, i = {}, 0
                while True:
                    try:
                        nume, valoare, tip = w.EnumValue(cheie, i)
                    except OSError:
                        return valori
                    if tip == w.REG_SZ:
                        valori[nume] = valoare
                    i += 1
        except FileNotFoundError:
            return {}

    def scrie(self, cale, nume, valoare):
        w = self._w
        with w.CreateKeyEx(w.HKEY_LOCAL_MACHINE, cale, 0, w.KEY_SET_VALUE | self._acces) as cheie:
            w.SetValueEx(cheie, nume, 0, w.REG_SZ, valoare)

    def sterge(self, cale, nume):
        w = self._w
        with w.OpenKey(w.HKEY_LOCAL_MACHINE, cale, 0, w.KEY_SET_VALUE | self._acces) as cheie:
            w.DeleteValue(cheie, nume)

    def sterge_cheie(self, cale):
        w = self._w
        w.DeleteKeyEx(w.HKEY_LOCAL_MACHINE, cale, self._acces, 0)
