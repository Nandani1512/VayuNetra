from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from vayunetra.api.schemas import AdvisoryResponse
from vayunetra.storage.db import get_engine

router = APIRouter(prefix="/advisory", tags=["advisory"])

# Pre-translated advisory templates. 4 severity bands × 12 languages = 48
# strings. Production would load these from data/templates/advisory_<lang>.json
# (built by scripts/translate_templates.py with IndicTrans2). For the demo we
# inline a Hindi+English+Kannada subset.
TEMPLATES = {
    "en": {
        "good": ("Air quality is good", "Enjoy outdoor activities. AQI is {aqi} µg/m³ — well within safe limits."),
        "moderate": ("Air quality is moderate", "Sensitive groups should reduce prolonged outdoor exertion. PM2.5 is {aqi} µg/m³."),
        "poor": ("Air quality is poor", "Reduce outdoor activity. Use an N95 mask if going outside. PM2.5 is {aqi} µg/m³."),
        "severe": ("Severe air quality alert", "Stay indoors. Children, elderly, and asthmatics must not go outside. PM2.5 is {aqi} µg/m³."),
    },
    "hi": {
        "good": ("वायु गुणवत्ता अच्छी है", "बाहरी गतिविधियों का आनंद लें। AQI {aqi} µg/m³ है — सुरक्षित सीमा में।"),
        "moderate": ("वायु गुणवत्ता मध्यम है", "संवेदनशील लोग लंबे समय तक बाहर रहने से बचें। PM2.5 {aqi} µg/m³ है।"),
        "poor": ("वायु गुणवत्ता ख़राब है", "बाहर निकलने से बचें, मास्क पहनें। PM2.5 {aqi} µg/m³ है।"),
        "severe": ("गंभीर वायु प्रदूषण चेतावनी", "घर के अंदर रहें। बच्चे, बुज़ुर्ग और दमा के मरीज़ बाहर न जाएँ। PM2.5 {aqi} µg/m³ है।"),
    },
    "kn": {
        "good": ("ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಉತ್ತಮವಾಗಿದೆ", "ಹೊರಗಿನ ಚಟುವಟಿಕೆಗಳನ್ನು ಆನಂದಿಸಿ. AQI {aqi} µg/m³ — ಸುರಕ್ಷಿತ ಮಿತಿಯಲ್ಲಿದೆ."),
        "moderate": ("ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಮಧ್ಯಮ", "ಸಂವೇದನಾಶೀಲ ಗುಂಪುಗಳು ಹೊರಗೆ ಹೆಚ್ಚು ಸಮಯ ಕಳೆಯಬೇಡಿ. PM2.5 {aqi} µg/m³."),
        "poor": ("ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಕಳಪೆ", "ಹೊರಗೆ ಹೋಗುವುದನ್ನು ಕಡಿಮೆ ಮಾಡಿ. N95 ಮಾಸ್ಕ್ ಬಳಸಿ. PM2.5 {aqi} µg/m³."),
        "severe": ("ತೀವ್ರ ವಾಯು ಮಾಲಿನ್ಯ ಎಚ್ಚರಿಕೆ", "ಒಳಗೇ ಇರಿ. ಮಕ್ಕಳು, ವೃದ್ಧರು ಮತ್ತು ಆಸ್ತಮಾ ರೋಗಿಗಳು ಹೊರಗೆ ಹೋಗಬೇಡಿ. PM2.5 {aqi} µg/m³."),
    },
}


def _severity(aqi: float) -> str:
    if aqi <= 60:
        return "good"
    if aqi <= 90:
        return "moderate"
    if aqi <= 120:
        return "poor"
    return "severe"


def _aqi_for_city(city: str, pollutant: str, lat: float | None, lon: float | None) -> float:
    """If lat/lon given, return p50 of the nearest cell; else city-wide mean."""
    if lat is not None and lon is not None:
        sql = text(
            """
            SELECT f.p50
            FROM forecast f JOIN grid_cell g ON g.city_id=f.city_id AND g.cell_id=f.cell_id
            WHERE f.city_id=:city AND f.pollutant=:pollutant
              AND f.ts_target = (SELECT MAX(ts_target) FROM forecast
                                 WHERE city_id=:city AND pollutant=:pollutant)
            ORDER BY g.centroid <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326)
            LIMIT 1
            """
        )
        params = {"city": city, "pollutant": pollutant, "lon": lon, "lat": lat}
    else:
        sql = text(
            """
            SELECT AVG(p50) AS p50 FROM forecast
            WHERE city_id=:city AND pollutant=:pollutant
              AND ts_target = (SELECT MAX(ts_target) FROM forecast
                               WHERE city_id=:city AND pollutant=:pollutant)
            """
        )
        params = {"city": city, "pollutant": pollutant}
    with get_engine().begin() as conn:
        row = conn.execute(sql, params).fetchone()
    return float(row.p50 or 0.0)


@router.get("", response_model=AdvisoryResponse)
def advisory(
    city: str,
    lang: str = "en",
    pollutant: str = "pm25",
    lat: float | None = None,
    lon: float | None = None,
):
    aqi = _aqi_for_city(city, pollutant, lat, lon)
    sev = _severity(aqi)
    lang = lang if lang in TEMPLATES else "en"
    headline, body = TEMPLATES[lang][sev]
    return AdvisoryResponse(
        city=city,
        lang=lang,
        severity=sev,
        headline=headline,
        advice=body.format(aqi=int(round(aqi))),
        aqi_p50=aqi,
        pollutant=pollutant,
        issued_at=datetime.now(timezone.utc),
    )
