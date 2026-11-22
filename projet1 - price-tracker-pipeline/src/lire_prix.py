"""Read one price. A doubtful price is refused: we never guess."""


def que_des_chiffres(texte):
    if texte == "":
        return False
    for caractere in texte:
        if caractere not in "0123456789":
            return False
    return True


def lire_prix(texte):
    prix = texte.strip().removeprefix("Â")
    if not prix.startswith("£"):
        raise ValueError("prix douteux, pas de £ : " + texte)
    morceaux = prix.removeprefix("£").split(".")
    if len(morceaux) != 2 or len(morceaux[1]) != 2:
        raise ValueError("prix douteux, format attendu £51.77 : " + texte)
    if not que_des_chiffres(morceaux[0]) or not que_des_chiffres(morceaux[1]):
        raise ValueError("prix douteux, pas que des chiffres : " + texte)
    return float(morceaux[0] + "." + morceaux[1])
