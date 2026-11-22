"""Assembly: pass one raw message through the parts. A bad message goes to the bin, a breakdown stops the line."""
from src.enregistrer import enregistrer
from src.est_nouveau import est_nouveau
from src.lire_message import lire_message
from src.mettre_au_rebut import mettre_au_rebut
from src.trouver_case import trouver_case


def octets_en_texte(octets):
    if octets is None:
        raise ValueError("message refusé, vide")
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("message refusé, octets abîmés")


def texte_pour_le_bac(octets):
    if octets is None:
        return ""
    return octets.decode("utf-8", "backslashreplace")


def traiter(base, feuille, octets, position):
    try:
        fiche = lire_message(octets_en_texte(octets))
        nouveau = est_nouveau(fiche, feuille)
    except ValueError as refus:
        mettre_au_rebut(base, texte_pour_le_bac(octets), str(refus), position)
        return "rebut"
    enregistrer(base, fiche, trouver_case(fiche), nouveau, position)
    if nouveau:
        feuille[fiche["id"]] = fiche
        return "compté"
    return "déjà vu"
