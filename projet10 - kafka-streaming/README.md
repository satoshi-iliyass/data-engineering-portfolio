# Kafka Streaming Counter — nothing lost, nothing counted twice

Machines send one message per part produced. This program reads the messages from **Kafka**, counts the parts **per machine and per minute**, and keeps the totals right even if it is killed in the middle of its work or receives the same message twice.

Typical client request: *"Our live counters are wrong after every restart: some events are missing, some are counted twice."*

```
                          ┌─ refused ──────────────► reject bin (message + reason)
 Kafka ──► read & check ──┤
                          └─ new or seen before? ──► total + sheet of seen ids + position
                                                     written together: all of it or nothing (SQLite)
 restart ──► recount the base ──► wrong: refuse to start
                             └──► right: go to the position saved in the base, not in Kafka
```

## Verified result (GitHub Actions, real Kafka 4.3.2 broker)

A stream of 24 messages (12 good, 4 sent again, 8 bad):

| Test | Result |
|---|---|
| Normal run | 12 counted, 4 seen before, 8 rejected with a reason |
| Second run | nothing changes |
| Process killed at **each** of the 24 messages, in the middle of a write | same base as without any crash |
| Process killed at **each** of the 24 messages, just after a write | same base as without any crash |
| Messages already deleted from Kafka | refuses to start instead of skipping them |
| Saved position beyond the end of the topic | refuses to start |
| Topic missing, 2 partitions, broker unreachable | stops with an error |

"Same base" = totals, seen ids, position and reject bin compared row by row.

## How it works

Small parts, each one tested alone, then assembled:

| Part | Job |
|---|---|
| `lire_message.py` | read one message; a doubtful one is refused, never guessed |
| `trouver_case.py` | find its box: machine + minute written in the message (late messages go to their own minute) |
| `est_nouveau.py` | new or seen before; same id with different content is refused |
| `enregistrer.py` | write total + seen id + position in one transaction |
| `reprendre.py` | recount everything from the seen ids before starting; a wrong base is refused, never repaired |
| `mettre_au_rebut.py` | keep a refused message with its reason and move on |
| `traiter.py` | assembly: a bad **message** goes to the bin, a **breakdown** stops the line |
| `brancher.py` | plug to the conveyor; checks the position first, and that nothing was left behind at the end |
| `convoyeur_kafka.py` | the only file that knows Kafka |
| `lancer.py` | start button |

**205 tests**: 195 without Kafka (real process kills on SQLite, fake conveyor) and 10 on a real broker. The parts and the assembly (all but `convoyeur_kafka.py` and `lancer.py`) were also broken on purpose, one line at a time: 171 of 172 breaks were caught, the last one has no effect.

## Run

```bash
pip install -r requirements.txt
python -m pytest -q                                          # without Kafka: 195 tests, the broker tests are skipped

docker run -d -p 9092:9092 apache/kafka:4.3.2
KAFKA_ADRESSE=localhost:9092 python -m pytest -q             # all 205 tests
python -m src.lancer localhost:9092 <topic> totals.db        # count an existing topic
```

Message format: `{"id": "e1", "machine": "M1", "heure": "2026-11-22T10:00:05Z"}`

## Limits

- One partition and one reader; no transactional producers.
- It stops after about 5 seconds without a message. A production service would keep running.
- The sheet of seen ids grows with every message and is reloaded at start: fine for a demo, it needs a cleanup rule for large volumes. Speed was not measured.
- A crash means a killed process. Power loss, and a broker failing while reading, were not tested on a real Kafka.
- If a topic is deleted and created again with the same name and more messages, this is not detected.
- Nobody empties the reject bin: someone has to look at it.

## Stack

Apache Kafka · Python · confluent-kafka · SQLite · GitHub Actions · pytest
