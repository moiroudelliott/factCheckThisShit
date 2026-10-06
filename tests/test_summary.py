"""Fiche de fin de débat (server/summary.py) : chiffres calculés par le code,
rédaction de Mistral simulée et passée au crible — sans réseau ni serveur.

Lancer : python tests/test_summary.py   (ou python -m pytest tests)"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import summary  # noqa: E402
from server.themes import normalize_theme  # noqa: E402

A, B = "Gabriel Attal", "Jordan Bardella"


def _pt(i, qui, t, theme="budget", type_="affirmation", texte=None, enjeu=8):
    return {"id": f"p{i}", "type": type_, "texte": texte or f"propos {i}", "citation": "", "qui": qui,
            "theme": theme, "t": t, "enjeu": enjeu}


def _fiche(points, verdicts, speech=(), redaction=None):
    return summary.build_fiche(points, verdicts, list(speech), 600.0, redaction=redaction)


def test_themes_are_a_fixed_list():
    assert normalize_theme("energie") == "energie"
    assert normalize_theme("Énergie") == "energie"            # libellé accepté
    assert normalize_theme("Pouvoir d'achat") == "pouvoir_achat"
    assert normalize_theme("cybersécurité") == "autre"         # inventé → autre
    assert normalize_theme(None) == "autre"


def test_index_has_a_margin_even_when_everything_is_true():
    mean, marge, n = summary.indice({"vrai": 15})
    assert mean == 1.0 and n == 15 and marge > 0.05            # jamais « 100 % ± 0 »
    mean, marge, n = summary.indice({"vrai": 10, "partiellement_vrai": 4, "faux": 6})
    assert round(mean, 2) == 0.6 and marge > 0
    assert summary.indice({"non_verifiable": 3}) == (None, None, 0)


def test_speaker_names_come_from_the_final_voice_map():
    smap = {"Intervenant C": A}
    assert summary.resolve_name("Intervenant C", "Intervenant C", smap) == A
    assert summary.resolve_name(B, "Intervenant D", smap) == B     # nom porté par le point
    assert summary.resolve_name("Intervenant D", "Intervenant D", smap) == ""  # jamais identifié
    assert summary.resolve_name("?", "", smap) == ""


def test_index_only_above_the_minimum_and_speakers_never_ranked_by_score():
    points = [_pt(i, A, i * 10) for i in range(20)] + [_pt(100 + i, B, i * 10) for i in range(5)]
    verdicts = {f"p{i}": {"verdict": "faux"} for i in range(20)}
    verdicts.update({f"p{100 + i}": {"verdict": "vrai"} for i in range(5)})
    f = _fiche(points, verdicts, speech=[(0, 300, B), (300, 100, A)])
    assert [d["nom"] for d in f["debatteurs"]] == [B, A]         # temps de parole, pas l'exactitude
    attal, bardella = f["debatteurs"][1], f["debatteurs"][0]
    assert attal["suffisant"] and attal["exactitude"] == 0
    assert not bardella["suffisant"] and bardella["exactitude"] is None  # 5 verdicts : pas d'indice
    assert f["comparaisons"] == []                               # rien à comparer à un seul indice


def test_inaudible_claims_count_nowhere():
    points = [_pt(1, A, 10), _pt(2, A, 20)]
    verdicts = {"p1": {"verdict": "non_verifiable", "explication": "Transcription douteuse : chiffre illisible."},
                "p2": {"verdict": "vrai"}}
    f = _fiche(points, verdicts)
    assert [a["id"] for a in f["affirmations"]] == ["p2"]


def test_frise_smooths_isolated_points_and_short_chapters():
    themes = ["budget"] * 6 + ["energie"] + ["budget"] * 6 + ["immigration"] * 8
    points = [_pt(i, A, i * 40, theme=th, type_="argument") for i, th in enumerate(themes)]
    frise = summary.build_frise(points, 21 * 40)
    assert [s["theme"] for s in frise] == ["budget", "immigration"]
    assert frise[0]["debut"] == 0.0 and frise[-1]["fin"] == 840
    assert frise[0]["fin"] == frise[1]["debut"]


def _redaction_case():
    points = [_pt(1, A, 10, texte="Le SMIC a augmenté de 47 %"), _pt(2, B, 20, texte="Les éoliennes durent 10 ans"),
              _pt(3, A, 30, texte="Les éoliennes durent 25 ans"), _pt(4, B, 40, type_="argument",
                                                                       texte="Baisser la TVA sur l'énergie"),
              _pt(5, A, 50, texte="Le déficit est de 5,5 %")]
    points[3]["citation"] = "dès l'été, j'entends baisser la TVA sur l'énergie"
    verdicts = {"p1": {"verdict": "faux", "explication": "Le SMIC a augmenté de 38 % selon l'Insee.", "confiance": 90},
                "p2": {"verdict": "faux", "explication": "Elles durent 20 à 25 ans.", "confiance": 80},
                "p3": {"verdict": "vrai", "explication": "20 à 25 ans selon l'Ademe.", "confiance": 90},
                "p5": {"verdict": "vrai", "explication": "5,5 % du PIB en 2023.", "confiance": 90}}
    return points, verdicts


def test_redaction_keeps_only_checked_choices():
    points, verdicts = _redaction_case()
    f = _fiche(points, verdicts)
    raw = {
        "resume": "Un débat sur le budget et l'énergie.",
        "moments": [{"id": "p1", "pourquoi": "Le cœur de son bilan."}, {"id": "zz", "pourquoi": "inventé"},
                    {"id": "p3", "pourquoi": "Chiffre clé."}, {"id": "p4", "pourquoi": "pas un verdict"}],
        "chiffres": [{"id": "p1", "annonce": "+47 %", "selon_source": "+38 % selon l'Insee"},
                     {"id": "p2", "annonce": "10 ans", "selon_source": "30 ans"}],  # 30 absent de l'explication
        "contradictions": [{"ids": ["p1", "p3"], "sujet": "même orateur"}, {"ids": ["p1", "p2"], "sujet": "énergie"}],
        "propositions": [{"id": "p4", "intitule": "Baisser la TVA sur l'énergie"}, {"id": "zz", "intitule": "x"}],
    }
    red = summary.validate_redaction(raw, f["affirmations"], points, candidates={"p1", "p2"})
    assert [m["id"] for m in red["moments"]] == ["p1"]        # p3 hors candidats, p4 sans verdict, zz inconnu
    assert red["moments"][0]["genre"] == "erreur"
    assert [c["id"] for c in red["chiffres"]] == ["p1"]       # chiffre de la source introuvable → écarté
    assert summary._rounding(frozenset({"350000"}), frozenset({"347000"}))  # arrondi : pas une erreur de chiffre
    assert not summary._rounding(frozenset({"47"}), frozenset({"19.3"}))
    assert red["contradictions"] == []   # même orateur ; deux « faux » ne se contredisent pas
    raw["contradictions"] = [{"ids": ["p2", "p3"], "sujet": "x"}, {"ids": ["p2", "p5"], "sujet": "rien en commun"}]
    verdicts_ok = summary.validate_redaction(raw, f["affirmations"], points)
    # faux (Bardella) / vrai (Attal) sur le même fait ; sans mot-clé commun, écartée
    assert [c["ids"] for c in verdicts_ok["contradictions"]] == [["p2", "p3"]]
    assert [(p["id"], p["qui"]) for p in red["propositions"]] == [("p4", B)]
    # proposition tirée d'une affirmation jugée fausse (programme adverse mal décrit) : écartée
    raw["propositions"] = [{"id": "p1", "intitule": "Augmenter le SMIC de 47 %"}]
    assert summary.validate_redaction(raw, f["affirmations"], points)["propositions"] == []
    # la mesure doit être la sienne, dans ses mots exacts (cas vécu : Attal décrivant la CSG progressive du NFP)
    points[3]["citation"] = "vous avez proposé dans le dernier budget la CSG progressive"
    raw["propositions"] = [{"id": "p4", "intitule": "Instaurer une CSG progressive"}]
    assert summary.validate_redaction(raw, f["affirmations"], points)["propositions"] == []
    points[3]["citation"] = "il faut baisser la TVA sur l'énergie"
    assert len(summary.validate_redaction(raw, f["affirmations"], points)["propositions"]) == 1


def test_a_moment_whose_verdict_changed_is_dropped():
    """Rédaction écrite, puis affirmation revérifiée (rejuger_session.py) :
    l'« erreur » devenue vraie ne garde pas son « pourquoi »."""
    points, verdicts = _redaction_case()
    stored = {"moments": [{"id": "p1", "qui": A, "genre": "erreur", "pourquoi": "Il exagère."}]}
    verdicts["p1"] = {"verdict": "vrai", "explication": "Exact."}
    f = _fiche(points, verdicts, redaction=stored)
    assert f["redaction"]["moments"] == []


