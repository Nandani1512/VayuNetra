from datetime import datetime, timezone

from vayunetra.serving.tile_cache import TileKey


def test_object_path_is_stable_and_hour_aligned():
    ts = datetime(2026, 6, 22, 13, 47, tzinfo=timezone.utc)
    k = TileKey(city="delhi", pollutant="pm25", horizon_h=24, ts_issued=ts)
    assert k.object_path() == "forecast/delhi/pm25/24/2026-06-22T13.geojson"
    assert k.redis_key() == "tile:" + k.object_path()


def test_object_path_normalizes_to_utc():
    from datetime import timedelta, timezone as tz

    ist = tz(timedelta(hours=5, minutes=30))
    ts = datetime(2026, 6, 22, 18, 0, tzinfo=ist)  # = 12:30 UTC
    k = TileKey(city="delhi", pollutant="pm25", horizon_h=48, ts_issued=ts)
    assert k.object_path() == "forecast/delhi/pm25/48/2026-06-22T12.geojson"
