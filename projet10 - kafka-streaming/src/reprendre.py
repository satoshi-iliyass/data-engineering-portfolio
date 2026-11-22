"""Find where to start again. The base is recounted first: a wrong base is refused, never repaired. Reads only."""
from datetime import datetime

from src.trouver_case import trouver_case


def lire_feuille(base):
    feuille = {}
    for numero, machine, heure in base.execute("SELECT id, machine, heure FROM feuille"):
        try:
            feuille[numero] = {"id": numero, "machine": machine, "heure": datetime.fromisoformat(heure)}
        except (ValueError, TypeError):
            raise ValueError("base refusée, heure illisible : " + numero)
    return feuille


def lire_totaux(base):
    totaux = {}
    for machine, minute, total in base.execute("SELECT machine, minute, total FROM totaux"):
        totaux[(machine, minute)] = total
    return totaux


def recompter(feuille):
    totaux = {}
    for fiche in feuille.values():
        machine, minute = trouver_case(fiche)
        case = (machine, minute.isoformat())
        totaux[case] = totaux.get(case, 0) + 1
    return totaux


def reprendre(base):
    feuille = lire_feuille(base)
    if recompter(feuille) != lire_totaux(base):
        raise ValueError("base refusée, les totaux ne correspondent pas à la feuille")
    prochaine = base.execute("SELECT position FROM position").fetchone()[0] + 1
    if len(feuille) > prochaine:
        raise ValueError(f"base refusée, position trop basse : {len(feuille)} messages pour {prochaine} positions")
    return prochaine, feuille
