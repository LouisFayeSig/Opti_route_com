from __future__ import annotations

import json

from opti_route.map_view import _persistent_label_layer


def test_persistent_labels_use_literal_alignment_and_visible_background() -> None:
    points = [
        {
            "latitude": 49.18,
            "longitude": -0.37,
            "map_label": "1. Entreprise Démo",
        }
    ]

    serialized = json.loads(_persistent_label_layer(points).to_json())

    assert serialized["getText"] == "@@=map_label"
    assert serialized["getAlignmentBaseline"] == "bottom"
    assert serialized["getTextAnchor"] == "middle"
    assert serialized["background"] is True
    assert serialized["characterSet"] == "auto"
