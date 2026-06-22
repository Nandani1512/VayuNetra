"""Minimal RAG for citizen advisories.

Production path (Phase 12) uses pgvector with BGE-small-en embeddings over WHO
Air Quality Guidelines (2021 update), CPCB AQI categories, and IIT-Kanpur
health advisory leaflets. For the hackathon demo we ship a small in-process
corpus and rank by TF-IDF cosine similarity, which keeps the API path free of
heavy ML deps and still produces useful citations.

The interface is intentionally tiny: ``retrieve(query, k=5) -> list[Chunk]``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Chunk:
    """A retrievable knowledge-base chunk with a stable citation string."""

    id: str
    source: str
    title: str
    text: str

    @property
    def citation(self) -> str:
        return f"[{self.source} — {self.title}]"


# Curated corpus. Each entry is short enough to fit in a Telegram message and
# tagged with provenance so we can render a citation block. Real deployment
# replaces this with chunked PDFs in pgvector.
_CORPUS: tuple[Chunk, ...] = (
    Chunk(
        id="who-pm25-annual",
        source="WHO AQG 2021",
        title="PM2.5 annual guideline",
        text=(
            "WHO recommends an annual mean PM2.5 below 5 µg/m³ and a 24-hour "
            "mean below 15 µg/m³. Above 35 µg/m³, even short-term exposure "
            "raises cardiopulmonary mortality."
        ),
    ),
    Chunk(
        id="who-pm10-annual",
        source="WHO AQG 2021",
        title="PM10 annual guideline",
        text=(
            "WHO sets the PM10 annual mean target at 15 µg/m³ and the 24-hour "
            "limit at 45 µg/m³. Coarse particles aggravate asthma and chronic "
            "obstructive pulmonary disease."
        ),
    ),
    Chunk(
        id="cpcb-aqi-bands",
        source="CPCB NAAQS",
        title="National AQI categories",
        text=(
            "CPCB groups AQI into Good (0–50), Satisfactory (51–100), "
            "Moderate (101–200), Poor (201–300), Very Poor (301–400) and "
            "Severe (401–500). PM2.5 dominates the index in Indian cities."
        ),
    ),
    Chunk(
        id="cpcb-sensitive",
        source="CPCB NAAQS",
        title="Sensitive groups",
        text=(
            "Children, the elderly, pregnant women, and people with asthma, "
            "COPD or cardiac conditions are flagged as sensitive groups. "
            "They should reduce exposure once AQI crosses 100."
        ),
    ),
    Chunk(
        id="iitk-masks",
        source="IIT-Kanpur Leaflet",
        title="Mask selection",
        text=(
            "Surgical masks do not block PM2.5. Use N95/FFP2 respirators with "
            "a tight fit. Replace after 8–10 hours of continuous wear or when "
            "breathing resistance rises noticeably."
        ),
    ),
    Chunk(
        id="iitk-indoor",
        source="IIT-Kanpur Leaflet",
        title="Indoor protection",
        text=(
            "On Severe-AQI days keep windows shut, run HEPA purifiers in "
            "occupied rooms, avoid frying and incense, and damp-mop floors "
            "instead of dry sweeping."
        ),
    ),
    Chunk(
        id="iitk-exercise",
        source="IIT-Kanpur Leaflet",
        title="Outdoor exercise",
        text=(
            "When AQI exceeds 200, postpone running, cycling and team sports. "
            "Brisk walking indoors for 30 minutes preserves cardiovascular "
            "benefit without the exposure dose."
        ),
    ),
    Chunk(
        id="who-ozone",
        source="WHO AQG 2021",
        title="Ozone short-term",
        text=(
            "Peak 8-hour ozone should not exceed 100 µg/m³. Levels above "
            "160 µg/m³ trigger eye irritation and reduce lung function in "
            "children within four hours."
        ),
    ),
    Chunk(
        id="cpcb-vehicular",
        source="CPCB Source Apportionment",
        title="Vehicular fraction",
        text=(
            "In Indian metros vehicular sources contribute 30–55 % of urban "
            "PM2.5 during winter inversions. Carpooling and engine off at "
            "idle reduce neighbourhood-scale exposure."
        ),
    ),
    Chunk(
        id="who-children",
        source="WHO AQG 2021",
        title="Children's exposure",
        text=(
            "Children breathe more air per kilogram of body weight than "
            "adults, so the same ambient PM2.5 concentration delivers a "
            "higher dose. Schools should suspend outdoor PE above AQI 200."
        ),
    ),
    Chunk(
        id="iitk-asthma",
        source="IIT-Kanpur Leaflet",
        title="Asthma rescue",
        text=(
            "Asthmatics should carry a short-acting beta-agonist inhaler on "
            "Poor-or-worse days and rinse the nasal cavity with saline after "
            "outdoor exposure to clear deposited particles."
        ),
    ),
    Chunk(
        id="cpcb-monitoring",
        source="CPCB CAAQMS",
        title="Network coverage",
        text=(
            "CPCB operates ~900 continuous ambient monitoring stations "
            "(CAAQMS) across India. Real-time data feed both the national "
            "AQI bulletin and downscaled grids used in VayuNetra."
        ),
    ),
)


_TOKEN_RE = re.compile(r"[a-zA-Z]{2,}")


def _tokenise(text: str) -> list[str]:
    return [w.lower() for w in _TOKEN_RE.findall(text)]


@lru_cache(maxsize=1)
def _index() -> tuple[list[Counter[str]], dict[str, float], list[Chunk]]:
    """Build a tiny TF/IDF index over the static corpus.

    Cached for the process lifetime — the corpus is immutable.
    """
    chunks = list(_CORPUS)
    docs = [Counter(_tokenise(c.text + " " + c.title)) for c in chunks]
    n = len(docs)
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d.keys())
    idf = {w: math.log((1 + n) / (1 + df_w)) + 1.0 for w, df_w in df.items()}
    return docs, idf, chunks


def _score(query_tokens: list[str], doc: Counter[str], idf: dict[str, float]) -> float:
    if not query_tokens or not doc:
        return 0.0
    q = Counter(query_tokens)
    num = sum(q[w] * doc.get(w, 0) * idf.get(w, 0.0) ** 2 for w in q)
    qn = math.sqrt(sum((q[w] * idf.get(w, 0.0)) ** 2 for w in q))
    dn = math.sqrt(sum((doc[w] * idf.get(w, 0.0)) ** 2 for w in doc))
    if qn == 0 or dn == 0:
        return 0.0
    return num / (qn * dn)


def retrieve(query: str, k: int = 5) -> list[Chunk]:
    """Return the top-``k`` chunks for ``query`` ranked by TF-IDF cosine."""
    docs, idf, chunks = _index()
    qtok = _tokenise(query)
    scored = sorted(
        ((_score(qtok, d, idf), c) for d, c in zip(docs, chunks, strict=True)),
        key=lambda t: t[0],
        reverse=True,
    )
    out = [c for s, c in scored if s > 0][:k]
    # If the query has no overlap fall back to the most generic CPCB band card
    # so the caller always gets something cite-able.
    if not out:
        out = [next(c for c in chunks if c.id == "cpcb-aqi-bands")]
    return out


def corpus() -> tuple[Chunk, ...]:
    """Expose the immutable corpus — used by tests and the translate script."""
    return _CORPUS
