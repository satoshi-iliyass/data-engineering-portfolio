import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.mettre_au_rebut as piece
from src.enregistrer import enregistrer, ouvrir_base
from src.mettre_au_rebut import LONGUEUR_GARDEE, mettre_au_rebut, ouvrir_bac, rendre_rangeable
from src.reprendre import reprendre
from src.trouver_case import trouver_case

PROJET = Path(__file__).parent.parent
BON = {"id": "e1", "machine": "M1", "heure": datetime(2026, 11, 22, 10, 0, 5, tzinfo=timezone.utc)}


def preparer(chemin):
    base = ouvrir_base(chemin)
    ouvrir_bac(base)
    return base


def photo(base):
    return [base.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("totaux", "feuille", "position", "rebuts")]


@pytest.fixture
def base(tmp_path):
    b = preparer(tmp_path / "base.db")
    enregistrer(b, BON, trouver_case(BON), True, 0)
    yield b
    b.close()


def test_rebut_range_et_position_avancee_rien_d_autre(base):
    avant = photo(base)
    mettre_au_rebut(base, "bonjour", "illisible", 1)
    assert photo(base) == [avant[0], avant[1], [(1, 1)], [(1, "bonjour", "illisible")]]


def test_la_suite_continue_apres_un_rebut(base):
    mettre_au_rebut(base, "bonjour", "illisible", 1)
    f = {**BON, "id": "e2"}
    enregistrer(base, f, trouver_case(f), True, 2)
    assert reprendre(base) == (3, {"e1": BON, "e2": f})


def test_plusieurs_rebuts_gardes(base):
    mettre_au_rebut(base, "a", "r1", 1)
    mettre_au_rebut(base, "a", "r1", 2)
    assert photo(base)[3] == [(1, "a", "r1"), (2, "a", "r1")]


@pytest.mark.parametrize("position", [0, -1])
def test_position_deja_passee_refusee(base, position):
    avant = photo(base)
    with pytest.raises(ValueError, match="position refusée"):
        mettre_au_rebut(base, "bonjour", "illisible", position)
    assert photo(base) == avant


def test_le_bac_refuse_lui_meme_deux_rebuts_a_la_meme_position(base):
    mettre_au_rebut(base, "bonjour", "illisible", 1)
    base.execute("UPDATE position SET position = 0")
    base.commit()
    avant = photo(base)
    with pytest.raises(sqlite3.IntegrityError):
        mettre_au_rebut(base, "bonjour", "illisible", 1)
    assert photo(base) == avant


@pytest.mark.parametrize("raison", ["", "  ", "\n"])
def test_raison_vide_refusee(base, raison):
    avant = photo(base)
    with pytest.raises(ValueError, match="il faut une raison"):
        mettre_au_rebut(base, "bonjour", raison, 1)
    assert photo(base) == avant


def test_message_et_raison_geants_raccourcis(base):
    mettre_au_rebut(base, "x" * 1_000_000, "y" * 1_000_000, 1)
    assert photo(base)[3] == [(1, "x" * LONGUEUR_GARDEE, "y" * LONGUEUR_GARDEE)]
    assert LONGUEUR_GARDEE == 500


def test_message_vide_accepte(base):
    mettre_au_rebut(base, "", "illisible", 1)
    assert photo(base)[3] == [(1, "", "illisible")]


@pytest.mark.parametrize("piege", ["x'); DROP TABLE totaux; --", "a" + chr(0) + "b", "été £ 日本"])
def test_texte_piege_range_tel_quel(base, piege):
    mettre_au_rebut(base, piege, piege, 1)
    assert photo(base)[3] == [(1, piege, piege)]
    assert len(photo(base)[0]) == 1


def test_caractere_impossible_remplace():
    assert rendre_rangeable("M" + chr(0xD800)) == "M?"


def test_caractere_impossible_ne_bloque_pas(base):
    mettre_au_rebut(base, "M" + chr(0xD800), "raison" + chr(0xD800), 1)
    assert photo(base)[3] == [(1, "M?", "raison?")]


def test_erreur_entre_les_deux_ecritures(base, monkeypatch):
    avant, vraie = photo(base), piece.avancer

    def panne(*a):
        vraie(*a)
        raise RuntimeError("panne")
    monkeypatch.setattr(piece, "avancer", panne)
    with pytest.raises(RuntimeError):
        mettre_au_rebut(base, "bonjour", "illisible", 1)
    assert photo(base) == avant


def test_bac_absent_la_position_ne_bouge_pas(base):
    base.execute("DROP TABLE rebuts")
    with pytest.raises(sqlite3.OperationalError):
        mettre_au_rebut(base, "bonjour", "illisible", 1)
    assert base.execute("SELECT position FROM position").fetchall() == [(0,)]


COUPURE = """
import os, sys
import src.mettre_au_rebut as piece
from tests.test_mettre_au_rebut import preparer
vraie = piece.avancer
def coupe(*a):
    vraie(*a)
    os._exit(9)
piece.avancer = coupe
piece.mettre_au_rebut(preparer(sys.argv[1]), "bonjour", "illisible", 1)
"""


def test_programme_tue_entre_les_deux_ecritures(tmp_path, base):
    avant = photo(base)
    r = subprocess.run([sys.executable, "-B", "-c", COUPURE, str(tmp_path / "base.db")], cwd=PROJET)
    assert r.returncode == 9
    assert photo(base) == avant


def test_rebut_ecrit_pour_de_bon_et_bac_garde_a_la_reouverture(tmp_path, base):
    mettre_au_rebut(base, "bonjour", "illisible", 1)
    autre = preparer(tmp_path / "base.db")
    assert photo(autre)[3] == [(1, "bonjour", "illisible")]
    autre.close()


def test_ecriture_reservee_des_le_debut(tmp_path, base, monkeypatch):
    autre, vu, vraie = sqlite3.connect(tmp_path / "base.db", timeout=0), [], piece.avancer

    def regarde(*a):
        try:
            autre.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            vu.append("bloqué")
        vraie(*a)
    monkeypatch.setattr(piece, "avancer", regarde)
    mettre_au_rebut(base, "bonjour", "illisible", 1)
    autre.close()
    assert vu == ["bloqué"]
