import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.enregistrer as piece
from src.enregistrer import enregistrer, ouvrir_base, verifier_deja_vu
from src.trouver_case import trouver_case

PROJET = Path(__file__).parent.parent


def fiche(numero="e1", machine="M1", minute=0):
    return {"id": numero, "machine": machine, "heure": datetime(2026, 11, 22, 10, minute, 5, tzinfo=timezone.utc)}


def ecrire(base, f, nouveau, position):
    enregistrer(base, f, trouver_case(f), nouveau, position)


def photo(base):
    return [base.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall() for table in ("totaux", "feuille", "position")]


@pytest.fixture
def base(tmp_path):
    b = ouvrir_base(tmp_path / "base.db")
    yield b
    b.close()


def test_base_neuve_vide(base):
    assert photo(base) == [[], [], [(1, -1)]]


def test_un_message_nouveau_ecrit_les_trois(base):
    ecrire(base, fiche(), True, 0)
    assert photo(base) == [
        [("M1", "2026-11-22T10:00:00+00:00", 1)],
        [("e1", "M1", "2026-11-22T10:00:05+00:00")],
        [(1, 0)]]


def test_le_total_monte_case_par_case(base):
    for position, f in enumerate([fiche("e1"), fiche("e2"), fiche("e3", "M2"), fiche("e4", "M1", 1)]):
        ecrire(base, f, True, position)
    assert photo(base)[0] == [("M1", "2026-11-22T10:00:00+00:00", 2), ("M1", "2026-11-22T10:01:00+00:00", 1),
                              ("M2", "2026-11-22T10:00:00+00:00", 1)]


def test_deja_vu_avance_seulement_la_position(base):
    ecrire(base, fiche(), True, 0)
    avant = photo(base)
    ecrire(base, fiche(), False, 1)
    assert photo(base) == [avant[0], avant[1], [(1, 1)]]


def test_mentir_nouveau_pour_un_doublon_ne_change_rien(base):
    ecrire(base, fiche(), True, 0)
    avant = photo(base)
    with pytest.raises(sqlite3.IntegrityError):
        ecrire(base, fiche(), True, 1)
    assert photo(base) == avant


def test_mentir_deja_vu_pour_un_nouveau_ne_change_rien(base):
    avant = photo(base)
    with pytest.raises(ValueError, match="absent de la feuille : e1"):
        ecrire(base, fiche(), False, 0)
    assert photo(base) == avant


def test_mentir_deja_vu_avec_une_feuille_deja_remplie(base):
    ecrire(base, fiche("e1"), True, 0)
    avant = photo(base)
    with pytest.raises(ValueError, match="absent de la feuille : e2"):
        ecrire(base, fiche("e2"), False, 1)
    assert photo(base) == avant


def test_verifier_deja_vu_accepte_un_id_connu(base):
    ecrire(base, fiche(), True, 0)
    verifier_deja_vu(base, fiche())


@pytest.mark.parametrize("position", [4, 5, -1])
def test_position_deja_passee_refusee(base, position):
    ecrire(base, fiche(), True, 5)
    avant = photo(base)
    with pytest.raises(ValueError, match="position refusée, déjà passée"):
        ecrire(base, fiche("e2"), True, position)
    assert photo(base) == avant


def test_position_negative_refusee_sur_base_neuve(base):
    with pytest.raises(ValueError, match="position refusée"):
        ecrire(base, fiche(), True, -1)


def test_trou_dans_les_positions_accepte(base):
    ecrire(base, fiche("e1"), True, 0)
    ecrire(base, fiche("e2"), True, 7)
    assert photo(base)[2] == [(1, 7)]


@pytest.mark.parametrize("etape", ["avancer", "noter", "compter"])
def test_erreur_juste_apres_chaque_ecriture(base, monkeypatch, etape):
    ecrire(base, fiche(), True, 0)
    avant = photo(base)
    vraie = getattr(piece, etape)

    def panne(*a):
        vraie(*a)
        raise RuntimeError("panne")
    monkeypatch.setattr(piece, etape, panne)
    with pytest.raises(RuntimeError):
        ecrire(base, fiche("e2"), True, 1)
    assert photo(base) == avant
    monkeypatch.undo()
    ecrire(base, fiche("e2"), True, 1)
    assert photo(base)[0] == [("M1", "2026-11-22T10:00:00+00:00", 2)]


COUPURE = """
import os, sys
import src.enregistrer as piece
from tests.test_enregistrer import ecrire, fiche
vraie = getattr(piece, sys.argv[2])
def coupe(*a):
    vraie(*a)
    os._exit(9)
setattr(piece, sys.argv[2], coupe)
ecrire(piece.ouvrir_base(sys.argv[1]), fiche("e2"), True, 1)
"""


@pytest.mark.parametrize("etape", ["avancer", "noter", "compter"])
def test_programme_tue_juste_apres_chaque_ecriture(tmp_path, etape):
    chemin = tmp_path / "base.db"
    b = ouvrir_base(chemin)
    ecrire(b, fiche(), True, 0)
    avant = photo(b)
    b.close()
    r = subprocess.run([sys.executable, "-B", "-c", COUPURE, str(chemin), etape], cwd=PROJET)
    assert r.returncode == 9
    b = ouvrir_base(chemin)
    assert photo(b) == avant
    ecrire(b, fiche("e2"), True, 1)
    assert photo(b)[0] == [("M1", "2026-11-22T10:00:00+00:00", 2)]
    b.close()


def test_ecrit_pour_de_bon(tmp_path):
    b = ouvrir_base(tmp_path / "base.db")
    ecrire(b, fiche(), True, 0)
    avant = photo(b)
    b.close()
    b = ouvrir_base(tmp_path / "base.db")
    assert photo(b) == avant
    b.close()


def test_ecriture_reservee_des_le_debut(base, monkeypatch):
    chemin = base.execute("PRAGMA database_list").fetchone()[2]
    autre = sqlite3.connect(chemin, timeout=0)
    vraie, vu = piece.avancer, []

    def regarde(*a):
        try:
            autre.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            vu.append("bloqué")
        vraie(*a)
    monkeypatch.setattr(piece, "avancer", regarde)
    ecrire(base, fiche(), True, 0)
    autre.close()
    assert vu == ["bloqué"]


def test_texte_piege_reste_du_texte(base):
    piege = "x'); DROP TABLE totaux; --"
    ecrire(base, fiche(piege, piege), True, 0)
    assert photo(base)[0] == [(piege, "2026-11-22T10:00:00+00:00", 1)]
    assert photo(base)[1][0][0] == piege
