"""Say if a message is new. This part only reads the sheet of seen messages, it never writes on it."""


def est_nouveau(fiche, feuille):
    if fiche["id"] not in feuille:
        return True
    if feuille[fiche["id"]] != fiche:
        raise ValueError("message refusé, même id mais contenu différent : " + fiche["id"])
    return False
