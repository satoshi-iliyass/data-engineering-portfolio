"""Find the box of a message: its machine and its minute. The time is the one written in the message."""


def trouver_case(fiche):
    heure = fiche["heure"]
    if heure.utcoffset() is None:
        raise ValueError("heure refusée, pas de fuseau horaire : " + str(heure))
    minute = heure.replace(second=0, microsecond=0)
    return fiche["machine"], minute
