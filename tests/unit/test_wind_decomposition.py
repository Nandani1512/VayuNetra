from math import isclose

from vayunetra.ingestion.open_meteo import _wind_to_uv


def test_wind_from_north_blows_southward():
    u, v = _wind_to_uv(10.0, 0.0)  # wind FROM north
    assert isclose(u, 0.0, abs_tol=1e-6)
    assert v < 0  # blowing southward (negative v)


def test_wind_from_east_blows_westward():
    u, v = _wind_to_uv(10.0, 90.0)
    assert u < 0
    assert isclose(v, 0.0, abs_tol=1e-6)


def test_none_inputs_return_none():
    assert _wind_to_uv(None, 90.0) == (None, None)
    assert _wind_to_uv(5.0, None) == (None, None)
