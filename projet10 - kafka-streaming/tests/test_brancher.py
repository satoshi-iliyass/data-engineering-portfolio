import pytest

import src.brancher as piece
from src.brancher import brancher, verifier_position, verifier_tout_lu
from tests.test_traiter import COMPTES, CONVOYEUR, DEJA_VUS, REBUTS, brut, photo, preparer, travailler


class FauxConvoyeur:
    """messages = {position: octets}; None in `silences` positions means: say nothing once before this message."""

    def __init__(self, messages, premiere=0, fin=None, silences=()):
        self.messages, self.premiere = dict(messages), premiere
        self.fin = len(messages) if fin is None else fin
        self.silences, self.depart, self.demandes = list(silences), None, 0

    def limites(self):
        return self.premiere, self.fin

    def aller(self, position):
        self.depart = self.ou = position

    def suivant(self):
        self.demandes += 1
        restants = [p for p in sorted(self.messages) if p >= self.ou]
        if not restants:
            return None
        if restants[0] in self.silences:
            self.silences.remove(restants[0])
            return None
        self.ou = restants[0] + 1
        return restants[0], self.messages[restants[0]]


@pytest.fixture
def base(tmp_path):
    b = preparer(tmp_path / "base.db")
    yield b
    b.close()


@pytest.fixture
def reference(tmp_path):
    b = preparer(tmp_path / "reference.db")
    travailler(b)
    p = photo(b)
    b.close()
    return p


TOUT = dict(enumerate(CONVOYEUR))


def test_tout_le_convoyeur(base, reference):
    c = FauxConvoyeur(TOUT)
    assert brancher(base, c, 3) == {"compté": COMPTES, "déjà vu": DEJA_VUS, "rebut": REBUTS}
    assert photo(base) == reference
    assert c.depart == 0


def test_deuxieme_passage_ne_fait_rien(base, reference):
    brancher(base, FauxConvoyeur(TOUT), 1)
    c = FauxConvoyeur(TOUT)
    assert brancher(base, c, 1) == {"compté": 0, "déjà vu": 0, "rebut": 0}
    assert c.depart == len(CONVOYEUR) and photo(base) == reference


@pytest.mark.parametrize("coupe", range(len(CONVOYEUR)))
def test_panne_puis_reprise_a_chaque_message(base, reference, monkeypatch, coupe):
    vraie = piece.traiter

    def panne(b, feuille, octets, position):
        resultat = vraie(b, feuille, octets, position)
        if position == coupe:
            raise RuntimeError("panne")
        return resultat
    monkeypatch.setattr(piece, "traiter", panne)
    with pytest.raises(RuntimeError):
        brancher(base, FauxConvoyeur(TOUT), 1)
    monkeypatch.undo()
    c = FauxConvoyeur(TOUT)
    brancher(base, c, 1)
    assert c.depart == coupe + 1 and photo(base) == reference


@pytest.mark.parametrize("prochaine,accepte", [(99, False), (100, True), (250, True), (500, True), (501, False)])
def test_verifier_position(prochaine, accepte):
    if accepte:
        verifier_position(prochaine, 100, 500)
    else:
        with pytest.raises(ValueError, match="démarrage refusé"):
            verifier_position(prochaine, 100, 500)


def test_messages_deja_effaces_refuse_sans_rien_lire(base):
    c = FauxConvoyeur({5: brut()}, premiere=5, fin=6)
    avant = photo(base)
    with pytest.raises(ValueError, match="messages déjà effacés : demandé 0, premier restant 5"):
        brancher(base, c, 1)
    assert photo(base) == avant and c.depart is None and c.demandes == 0


def test_position_trop_haute_refusee(base):
    brancher(base, FauxConvoyeur({0: brut("e1"), 1: brut("e2")}), 1)
    with pytest.raises(ValueError, match="position inconnue de Kafka : demandé 2, fin 1"):
        brancher(base, FauxConvoyeur({0: brut("e1")}), 1)


def test_un_silence_court_ne_coupe_pas(base, reference):
    brancher(base, FauxConvoyeur(TOUT, silences=[0, 3, 3, 9]), 3)
    assert photo(base) == reference


def test_silences_remis_a_zero_apres_un_message(base, reference):
    brancher(base, FauxConvoyeur(TOUT, silences=[1, 1, 4, 4, 8, 8]), 3)
    assert photo(base) == reference


def test_attend_le_nombre_de_silences_demande(base):
    c = FauxConvoyeur({})
    assert brancher(base, c, 4) == {"compté": 0, "déjà vu": 0, "rebut": 0}
    assert c.demandes == 4


def test_silence_trop_long_au_milieu_est_un_arret_anormal(base):
    c = FauxConvoyeur(TOUT, silences=[5, 5])
    with pytest.raises(ValueError, match="arrêt anormal, il reste des messages : arrêté à 5, fin 24"):
        brancher(base, c, 2)


def test_message_arrive_pendant_la_lecture_est_attendu(base):
    c = FauxConvoyeur({0: brut("e1")})
    vraies, appels = c.limites, []

    def limites():
        appels.append(1)
        if len(appels) == 2:
            c.fin = 2
        return vraies()
    c.limites = limites
    with pytest.raises(ValueError, match="il reste des messages : arrêté à 1, fin 2"):
        brancher(base, c, 1)


def test_verifier_tout_lu():
    verifier_tout_lu(5, 5)
    with pytest.raises(ValueError, match="arrêt anormal"):
        verifier_tout_lu(4, 5)


def test_kafka_en_panne_arrete(base):
    c = FauxConvoyeur(TOUT)
    vrai = c.suivant

    def suivant():
        if c.demandes == 3:
            raise RuntimeError("Kafka en panne")
        return vrai()
    c.suivant = suivant
    with pytest.raises(RuntimeError, match="Kafka en panne"):
        brancher(base, c, 1)
    assert photo(base)[2] == [(1, 2)]


def test_trou_dans_les_positions(base):
    c = FauxConvoyeur({0: brut("e1"), 7: brut("e2")}, fin=8)
    assert brancher(base, c, 1)["compté"] == 2
    assert photo(base)[2] == [(1, 7)]
