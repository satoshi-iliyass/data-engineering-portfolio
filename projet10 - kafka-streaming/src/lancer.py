"""Start button: python -m src.lancer <kafka address> <topic> <base file>"""
import sys

from src.brancher import brancher
from src.convoyeur_kafka import ConvoyeurKafka
from src.enregistrer import ouvrir_base
from src.mettre_au_rebut import ouvrir_bac

SILENCES_MAX = 5


def lancer(adresse, sujet, chemin):
    base = ouvrir_base(chemin)
    ouvrir_bac(base)
    convoyeur = ConvoyeurKafka(adresse, sujet)
    try:
        convoyeur.verifier_une_seule_piste()
        return brancher(base, convoyeur, SILENCES_MAX)
    finally:
        convoyeur.fermer()
        base.close()


if __name__ == "__main__":
    print(lancer(*sys.argv[1:]))
