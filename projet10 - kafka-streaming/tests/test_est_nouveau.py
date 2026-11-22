from datetime import datetime, timezone

import pytest

from src.est_nouveau import est_nouveau
from src.lire_message import lire_message


def fiche(numero="e1", machine="M1", seconde=5):
    return {"id": numero, "machine": machine, "heure": datetime(2026, 11, 22, 10, 0, seconde, tzinfo=timezone.utc)}


FEUILLE = {"e1": fiche()}


def test_feuille_vide():
    assert est_nouveau(fiche(), {}) is True


def test_id_inconnu():
    assert est_nouveau(fiche("e2"), FEUILLE) is True


def test_vrai_doublon():
    assert est_nouveau(fiche(), FEUILLE) is False


def test_doublon_parmi_plusieurs():
    feuille = {"e1": fiche(), "e2": fiche("e2", "M2", 9)}
    assert est_nouveau(fiche(), feuille) is False
    assert est_nouveau(fiche("e2", "M2", 9), feuille) is False


def test_identiques_sauf_id_sont_deux_pieces():
    assert est_nouveau(fiche("e9"), FEUILLE) is True


@pytest.mark.parametrize("autre", [fiche(machine="M2"), fiche(seconde=6)])
def test_meme_id_contenu_different_refuse(autre):
    with pytest.raises(ValueError, match="même id mais contenu différent : e1"):
        est_nouveau(autre, FEUILLE)


@pytest.mark.parametrize("numero", ["e1", "e2"])
def test_rien_n_est_modifie(numero):
    f, feuille = fiche(numero), {"e1": fiche()}
    est_nouveau(f, feuille)
    assert feuille == {"e1": fiche()}
    assert f == fiche(numero)


def test_branchee_sur_la_piece_1():
    ligne = '{"id": "e1", "machine": "M1", "heure": "2026-11-22T10:00:05Z"}'
    assert est_nouveau(lire_message(ligne), {"e1": lire_message(ligne)}) is False
