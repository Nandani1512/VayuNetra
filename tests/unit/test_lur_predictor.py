import numpy as np

from vayunetra.models.lur.predictor import _idw_interpolate


def test_idw_returns_zeros_when_no_sources():
    out = _idw_interpolate(
        np.array([]), np.array([]), np.array([]),
        np.array([77.0, 77.1]), np.array([28.5, 28.6]),
    )
    assert np.allclose(out, 0.0)


def test_idw_recovers_exact_value_at_source_point():
    out = _idw_interpolate(
        src_lon=np.array([77.0]),
        src_lat=np.array([28.5]),
        src_val=np.array([42.0]),
        dst_lon=np.array([77.0, 77.5]),
        dst_lat=np.array([28.5, 28.6]),
    )
    # Single source → constant field equal to that source value.
    np.testing.assert_allclose(out, [42.0, 42.0], atol=1e-9)


def test_idw_weights_closer_source_more_heavily():
    src_lon = np.array([77.0, 77.5])
    src_lat = np.array([28.5, 28.5])
    src_val = np.array([100.0, 0.0])
    # Probe right next to the first source.
    out = _idw_interpolate(src_lon, src_lat, src_val,
                           np.array([77.01]), np.array([28.5]))
    assert out[0] > 50.0
