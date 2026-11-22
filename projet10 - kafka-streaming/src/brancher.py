"""The plug between the conveyor and the line. The base decides where to start, not Kafka."""
from src.reprendre import reprendre
from src.traiter import traiter


def verifier_position(prochaine, premiere, fin):
    if prochaine < premiere:
        raise ValueError(f"démarrage refusé, messages déjà effacés : demandé {prochaine}, premier restant {premiere}")
    if prochaine > fin:
        raise ValueError(f"démarrage refusé, position inconnue de Kafka : demandé {prochaine}, fin {fin}")


def verifier_tout_lu(prochaine, fin):
    if prochaine < fin:
        raise ValueError(f"arrêt anormal, il reste des messages : arrêté à {prochaine}, fin {fin}")


def brancher(base, convoyeur, silences_max):
    prochaine, feuille = reprendre(base)
    premiere, fin = convoyeur.limites()
    verifier_position(prochaine, premiere, fin)
    convoyeur.aller(prochaine)
    bilan = {"compté": 0, "déjà vu": 0, "rebut": 0}
    silences = 0
    while silences < silences_max:
        message = convoyeur.suivant()
        if message is None:
            silences = silences + 1
        else:
            position, octets = message
            bilan[traiter(base, feuille, octets, position)] += 1
            prochaine = position + 1
            silences = 0
    premiere, fin = convoyeur.limites()
    verifier_tout_lu(prochaine, fin)
    return bilan
