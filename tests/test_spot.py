import pytest

from ai_demand.errors import SourceError
from ai_demand.spot import parse_spot


def _payload():
    return {
        "ranges": [
            {"index": 0, "label": "<5%"},
            {"index": 1, "label": "5-10%"},
            {"index": 2, "label": "10-15%"},
            {"index": 3, "label": "15-20%"},
            {"index": 4, "label": ">20%"},
        ],
        "spot_advisor": {
            "us-east-1": {
                "Linux": {
                    "p5.48xlarge": {"s": 57, "r": 0},
                    "p4d.24xlarge": {"s": 56, "r": 3},
                }
            },
            "us-west-2": {
                "Linux": {
                    "p5.48xlarge": {"s": 69, "r": 0},
                    "p4d.24xlarge": {"s": 45, "r": 4},
                }
            },
        },
    }


def test_parse_spot_raw_index_and_labels():
    reading = parse_spot(_payload())
    assert reading.values == {
        "spot_use1_p5": 0,
        "spot_use1_p4d": 3,
        "spot_usw2_p5": 0,
        "spot_usw2_p4d": 4,
    }
    assert reading.labels["spot_use1_p5"] == "<5%"
    assert reading.labels["spot_use1_p4d"] == "15-20%"
    assert reading.labels["spot_usw2_p4d"] == ">20%"


def test_parse_spot_uses_range_label_from_the_payload():
    payload = _payload()
    payload["ranges"][0]["label"] = "under-five"
    reading = parse_spot(payload)
    assert reading.labels["spot_use1_p5"] == "under-five"


def test_parse_spot_omits_a_missing_instance():
    payload = _payload()
    del payload["spot_advisor"]["us-west-2"]["Linux"]["p5.48xlarge"]
    reading = parse_spot(payload)
    assert "spot_usw2_p5" not in reading.values
    assert reading.values["spot_usw2_p4d"] == 4


def test_parse_spot_fails_when_none_of_the_instances_exist():
    with pytest.raises(SourceError) as caught:
        parse_spot({"spot_advisor": {"us-east-1": {"Linux": {}}}, "ranges": []})
    assert caught.value.code == "not_in_response"


def test_parse_spot_falls_back_to_default_labels_without_ranges():
    payload = _payload()
    del payload["ranges"]
    reading = parse_spot(payload)
    assert reading.labels["spot_use1_p4d"] == "15-20%"
