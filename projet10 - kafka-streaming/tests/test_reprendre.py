import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.enregistrer as piece4
from src.enregistrer import ouvrir_base
from src.est_nouveau import est_nouveau
from src.reprendre import reprendre
from src.trouver_case import trouver_case

PROJET = Path(__file__).parent.parent


def fiche(numero, machine="M1", minute=0):
    return {"id": numero, "machine": machine, "heure": datetime(2026, 11, 22, 10, minute, 5, tzinfo=timezone.utc)}


# 20 messages, the last 5 are messages already sent before
CONVOYEUR = [fiche(f"e{n}", f"M{n % 2}", n % 3) for n in range(15)] + [fiche(f"e{n}", f"M{n % 2}", n % 3) for n in range(5)]


def travailler(base):
    prochaine, feuille = reprendre(base)
    for position in range(prochaine, len(CONVOYEUR)):
        f = CONVOYEUR[position]
        nouveau = est_nouveau(f, feuille)
        piece4.enregistrer(base, f, trouver_case(f), nouveau, position)
        feuille[f["id"]] = f


def photo(base):
    return [base.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall() for table in ("totaux", "feuille", "position")]


@pytest.fixture
def base(tmp_path):
    b = ouvrir_base(tmp_path / "base.db")
    yield b
    b.close()


@pytest.fixture
def pleine(base):
    travailler(base)
    return base


def test_premier_demarrage(base):
    assert reprendre(base) == (0, {})


def test_recommence_juste_apres_et_rend_la_feuille(pleine):
    assert reprendre(pleine) == (20, {f["id"]: f for f in CONVOYEUR[:15]})


def test_recommence_apres_une_position_sautee(base):
    f = fiche("e1")
    piece4.enregistrer(base, f, trouver_case(f), True, 41)
    assert reprendre(base) == (42, {"e1": f})


def test_lit_seulement(pleine):
    avant = photo(pleine)
    reprendre(pleine)
    assert photo(pleine) == avant
    assert pleine.in_transaction is False


@pytest.mark.parametrize("degat", [
    "UPDATE totaux SET total = total + 1 WHERE machine = 'M1' AND minute LIKE '%10:00%'",
    "DELETE FROM totaux WHERE machine = 'M1' AND minute LIKE '%10:00%'",
    "INSERT INTO totaux VALUES ('M9', '2026-11-22T10:00:00+00:00', 1)",
    "DELETE FROM feuille WHERE id = 'e3'",
    "INSERT INTO feuille VALUES ('e99', 'M1', '2026-11-22T10:00:05+00:00')",
    "UPDATE feuille SET machine = 'M9' WHERE id = 'e3'",
    "UPDATE feuille SET heure = '2026-11-22T10:30:00+00:00' WHERE id = 'e3'",
])
def test_totaux_et_feuille_pas_d_accord(pleine, degat):
    pleine.execute(degat)
    with pytest.raises(ValueError, match="les totaux ne correspondent pas"):
        reprendre(pleine)


def test_meme_somme_mais_mauvaises_cases(pleine):
    pleine.execute("UPDATE totaux SET total = total + 1 WHERE machine = 'M1' AND minute LIKE '%10:00%'")
    pleine.execute("UPDATE totaux SET total = total - 1 WHERE machine = 'M0' AND minute LIKE '%10:00%'")
    with pytest.raises(ValueError, match="les totaux ne correspondent pas"):
        reprendre(pleine)


@pytest.mark.parametrize("heure", ["hier", "", None])
def test_heure_abimee(pleine, heure):
    pleine.execute("UPDATE feuille SET heure = ? WHERE id = 'e3'", (heure,))
    with pytest.raises(ValueError, match="heure illisible : e3"):
        reprendre(pleine)


def test_heure_sans_fuseau(pleine):
    pleine.execute("UPDATE feuille SET heure = '2026-11-22T10:00:05' WHERE id = 'e3'")
    with pytest.raises(ValueError, match="pas de fuseau horaire"):
        reprendre(pleine)


@pytest.mark.parametrize("position,accepte", [(-1, False), (13, False), (14, True)])
def test_position_trop_basse(pleine, position, accepte):
    pleine.execute("UPDATE position SET position = ?", (position,))
    if accepte:
        assert reprendre(pleine)[0] == 15
    else:
        with pytest.raises(ValueError, match="position trop basse"):
            reprendre(pleine)


COUPURE = """
import os, sys
import src.enregistrer as piece
from tests.test_reprendre import travailler
chemin, etape, mort = sys.argv[1], sys.argv[2], int(sys.argv[3])
vraie = getattr(piece, etape)
def coupe(*a):
    vraie(*a)
    if a[-1] == mort:
        os._exit(9)
setattr(piece, etape, coupe)
travailler(piece.ouvrir_base(chemin))
"""


@pytest.mark.parametrize("etape", ["avancer", "enregistrer"])
def test_tue_20_fois_meme_resultat_que_sans_panne(tmp_path, pleine, etape):
    chemin = tmp_path / "panne.db"
    for mort in range(len(CONVOYEUR)):
        r = subprocess.run([sys.executable, "-B", "-c", COUPURE, str(chemin), etape, str(mort)], cwd=PROJET)
        assert r.returncode == 9
    b = ouvrir_base(chemin)
    travailler(b)
    resultat = photo(b)
    b.close()
    assert resultat == photo(pleine)
    assert sum(total for _, _, total in resultat[0]) == 15
