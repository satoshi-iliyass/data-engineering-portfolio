"""Read one message. A doubtful message is refused: we never guess."""
import json
from datetime import datetime, timezone

LONGUEUR_MAX = 500
FORMAT_HEURE = "%Y-%m-%dT%H:%M:%SZ"


def fiche_sans_doublon(champs):
    fiche = dict(champs)
    if len(fiche) != len(champs):
        raise ValueError("champ écrit deux fois")
    return fiche


def lire_champ(message, nom):
    valeur = message.get(nom)
    if not isinstance(valeur, str):
        raise ValueError("message refusé, champ absent ou pas du texte : " + nom)
    if valeur == "" or valeur != valeur.strip():
        raise ValueError("message refusé, champ vide ou espace en trop : " + nom)
    if not valeur.isprintable():
        raise ValueError("message refusé, caractère illisible : " + nom)
    return valeur


def lire_heure(texte):
    try:
        heure = datetime.strptime(texte, FORMAT_HEURE)
    except ValueError:
        raise ValueError("message refusé, heure impossible : " + texte)
    if heure.isoformat() + "Z" != texte:
        raise ValueError("message refusé, heure mal écrite : " + texte)
    return heure.replace(tzinfo=timezone.utc)


def lire_message(ligne):
    if len(ligne) > LONGUEUR_MAX:
        raise ValueError("message refusé, trop long")
    try:
        message = json.loads(ligne, object_pairs_hook=fiche_sans_doublon)
    except ValueError:
        raise ValueError("message refusé, illisible : " + ligne)
    if not isinstance(message, dict):
        raise ValueError("message refusé, pas une fiche : " + ligne)
    return {
        "id": lire_champ(message, "id"),
        "machine": lire_champ(message, "machine"),
        "heure": lire_heure(lire_champ(message, "heure")),
    }
