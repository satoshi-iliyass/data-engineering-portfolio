"""On a real Kafka (GitHub Actions). Skipped without KAFKA_ADRESSE, unless REQUIRE_KAFKA says it must run."""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.test_traiter import COMPTES, CONVOYEUR, DEJA_VUS, REBUTS, photo, preparer, travailler

PROJET = Path(__file__).parent.parent
ADRESSE = os.environ.get("KAFKA_ADRESSE")
if not ADRESSE and os.environ.get("REQUIRE_KAFKA") != "1":
    pytest.skip("no Kafka here: runs in GitHub Actions", allow_module_level=True)

from confluent_kafka import KafkaException, Producer, TopicPartition  # noqa: E402
from confluent_kafka.admin import AdminClient, NewTopic  # noqa: E402

from src.lancer import lancer  # noqa: E402

ADMIN = AdminClient({"bootstrap.servers": ADRESSE})


def creer(pistes=1):
    sujet = "pieces-" + uuid.uuid4().hex[:8]
    for resultat in ADMIN.create_topics([NewTopic(sujet, pistes, 1)]).values():
        resultat.result(30)
    time.sleep(1)
    return sujet


def envoyer(sujet, messages):
    erreurs = []
    facteur = Producer({"bootstrap.servers": ADRESSE, "acks": "all"})
    for octets in messages:
        facteur.produce(sujet, value=octets, partition=0, on_delivery=lambda e, m: erreurs.append(e) if e else None)
    assert facteur.flush(30) == 0 and erreurs == []


def lire(chemin):
    b = preparer(chemin)
    p = photo(b)
    b.close()
    return p


@pytest.fixture
def sujet():
    s = creer()
    envoyer(s, CONVOYEUR)
    return s


@pytest.fixture
def reference(tmp_path):
    b = preparer(tmp_path / "reference.db")
    travailler(b)
    p = photo(b)
    b.close()
    return p


def test_tout_le_convoyeur_puis_rien_de_plus(tmp_path, sujet, reference):
    chemin = tmp_path / "base.db"
    assert lancer(ADRESSE, sujet, chemin) == {"compté": COMPTES, "déjà vu": DEJA_VUS, "rebut": REBUTS}
    assert lire(chemin) == reference
    assert lancer(ADRESSE, sujet, chemin) == {"compté": 0, "déjà vu": 0, "rebut": 0}
    assert lire(chemin) == reference


def test_messages_arrives_entre_deux_passages(tmp_path, reference):
    s, chemin = creer(), tmp_path / "base.db"
    envoyer(s, CONVOYEUR[:10])
    lancer(ADRESSE, s, chemin)
    envoyer(s, CONVOYEUR[10:])
    lancer(ADRESSE, s, chemin)
    assert lire(chemin) == reference


COUPURE = """
import os, sys
import src.enregistrer, src.mettre_au_rebut
import src.traiter as piece
from src.lancer import lancer
moment, mort = sys.argv[4], int(sys.argv[5])
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
lancer(*sys.argv[1:4])
"""


@pytest.mark.parametrize("moment", ["au milieu", "juste apres"])
def test_tue_a_chaque_message_meme_resultat(tmp_path, sujet, reference, moment):
    chemin = tmp_path / "panne.db"
    for mort in range(len(CONVOYEUR)):
        r = subprocess.run([sys.executable, "-c", COUPURE, ADRESSE, sujet, str(chemin), moment, str(mort)],
                           cwd=PROJET, timeout=60)
        assert r.returncode == 9
    lancer(ADRESSE, sujet, chemin)
    assert lire(chemin) == reference


def test_messages_effaces_dans_kafka_refuse(tmp_path, sujet):
    for resultat in ADMIN.delete_records([TopicPartition(sujet, 0, 5)]).values():
        resultat.result(30)
    with pytest.raises(ValueError, match="messages déjà effacés : demandé 0, premier restant 5"):
        lancer(ADRESSE, sujet, tmp_path / "base.db")
    assert lire(tmp_path / "base.db") == [[], [], [(1, -1)], []]


def test_position_trop_haute_refusee(tmp_path, sujet):
    chemin = tmp_path / "base.db"
    lancer(ADRESSE, sujet, chemin)
    autre = creer()
    envoyer(autre, CONVOYEUR[:3])
    with pytest.raises(ValueError, match="position inconnue de Kafka"):
        lancer(ADRESSE, autre, chemin)


def test_deux_pistes_refuse(tmp_path):
    with pytest.raises(ValueError, match="une seule piste attendue, trouvé 2"):
        lancer(ADRESSE, creer(2), tmp_path / "base.db")


def test_convoyeur_inconnu_refuse_et_pas_cree(tmp_path):
    nom = "absent-" + uuid.uuid4().hex[:8]
    with pytest.raises(KafkaException):
        lancer(ADRESSE, nom, tmp_path / "base.db")
    assert nom not in ADMIN.list_topics(timeout=10).topics


def test_kafka_injoignable(tmp_path):
    with pytest.raises(KafkaException):
        lancer("localhost:1", "pieces", tmp_path / "base.db")


def test_commande(tmp_path, sujet):
    r = subprocess.run([sys.executable, "-m", "src.lancer", ADRESSE, sujet, str(tmp_path / "base.db")],
                       cwd=PROJET, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and f"'compté': {COMPTES}" in r.stdout
