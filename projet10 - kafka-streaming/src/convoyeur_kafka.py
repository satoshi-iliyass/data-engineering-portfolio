"""The real conveyor. Only this file knows Kafka."""
from confluent_kafka import Consumer, KafkaException, TopicPartition

ATTENTE = 10


class ConvoyeurKafka:
    def __init__(self, adresse, sujet):
        self.sujet = sujet
        self.lecteur = Consumer({
            "bootstrap.servers": adresse,
            "group.id": "compteur",
            "enable.auto.commit": False,
            "auto.offset.reset": "error",
        })

    def verifier_une_seule_piste(self):
        infos = self.lecteur.list_topics(self.sujet, timeout=ATTENTE).topics[self.sujet]
        if infos.error is not None:
            raise KafkaException(infos.error)
        if len(infos.partitions) != 1:
            raise ValueError(f"convoyeur refusé, une seule piste attendue, trouvé {len(infos.partitions)}")

    def limites(self):
        return self.lecteur.get_watermark_offsets(TopicPartition(self.sujet, 0), timeout=ATTENTE)

    def aller(self, position):
        self.lecteur.assign([TopicPartition(self.sujet, 0, position)])

    def suivant(self):
        message = self.lecteur.poll(1)
        if message is None:
            return None
        if message.error() is not None:
            raise KafkaException(message.error())
        return message.offset(), message.value()

    def fermer(self):
        self.lecteur.close()
