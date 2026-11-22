import pytest

from src.lire_prix import lire_prix, que_des_chiffres


@pytest.mark.parametrize("texte,attendu", [("£51.77", 51.77), ("Â£9.50", 9.5), ("  £10.00 ", 10.0), ("£0.99", 0.99)])
def test_prix_acceptes(texte, attendu):
    assert lire_prix(texte) == attendu


@pytest.mark.parametrize("texte", ["51,77 €", "2 pour 10", "gratuit", "", "£", "£1,299.00", "£1.299", "£20", "£5.5",
                                   "£-5.00", "£.50", "51.77", "£1.2.3", "£5a.00", "£5.0a", "£٣.٠٠"])
def test_prix_douteux_refuses(texte):
    with pytest.raises(ValueError, match="prix douteux"):
        lire_prix(texte)


def test_que_des_chiffres():
    assert que_des_chiffres("0123456789") is True
    assert que_des_chiffres("") is False
    assert que_des_chiffres("12a") is False