def test_write_redaction_sends_candidates_and_validates_the_answer():
    points, verdicts = _redaction_case()
    f = _fiche(points, verdicts, speech=[(0, 100, A), (100, 50, B)])
    prompts = []

    def fake(prompt, **k):
        prompts.append((prompt, k))
        return "Voici : " + json.dumps({"resume": "Budget.", "moments": [{"id": "p2", "pourquoi": "Durée de vie."}]})
    red = summary.write_redaction(f, points, call=fake, model="mistral-large-latest")
    assert red["modele"] == "mistral-large-latest" and red["resume"] == "Budget."
    assert [m["id"] for m in red["moments"]] == ["p2"]
    prompt, kwargs = prompts[0]
    assert "[p1] Gabriel Attal · faux" in prompt and kwargs["model"] == "mistral-large-latest"


def test_a_programme_description_is_not_a_striking_figure():
    points = [_pt(1, A, 10, texte="Le programme du NFP propose un SMIC à 1 600 euros"),
              _pt(2, A, 20, texte="Le chômage est à 7,4 %")]
    verdicts = {"p1": {"verdict": "vrai"}, "p2": {"verdict": "vrai"}}
    f = _fiche(points, verdicts)
    assert [a["id"] for a in summary.moment_candidates(f["affirmations"], points, [A])] == ["p2"]
    # rédaction déjà écrite (relue à la publication) : écartée aussi
    stored = {"moments": [{"id": "p1", "genre": "exact", "pourquoi": "x"}, {"id": "p2", "genre": "exact", "pourquoi": "y"}]}
    assert [m["id"] for m in _fiche(points, verdicts, redaction=stored)["redaction"]["moments"]] == ["p2"]


