from datetime import datetime, timezone

import pytest

from src.lire_message import lire_message
from src.trouver_case import trouver_case


def fiche(machine, *heure):
    return {"id": "e1", "machine": machine, "heure": datetime(*heure, tzinfo=timezone.utc)}


def case(machine, *minute):
    return machine, datetime(*minute, tzinfo=timezone.utc)


def test_milieu_de_minute():
    assert trouver_case(fiche("M1", 2026, 11, 22, 10, 0, 5)) == case("M1", 2026, 11, 22, 10, 0)


def test_derniere_seconde_reste_dans_la_minute():
    assert trouver_case(fiche("M1", 2026, 11, 22, 10, 0, 59, 999999)) == case("M1", 2026, 11, 22, 10, 0)


def test_premiere_seconde_change_de_minute():
    assert trouver_case(fiche("M1", 2026, 11, 22, 10, 1, 0)) == case("M1", 2026, 11, 22, 10, 1)


def test_minuit_change_de_jour():
    assert trouver_case(fiche("M1", 2026, 12, 31, 23, 59, 59)) == case("M1", 2026, 12, 31, 23, 59)
    assert trouver_case(fiche("M1", 2027, 1, 1, 0, 0, 0)) == case("M1", 2027, 1, 1, 0, 0)


def test_deux_machines_deux_cases():
    assert trouver_case(fiche("M1", 2026, 11, 22, 10, 0, 5)) != trouver_case(fiche("M2", 2026, 11, 22, 10, 0, 5))


def test_message_en_retard_va_dans_sa_propre_minute():
    avant = trouver_case(fiche("M1", 2026, 11, 22, 10, 0, 5))
    trouver_case(fiche("M1", 2026, 11, 22, 10, 7, 0))
    assert trouver_case(fiche("M1", 2026, 11, 22, 10, 0, 40)) == avant


def test_sans_fuseau_refuse():
    with pytest.raises(ValueError, match="pas de fuseau horaire"):
        trouver_case({"id": "e1", "machine": "M1", "heure": datetime(2026, 11, 22, 10, 0, 5)})


def test_la_fiche_n_est_pas_modifiee():
    f = fiche("M1", 2026, 11, 22, 10, 0, 5)
    trouver_case(f)
    assert f == fiche("M1", 2026, 11, 22, 10, 0, 5)


def test_branchee_sur_la_piece_1():
    ligne = '{"id": "e1", "machine": "M1", "heure": "2026-11-22T10:00:05Z"}'
    assert trouver_case(lire_message(ligne)) == case("M1", 2026, 11, 22, 10, 0)
