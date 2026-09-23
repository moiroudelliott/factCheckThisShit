"""Données officielles structurées : séries Eurostat (server/indicators.py) et
votes de l'Assemblée nationale (server/votes.py) — sans réseau.

Lancer : python tests/test_official_data.py   (ou python -m pytest tests)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import indicators, votes  # noqa: E402

# Réponse JSON-stat réduite, forme exacte de l'API Eurostat
JSONSTAT = {
    "id": ["freq", "age", "unit", "sex", "geo", "time"], "size": [1, 1, 1, 1, 2, 3],
    "dimension": {"geo": {"category": {"index": {"EU27_2020": 0, "FR": 1}}},
                  "time": {"category": {"index": {"2023": 0, "2024": 1, "2025": 2}}}},
    "value": {"0": 5.9, "1": 5.9, "2": 6.0, "3": 7.3, "4": 7.4, "5": 7.7},
}


def test_eurostat_decoding_and_format():
    data = indicators.decode_jsonstat(JSONSTAT)
    assert data == {"EU27_2020": {"2023": 5.9, "2024": 5.9, "2025": 6.0}, "FR": {"2023": 7.3, "2024": 7.4, "2025": 7.7}}
    chomage = next(i for i in indicators.INDICATORS if i["key"] == "chomage")
    assert indicators.format_series(chomage, data) == "France : 2023 7,3 · 2024 7,4 · 2025 7,7 — UE-27 : 2024 5,9 · 2025 6 (%)"


def test_eurostat_triggers():
    keys = lambda c: [i["key"] for i in indicators.match(c)]  # noqa: E731
    assert keys("Le taux de chômage des jeunes a été divisé par deux") == ["chomage_jeunes"]
    assert keys("Le chômage est au plus bas depuis 25 ans") == ["chomage"]
    assert set(keys("La dette publique atteint 3 000 milliards d'euros")) == {"dette_pib", "dette_eur"}
    assert keys("Le déficit commercial atteint 100 milliards") == []
    assert keys("Le SMIC est à 1 400 euros") == ["smic"]


def _state():
    deputies = [{"ref": "PA1", "name": "Marine Le Pen", "norm": "marine le pen", "nom_norm": "le pen", "group": "PO_RN"},
                {"ref": "PA2", "name": "Manuel Bompard", "norm": "manuel bompard", "nom_norm": "bompard", "group": "PO_LFI"}]
    groups = {"PO_RN": {"abrev": "RN", "libelle": "Rassemblement National"},
              "PO_LFI": {"abrev": "LFI-NFP", "libelle": "La France insoumise - NFP"}}
    dep_index = {"PA1": 0, "PA2": 1}
    raw = {
        "numero": "3213", "dateScrutin": "2023-12-19",
        "titre": "l'ensemble du projet de loi pour contrôler l'immigration, améliorer l'intégration",
        "sort": {"code": "adopté"},
        "syntheseVote": {"decompte": {"pour": "349", "contre": "186", "abstentions": "38"}},
        "ventilationVotes": {"organe": {"groupes": {"groupe": [
            {"organeRef": "PO_RN_16", "vote": {"positionMajoritaire": "pour",
                                               "decompteVoix": {"pour": "88", "contre": "0", "abstentions": "0"},
                                               "decompteNominatif": {"pours": {"votant": {"acteurRef": "PA1"}}}}},
            {"organeRef": "PO_LFI_16", "vote": {"positionMajoritaire": "contre",
                                                "decompteVoix": {"pour": "0", "contre": "75", "abstentions": "0"},
                                                "decompteNominatif": {"contres": {"votant": [{"acteurRef": "PA2"}]}}}},
        ]}}},
    }
    members = {}
    s = votes.parse_scrutin(raw, 16, dep_index, 2, members)
    return deputies, groups, members, s


def test_vote_parsing():
    _, _, members, s = _state()
    assert s["positions"] == bytes([1, 2])                   # Le Pen pour, Bompard contre
    assert s["final"] and "2023" not in s["words"] and "immigr" in s["words"]
    assert members == {"PO_RN_16": {0: 1}, "PO_LFI_16": {1: 1}}


def test_past_groups_are_inferred_from_current_members():
    deputies, groups, _, _ = _state()
    members = {"PO_RN_16": {0: 40}, "PO_X": {1: 3}}
    deputies = deputies + [dict(deputies[0], ref=f"PA{i}") for i in range(3, 10)]
    members["PO_RN_16"] = {i: 1 for i in [0, 2, 3, 4, 5, 6]}
    inferred = votes.infer_past_groups(members, deputies, groups)
    assert inferred["PO_RN_16"]["abrev"] == "RN"
    assert "PO_X" not in inferred                             # moins de 5 membres : pas de déduction


def test_vote_search():
    deputies, groups, _, s = _state()
    groups = dict(groups, PO_RN_16={"abrev": "RN", "libelle": "RN (législature passée)"})
    state = {"ready": True, "deputies": deputies, "groups": groups, "scrutins": [s]}
    hit = votes.search("Marine Le Pen a voté contre la loi immigration", state)
    assert hit and "Marine Le Pen a voté pour" in hit[0]["body"] and hit[0]["href"].endswith("/16/scrutins/3213")
    hit = votes.search("Le RN a voté pour la loi immigration en décembre 2023", state)
    assert hit and "groupe RN : majoritairement pour" in hit[0]["body"]
    assert votes.search("Marine Le Pen parle de la loi immigration", state) == []   # pas un vote
    assert votes.search("Jordan Bardella a voté contre la loi immigration", state) == []  # pas député


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
