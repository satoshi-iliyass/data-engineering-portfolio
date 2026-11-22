"""Keep a refused message with its reason, and move on. Both are written together."""
from src.enregistrer import avancer

LONGUEUR_GARDEE = 500


def ouvrir_bac(base):
    base.execute("CREATE TABLE IF NOT EXISTS rebuts (position INTEGER PRIMARY KEY, texte TEXT, raison TEXT)")


def rendre_rangeable(texte):
    return texte[:LONGUEUR_GARDEE].encode("utf-8", "replace").decode("utf-8")


def mettre_au_rebut(base, texte, raison, position):
    if raison.strip() == "":
        raise ValueError("rebut refusé, il faut une raison")
    with base:
        base.execute("BEGIN IMMEDIATE")
        avancer(base, position)
        base.execute("INSERT INTO rebuts VALUES (?, ?, ?)",
                     (position, rendre_rangeable(texte), rendre_rangeable(raison)))
