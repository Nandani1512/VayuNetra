from vayunetra.ingestion.utils import city_bbox, load_city_config, pollutant_from_id


def test_delhi_bbox_loads():
    bbox = city_bbox("delhi")
    assert bbox == (76.84, 28.40, 77.35, 28.88)


def test_city_config_has_required_fields():
    cfg = load_city_config("bengaluru")
    for key in ("id", "name", "bbox", "timezone", "default_lang", "primary_pollutant"):
        assert key in cfg


def test_pollutant_lookup():
    assert pollutant_from_id(2) == "pm25"
    assert pollutant_from_id(5) == "no2"
    assert pollutant_from_id(999) is None
    assert pollutant_from_id(None) is None
