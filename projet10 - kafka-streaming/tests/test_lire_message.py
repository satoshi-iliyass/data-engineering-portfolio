import json
from datetime import datetime, timezone

import pytest

from src.lire_message import LONGUEUR_MAX, fiche_sans_doublon, lire_champ, lire_heure, lire_message

BON = {"id": "e1", "machine": "M1", "heure": "2026-11-22T10:00:05Z"}


def ligne(**changements):
    return json.dumps({**BON, **changements})


def test_bon_message():
    assert lire_message(ligne()) == {
        "id": "e1", "machine": "M1", "heure": datetime(2026, 11, 22, 10, 0, 5, tzinfo=timezone.utc)}


def test_champ_en_plus_ignore():
    assert lire_message(ligne(couleur="rouge")) == lire_message(ligne())


@pytest.mark.parametrize("texte", ["", "bonjour", '{"id": "e1"', "{'id': 'e1'}"])
def test_illisible(texte):
    with pytest.raises(ValueError, match="message refusé, illisible"):
        lire_message(texte)


@pytest.mark.parametrize("texte", ["[]", "12", "null", '"e1"', "true"])
def test_pas_une_fiche(texte):
    with pytest.raises(ValueError, match="message refusé, pas une fiche"):
        lire_message(texte)


@pytest.mark.parametrize("nom", ["id", "machine", "heure"])
def test_champ_manquant(nom):
    message = dict(BON)
    del message[nom]
    with pytest.raises(ValueError, match="absent ou pas du texte : " + nom):
        lire_message(json.dumps(message))


@pytest.mark.parametrize("nom", ["id", "machine", "heure"])
@pytest.mark.parametrize("valeur", [None, 12, True, ["M1"], {"a": "b"}])
def test_champ_pas_du_texte(nom, valeur):
    with pytest.raises(ValueError, match="absent ou pas du texte : " + nom):
        lire_message(ligne(**{nom: valeur}))


@pytest.mark.parametrize("valeur", ["", " ", " M1", "M1 ", "M1\n"])
def test_champ_vide_ou_espace(valeur):
    with pytest.raises(ValueError, match="vide ou espace en trop : machine"):
        lire_champ({"machine": valeur}, "machine")


def test_champ_ecrit_deux_fois():
    with pytest.raises(ValueError, match="message refusé, illisible"):
        lire_message('{"id": "e1", "id": "e2", "machine": "M1", "heure": "2026-11-22T10:00:05Z"}')


def test_fiche_sans_doublon():
    assert fiche_sans_doublon([("a", 1), ("b", 1)]) == {"a": 1, "b": 1}
    with pytest.raises(ValueError, match="deux fois"):
        fiche_sans_doublon([("a", 1), ("a", 1)])


@pytest.mark.parametrize("valeur", ["M" + chr(0) + "1", "M" + chr(0xD800), "M" + chr(0x200B) + "1"])
def test_caractere_illisible(valeur):
    with pytest.raises(ValueError, match="caractère illisible : machine"):
        lire_message(ligne(machine=valeur))


def test_champ_rendu_tel_quel():
    assert lire_champ({"machine": "Presse 1"}, "machine") == "Presse 1"


@pytest.mark.parametrize("texte", ["hier", "22/11/2026 10:00", "2026-02-30T10:00:00Z", "2026-11-22T25:00:00Z",
                                   "2026-11-22T10:00:05", "2026-11-22 10:00:05Z", "2026-11-22T10:00:05+01:00"])
def test_heure_impossible(texte):
    with pytest.raises(ValueError, match="heure impossible"):
        lire_heure(texte)


@pytest.mark.parametrize("texte", ["2026-1-2T10:00:05Z", "2026-11-22T9:00:05Z"])
def test_heure_mal_ecrite(texte):
    with pytest.raises(ValueError, match="heure mal écrite"):
        lire_heure(texte)


def test_29_fevrier():
    assert lire_heure("2028-02-29T00:00:00Z") == datetime(2028, 2, 29, tzinfo=timezone.utc)


def test_longueur_limite():
    vide = len(ligne(couleur=""))
    assert lire_message(ligne(couleur="x" * (LONGUEUR_MAX - vide)))["id"] == "e1"
    with pytest.raises(ValueError, match="trop long"):
        lire_message(ligne(couleur="x" * (LONGUEUR_MAX - vide + 1)))


def test_message_piege_tres_long():
    with pytest.raises(ValueError, match="trop long"):
        lire_message("[" * 1_000_000)
