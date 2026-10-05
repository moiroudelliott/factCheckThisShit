"""Lecture des articles (server/articles.py) : texte d'une page, passage utile.

Lancer : python tests/test_articles.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.articles import extract_text, relevant_passage  # noqa: E402

PAGE = """<html><head><script>var x = "Le chômage a baissé de 2 points";</script><style>p{color:red}</style></head>
<body><nav><ul><li>Accueil — Politique — Économie — Le chômage en direct</li></ul></nav>
<h1>Blocage des lycées : le bilan des dégradations s'alourdit</h1>
<p>Le ministre de l'Éducation nationale a annoncé dimanche que <strong>24 établissements</strong> avaient
été incendiés ou saccagés depuis le début du mouvement.</p>
<p>À Nantes, la région Pays de la Loire précise qu'aucun élève ne se trouvait dans le bâtiment au moment
de l'incendie du lycée Nelson-Mandela, dont l'internat accueille 165 élèves.</p>
<p>Les syndicats appellent à une nouvelle journée de mobilisation mardi, avec la CGT.</p>
<footer><p>© Le Journal — Tous droits réservés, reproduction interdite sans autorisation.</p></footer>
</body></html>"""


def test_text_keeps_paragraphs_only():
    text = extract_text(PAGE)
    assert "24 établissements avaient été incendiés ou saccagés" in " ".join(text.split())
    assert "var x" not in text and "color:red" not in text       # scripts et styles
    assert "Accueil" not in text and "Tous droits réservés" not in text  # menu et pied de page
    assert extract_text("") == ""


def test_passage_is_what_speaks_about_the_claim():
    text = extract_text(PAGE)
    p = relevant_passage(text, "Le lycée Mandela a été incendié alors que 160 internes étaient à l'intérieur",
                         "incendie lycée Mandela Nantes internes", "2026")
    assert "aucun élève ne se trouvait dans le bâtiment" in p
    assert "CGT" not in p
    p = relevant_passage(text, "24 établissements scolaires ont été saccagés", "24 établissements saccagés lycées")
    assert p.startswith("Le ministre de l'Éducation nationale a annoncé dimanche que 24 établissements")


def test_no_passage_when_nothing_matches_and_size_is_bounded():
    text = extract_text(PAGE)
    assert relevant_passage(text, "Le prix du gazole a doublé en dix ans", "prix gazole doublé") == ""
    long_text = "\n".join(f"Le budget de l'Éducation nationale augmente de {n} millions d'euros cette année-là."
                          for n in range(100, 160))
    p = relevant_passage(long_text, "Le budget de l'Éducation nationale augmente", "budget Éducation nationale",
                         max_chars=300)
    assert 0 < len(p) <= 300


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
