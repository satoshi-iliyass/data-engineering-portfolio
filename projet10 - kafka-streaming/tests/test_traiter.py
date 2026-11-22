import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import src.traiter as piece
from src.enregistrer import ouvrir_base
from src.mettre_au_rebut import ouvrir_bac
from src.reprendre import reprendre
from src.traiter import octets_en_texte, texte_pour_le_bac, traiter

PROJET = Path(__file__).parent.parent


def brut(numero="e1", machine="M1", heure="2026-11-22T10:00:05Z"):
    return json.dumps({"id": numero, "machine": machine, "heure": heure}).encode()


# 12 good, 4 already seen, 8 bad
CONVOYEUR = [brut(f"e{n}", f"M{n % 2}", f"2026-11-22T10:0{n % 3}:05Z") for n in range(6)] + [
    brut("e0", "M0"), b"bonjour", None, b"\xff\xfe", brut("e1", "M9")] + [
    brut(f"e{n}", f"M{n % 2}", f"2026-11-22T10:0{n % 3}:05Z") for n in range(6, 12)] + [
    brut("e0", "M0"), brut("e6", "M0"), b"", brut("e50", heure="hier"), brut("e51", ""), b"[]", brut("e0", "M0")]
COMPTES, DEJA_VUS, REBUTS = 12, 4, 8


def preparer(chemin):
    base = ouvrir_base(chemin)
    ouvrir_bac(base)
    return base


def travailler(base):
    prochaine, feuille = reprendre(base)
    return [traiter(base, feuille, CONVOYEUR[p], p) for p in range(prochaine, len(CONVOYEUR))]


def photo(base):
    return [base.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("totaux", "feuille", "position", "rebuts")]


@pytest.fixture
def base(tmp_path):
    b = preparer(tmp_path / "base.db")
    yield b
    b.close()


def test_bon_message_compte(base):
    feuille = {}
    assert traiter(base, feuille, brut(), 0) == "compté"
    assert photo(base) == [[("M1", "2026-11-22T10:00:00+00:00", 1)], [("e1", "M1", "2026-11-22T10:00:05+00:00")],
                           [(1, 0)], []]
    assert list(feuille) == ["e1"]
    assert feuille == reprendre(base)[1]


def test_deja_vu_pas_recompte(base):
    feuille = {}
    traiter(base, feuille, brut(), 0)
    avant = photo(base)
    assert traiter(base, feuille, brut(), 1) == "déjà vu"
    assert photo(base) == [avant[0], avant[1], [(1, 1)], []]


@pytest.mark.parametrize("octets,texte,raison", [
    (b"bonjour", "bonjour", "illisible"),
    (b"", "", "illisible"),
    (None, "", "message refusé, vide"),
    (b'{"id": "\xff"}', '{"id": "\\xff"}', "octets abîmés"),
    (brut(machine=""), brut(machine="").decode(), "champ vide"),
    (brut(heure="hier"), brut(heure="hier").decode(), "heure impossible"),
])
def test_message_mauvais_au_bac(base, octets, texte, raison):
    feuille = {}
    assert traiter(base, feuille, octets, 0) == "rebut"
    totaux, sheet, position, rebuts = photo(base)
    assert (totaux, sheet, position, feuille) == ([], [], [(1, 0)], {})
    assert len(rebuts) == 1 and rebuts[0][:2] == (0, texte) and raison in rebuts[0][2]


def test_meme_id_autre_contenu_au_bac(base):
    feuille = {}
    traiter(base, feuille, brut(), 0)
    avant, feuille_avant = photo(base), dict(feuille)
    assert traiter(base, feuille, brut(machine="M2"), 1) == "rebut"
    apres = photo(base)
    assert apres[:2] == avant[:2] and feuille == feuille_avant
    assert "contenu différent" in apres[3][0][2]


def test_la_ligne_entiere(base):
    resultats = travailler(base)
    assert [resultats.count(mot) for mot in ("compté", "déjà vu", "rebut")] == [COMPTES, DEJA_VUS, REBUTS]
    assert len(resultats) == len(CONVOYEUR)
    totaux, feuille, position, rebuts = photo(base)
    assert sum(t for _, _, t in totaux) == len(feuille) == COMPTES
    assert len(rebuts) == REBUTS and position == [(1, len(CONVOYEUR) - 1)]


@pytest.mark.parametrize("octets", [brut(), b"bonjour"])
def test_base_bloquee_arrete_la_ligne_sans_rien_perdre(tmp_path, base, octets):
    base.execute("PRAGMA busy_timeout = 0")
    avant, feuille = photo(base), {}
    autre = sqlite3.connect(tmp_path / "base.db")
    autre.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        traiter(base, feuille, octets, 0)
    autre.rollback()
    autre.close()
    assert photo(base) == avant and feuille == {}
    assert traiter(base, feuille, octets, 0) in ("compté", "rebut")


@pytest.mark.parametrize("nom", ["trouver_case", "enregistrer"])
def test_une_panne_meme_valueerror_ne_va_jamais_au_bac(base, monkeypatch, nom):
    def panne(*a):
        raise ValueError("panne de machine")
    monkeypatch.setattr(piece, nom, panne)
    avant, feuille = photo(base), {}
    with pytest.raises(ValueError, match="panne de machine"):
        traiter(base, feuille, brut(), 0)
    assert photo(base) == avant and feuille == {}


def test_panne_pas_valueerror_dans_la_lecture_arrete(base, monkeypatch):
    def panne(*a):
        raise RuntimeError("panne")
    monkeypatch.setattr(piece, "lire_message", panne)
    with pytest.raises(RuntimeError):
        traiter(base, {}, brut(), 0)
    assert photo(base)[2:] == [[(1, -1)], []]


def test_position_deja_passee_arrete(base):
    feuille = {}
    traiter(base, feuille, brut(), 0)
    for octets in (brut("e2"), b"bonjour"):
        with pytest.raises(ValueError, match="position refusée"):
            traiter(base, feuille, octets, 0)
    assert photo(base)[3] == [] and list(feuille) == ["e1"]


def test_petites_pieces():
    assert octets_en_texte("été".encode()) == "été" == texte_pour_le_bac("été".encode())
    assert texte_pour_le_bac(None) == ""


COUPURE = """
import os, sys
import src.enregistrer, src.mettre_au_rebut
import src.traiter as piece
from tests.test_traiter import preparer, travailler
chemin, moment, mort = sys.argv[1], sys.argv[2], int(sys.argv[3])
def couper(module, nom):
    vraie = getattr(module, nom)
    def coupe(*a):
        vraie(*a)
        if a[-1] == mort:
            os._exit(9)
    setattr(module, nom, coupe)
if moment == "au milieu":
    couper(src.enregistrer, "avancer"); couper(src.mettre_au_rebut, "avancer")
else:
    couper(piece, "enregistrer"); couper(piece, "mettre_au_rebut")
travailler(preparer(chemin))
"""


@pytest.mark.parametrize("moment", ["au milieu", "juste apres"])
def test_tue_a_chaque_message_meme_resultat(tmp_path, base, moment):
    travailler(base)
    chemin = tmp_path / "panne.db"
    for mort in range(len(CONVOYEUR)):
        r = subprocess.run([sys.executable, "-B", "-c", COUPURE, str(chemin), moment, str(mort)], cwd=PROJET)
        assert r.returncode == 9
    b = preparer(chemin)
    travailler(b)
    resultat = photo(b)
    b.close()
    assert resultat == photo(base)
