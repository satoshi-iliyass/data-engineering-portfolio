"""Write the total, the sheet and the position together: all of it or nothing."""
import sqlite3

TABLES = """
CREATE TABLE IF NOT EXISTS totaux (machine TEXT, minute TEXT, total INTEGER, PRIMARY KEY (machine, minute));
CREATE TABLE IF NOT EXISTS feuille (id TEXT PRIMARY KEY, machine TEXT, heure TEXT);
CREATE TABLE IF NOT EXISTS position (une_seule_ligne INTEGER PRIMARY KEY CHECK (une_seule_ligne = 1), position INTEGER);
INSERT OR IGNORE INTO position VALUES (1, -1);
"""


def ouvrir_base(chemin):
    base = sqlite3.connect(chemin)
    base.executescript(TABLES)
    return base


def avancer(base, position):
    derniere = base.execute("SELECT position FROM position").fetchone()[0]
    if position <= derniere:
        raise ValueError(f"position refusée, déjà passée : {position}, dernière : {derniere}")
    base.execute("UPDATE position SET position = ?", (position,))


def noter(base, fiche):
    base.execute("INSERT INTO feuille VALUES (?, ?, ?)", (fiche["id"], fiche["machine"], fiche["heure"].isoformat()))


def compter(base, case):
    machine, minute = case
    base.execute("INSERT INTO totaux VALUES (?, ?, 1) ON CONFLICT (machine, minute) DO UPDATE SET total = total + 1",
                 (machine, minute.isoformat()))


def verifier_deja_vu(base, fiche):
    if base.execute("SELECT id FROM feuille WHERE id = ?", (fiche["id"],)).fetchone() is None:
        raise ValueError("refusé, annoncé déjà vu mais absent de la feuille : " + fiche["id"])


def enregistrer(base, fiche, case, nouveau, position):
    with base:
        base.execute("BEGIN IMMEDIATE")
        avancer(base, position)
        if nouveau:
            noter(base, fiche)
            compter(base, case)
        else:
            verifier_deja_vu(base, fiche)