def test_inputs_from_tape():
    tape = {"events": [
        {"t": 5.0, "m": {"type": "speaker_live", "speaker": "Intervenant C"}},
        {"t": 7.0, "m": {"type": "speaker_live", "speaker": "Intervenant C"}},
        {"t": 30.0, "m": {"type": "speaker_live", "speaker": "Intervenant D"}},
        {"t": 31.0, "m": {"type": "talking_points", "points": [
            {"id": "a1", "type": "affirmation", "texte": "x", "qui": "Intervenant C", "qui_label": "Intervenant C",
             "vt": 20.0, "theme": "Énergie"}]}},
        {"t": 40.0, "m": {"type": "fact_check_result", "id": "a1", "verdict": "vrai"}},
        {"t": 50.0, "m": {"type": "speaker_map", "map": {"Intervenant C": A}}},
        {"t": 60.0, "m": {"type": "debate_summary", "fiche": {"titre": "Débat", "date": "2024-06-27",
                                                              "redaction": {"resume": "r"}}}},
    ]}
    inp = summary.inputs_from_tape(tape)
    assert inp["points"][0]["qui"] == A and inp["points"][0]["theme"] == "energie" and inp["points"][0]["t"] == 20.0
    assert inp["verdicts"]["a1"]["verdict"] == "vrai"
    assert inp["speech"] == [(5.0, 2.0, A), (7.0, 5.0, A)]   # écart plafonné à 5 s
    assert inp["duration"] == 60.0 and inp["date"] == "2024-06-27" and inp["redaction"] == {"resume": "r"}


def test_inputs_from_live():
    points = [{"id": "a1", "type": "affirmation", "texte": "x", "qui": "Intervenant C", "qui_label": "Intervenant C",
               "said_at": 1030.0, "ts": 1020.0, "theme": "budget", "enjeu": 9},
              {"id": "a2", "type": "argument", "texte": "y", "qui": "", "ts": 1050.0}]
    inp = summary.inputs_from_live(points, [{"id": "a1", "verdict": "faux", "claim": "x"}],
                                   [(12.0, 3.0, "Intervenant C")], {"Intervenant C": A}, 1000.0, 1100.0)
    assert [(p["qui"], p["t"]) for p in inp["points"]] == [(A, 30.0), ("", 42.0)]  # sans citation : ts − 8 s
    assert inp["verdicts"]["a1"]["verdict"] == "faux" and inp["speech"] == [(12.0, 3.0, A)]
    assert inp["duration"] == 100.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
