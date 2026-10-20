import unittest

from worldbit_fiscal import chrome_policy as cp
from worldbit_fiscal.chrome_policy import OrigineInvalida

ORIGINE = "https://test.worldbit.ro"
L, B = "LocalNetworkAccessAllowedForUrls", "LoopbackNetworkAllowedForUrls"


class RegistruFals:
    """Registrul în memorie: {cale: {nume: valoare}}, cu aceeași interfață ca RegistruWindows."""

    def __init__(self, initial=None):
        self.chei = {k: dict(v) for k, v in (initial or {}).items()}
        self.operatii = []

    def citeste(self, cale):
        return dict(self.chei.get(cale, {}))

    def scrie(self, cale, nume, valoare):
        self.operatii.append(("scrie", cale, nume, valoare))
        self.chei.setdefault(cale, {})[nume] = valoare

    def sterge(self, cale, nume):
        self.operatii.append(("sterge", cale, nume))
        del self.chei[cale][nume]

    def sterge_cheie(self, cale):
        self.operatii.append(("sterge_cheie", cale))
        del self.chei[cale]


def cale(politica):
    return f"SOFTWARE\\Policies\\Google\\Chrome\\{politica}"


def cale_edge(politica):
    return f"SOFTWARE\\Policies\\Microsoft\\Edge\\{politica}"


def toate(chrome_l, chrome_b, edge_l=None, edge_b=None):
    """Rezultatul pe cele 4 politici (Chrome fără prefix, Edge cu prefixul „Edge."); Edge implicit ca Chrome."""
    return {L: chrome_l, B: chrome_b, "Edge." + L: edge_l or chrome_l, "Edge." + B: edge_b or chrome_b}


class AdaugaTest(unittest.TestCase):
    def test_scrie_originea_in_ambele_politici_ca_valoarea_1(self):
        r = RegistruFals()
        self.assertEqual(cp.adauga_origine(r, ORIGINE), toate("adaugata", "adaugata"))
        self.assertEqual(r.chei[cale(L)], {"1": ORIGINE})
        self.assertEqual(r.chei[cale(B)], {"1": ORIGINE})
        self.assertEqual(r.chei[cale_edge(L)], {"1": ORIGINE})   # Edge are aceleași politici, în propriul registru
        self.assertEqual(r.chei[cale_edge(B)], {"1": ORIGINE})

    def test_este_idempotenta(self):
        r = RegistruFals()
        cp.adauga_origine(r, ORIGINE)
        self.assertEqual(cp.adauga_origine(r, ORIGINE), toate("exista", "exista"))
        self.assertEqual(r.chei[cale(L)], {"1": ORIGINE})
        self.assertEqual(len([o for o in r.operatii if o[0] == "scrie"]), 4)   # nicio scriere la a doua rulare

    def test_nu_atinge_intrarile_existente_si_numeroteaza_dupa_ultima(self):
        r = RegistruFals({cale(L): {"1": "https://alta.ro", "3": "https://alta2.ro"}})
        cp.adauga_origine(r, ORIGINE)
        self.assertEqual(r.chei[cale(L)], {"1": "https://alta.ro", "3": "https://alta2.ro", "4": ORIGINE})
        self.assertEqual(r.chei[cale(B)], {"1": ORIGINE})

    def test_ignora_valorile_nenumerice_la_numerotare(self):
        r = RegistruFals({cale(L): {"altceva": "x"}})
        cp.adauga_origine(r, ORIGINE)
        self.assertEqual(r.chei[cale(L)]["1"], ORIGINE)
        self.assertEqual(r.chei[cale(L)]["altceva"], "x")

    def test_a_doua_origine_se_adauga_langa_prima(self):
        r = RegistruFals()
        cp.adauga_origine(r, ORIGINE)
        cp.adauga_origine(r, "https://dressbit2.worldbit.ro")
        self.assertEqual(r.chei[cale(B)], {"1": ORIGINE, "2": "https://dressbit2.worldbit.ro"})


class EdgeTest(unittest.TestCase):
    def test_scoate_originea_si_din_edge_fara_sa_atinga_intrarile_altora(self):
        r = RegistruFals({cale_edge(L): {"1": "https://alta.ro", "2": ORIGINE}, cale_edge(B): {"1": ORIGINE}})
        self.assertEqual(cp.scoate_origine(r, ORIGINE)["Edge." + L], "scoasa")
        self.assertEqual(r.chei[cale_edge(L)], {"1": "https://alta.ro"})
        self.assertNotIn(cale_edge(B), r.chei)

    def test_stare_arata_separat_chrome_si_edge(self):
        r = RegistruFals({cale_edge(B): {"1": ORIGINE}})
        self.assertEqual(cp.stare(r, ORIGINE), {L: False, B: False, "Edge." + L: False, "Edge." + B: True})


class ScoateTest(unittest.TestCase):
    def test_scoate_doar_originea_data_si_lasa_restul(self):
        r = RegistruFals({cale(L): {"1": "https://alta.ro", "2": ORIGINE}, cale(B): {"1": ORIGINE}})
        self.assertEqual(cp.scoate_origine(r, ORIGINE), {L: "scoasa", B: "scoasa", "Edge." + L: "lipsa", "Edge." + B: "lipsa"})
        self.assertEqual(r.chei[cale(L)], {"1": "https://alta.ro"})
        self.assertNotIn(cale(B), r.chei)                        # cheia rămasă goală se șterge

    def test_cand_lipseste_nu_face_nimic(self):
        r = RegistruFals({cale(L): {"1": "https://alta.ro"}})
        self.assertEqual(cp.scoate_origine(r, ORIGINE), toate("lipsa", "lipsa"))
        self.assertEqual(r.operatii, [])


class StareTest(unittest.TestCase):
    def test_arata_in_care_politici_este(self):
        r = RegistruFals({cale(L): {"1": ORIGINE}})
        self.assertEqual(cp.stare(r, ORIGINE), {L: True, B: False, "Edge." + L: False, "Edge." + B: False})


class ValidareTest(unittest.TestCase):
    def test_accepta_originile_valide(self):
        for o in ("https://test.worldbit.ro", "https://app.exemplu.ro:8443", "http://localhost:3000", "http://127.0.0.1:4747"):
            with self.subTest(o):
                self.assertEqual(cp.valideaza_origine(o), o)

    def test_refuza_ce_ar_deschide_mai_mult_decat_aplicatia(self):
        for o in ("", "test.worldbit.ro", "https://test.worldbit.ro/cale", "https://*.worldbit.ro", "https://[*.]worldbit.ro", "*",
                  "https://a.ro https://b.ro", "ftp://x.ro", "https://", None, 5):
            with self.subTest(o), self.assertRaises(OrigineInvalida):
                cp.adauga_origine(RegistruFals(), o)

    def test_originea_invalida_nu_scrie_nimic(self):
        r = RegistruFals()
        with self.assertRaises(OrigineInvalida):
            cp.adauga_origine(r, "https://*.ro")
        self.assertEqual(r.operatii, [])


if __name__ == "__main__":
    unittest.main()
