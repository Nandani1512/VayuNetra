// VayuNetra frontend — MapLibre + vanilla JS. Single page, no build step.

const API = ""; // same-origin
const CITY_VIEW = {
  delhi:     { center: [77.10, 28.64], zoom: 10 },
  bengaluru: { center: [77.59, 12.97], zoom: 10.5 },
};

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      "carto-dark": {
        type: "raster",
        tiles: [
          "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
          "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
          "https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors © CARTO",
      },
    },
    layers: [{ id: "basemap", type: "raster", source: "carto-dark" }],
  },
  center: CITY_VIEW.delhi.center,
  zoom: CITY_VIEW.delhi.zoom,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }));

const $ = (id) => document.getElementById(id);
const loadingEl = $("loading");
function setLoading(label) {
  if (label) { loadingEl.textContent = label; loadingEl.hidden = false; }
  else { loadingEl.hidden = true; }
}

// AQI colour ramp (PM2.5 µg/m³, CPCB bands).
function aqiColor(v) {
  if (v == null) return "#888";
  if (v <= 30)  return "#a8e05f";
  if (v <= 60)  return "#fdd64b";
  if (v <= 90)  return "#ff9b57";
  if (v <= 120) return "#fe6a69";
  if (v <= 250) return "#a97abc";
  return "#a87383";
}

let lastEnforce = null;

map.on("load", async () => {
  map.addSource("forecast", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "forecast-fill", type: "fill", source: "forecast",
    paint: {
      "fill-color": [
        "step", ["get", "p50"],
        "#a8e05f", 30,
        "#fdd64b", 60,
        "#ff9b57", 90,
        "#fe6a69", 120,
        "#a97abc", 250,
        "#a87383"
      ],
      "fill-opacity": 0.55,
    },
  });
  map.addLayer({
    id: "forecast-outline", type: "line", source: "forecast",
    paint: { "line-color": "#0d1626", "line-width": 0.4, "line-opacity": 0.35 },
  });

  map.addSource("hotspots", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "hotspot-outline", type: "line", source: "hotspots",
    paint: { "line-color": "#ff3b5c", "line-width": 2.5, "line-opacity": 0.95 },
  });

  map.addSource("trajectory", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "trajectory-region", type: "fill", source: "trajectory",
    filter: ["==", ["get", "role"], "source_region"],
    paint: { "fill-color": "#b06bff", "fill-opacity": 0.15 },
  });
  map.addLayer({
    id: "trajectory-line", type: "line", source: "trajectory",
    filter: ["==", ["get", "role"], "trajectory"],
    paint: { "line-color": "#b06bff", "line-width": 2, "line-dasharray": [2, 2] },
  });

  map.on("click", "forecast-fill", (e) => {
    if (!e.features?.length) return;
    onCellClick(e.features[0]);
  });
  map.on("mouseenter", "forecast-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "forecast-fill", () => (map.getCanvas().style.cursor = ""));

  await refreshForecast();
  refreshAdvisory();
});

function paramsTriple() {
  return {
    city: $("city").value,
    pollutant: $("pollutant").value,
    horizon: $("horizon").value,
  };
}

async function refreshForecast() {
  const { city, pollutant, horizon } = paramsTriple();
  setLoading(`Loading ${city} ${pollutant} +${horizon}h…`);
  const center = CITY_VIEW[city];
  if (center) map.flyTo({ center: center.center, zoom: center.zoom, duration: 600 });

  try {
    const r = await fetch(`${API}/forecast?city=${city}&pollutant=${pollutant}&horizon=${horizon}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const gj = await r.json();
    map.getSource("forecast").setData(gj);
    map.getSource("hotspots").setData({ type: "FeatureCollection", features: [] });
    map.getSource("trajectory").setData({ type: "FeatureCollection", features: [] });
    setLoading(null);
  } catch (e) {
    setLoading(null);
    alert(`Forecast load failed: ${e.message}`);
  }
}

async function refreshAdvisory() {
  const { city, pollutant } = paramsTriple();
  const lang = $("lang").value;
  try {
    const r = await fetch(`${API}/advisory?city=${city}&pollutant=${pollutant}&lang=${lang}`);
    const a = await r.json();
    const el = $("advisoryBox");
    el.className = `advisory severity-${a.severity}`;
    el.innerHTML = `<h4>${escapeHtml(a.headline)}</h4><p>${escapeHtml(a.advice)}</p>` +
                   `<p class="muted small">AQI p50: ${Math.round(a.aqi_p50)} · Tier: ${a.vuln_tier} · ${a.lang.toUpperCase()} · ${new Date(a.issued_at).toLocaleTimeString()}</p>` +
                   (a.citation_text ? `<p class="muted small">📖 ${escapeHtml(a.citation_text.slice(0, 120))}…</p>` : "");
  } catch (e) {
    /* ignore on errors */
  }
}

["city","pollutant","horizon"].forEach((id) => $(id).addEventListener("change", () => { refreshForecast(); refreshAdvisory(); }));
$("lang").addEventListener("change", refreshAdvisory);

async function onCellClick(feat) {
  const { city, pollutant } = paramsTriple();
  const props = feat.properties;
  const cell_id = props.cell_id;

  $("panel-default").hidden = true;
  $("panel-enforce").hidden = true;
  $("panel-cell").hidden = false;
  $("cell-title").textContent = `Cell ${cell_id}`;
  $("cell-stats").innerHTML = `
    <div class="stat"><div class="k">p10 (µg/m³)</div><div class="v">${(+props.p10).toFixed(0)}</div></div>
    <div class="stat"><div class="k">p50</div><div class="v" style="color:${aqiColor(+props.p50)}">${(+props.p50).toFixed(0)}</div></div>
    <div class="stat"><div class="k">p90</div><div class="v">${(+props.p90).toFixed(0)}</div></div>
  `;
  $("cell-attribution").innerHTML = `<p class="muted small">Loading source attribution…</p>`;
  $("cell-evidence").innerHTML = "";

  try {
    setLoading("Computing SHAP + back-trajectory…");
    const r = await fetch(`${API}/attribution?city=${city}&cell_id=${cell_id}&pollutant=${pollutant}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const a = await r.json();
    renderAttribution(a);
    map.getSource("trajectory").setData(a.trajectory_geojson);
    setLoading(null);
  } catch (e) {
    setLoading(null);
    $("cell-attribution").innerHTML = `<p class="muted small">Attribution unavailable: ${escapeHtml(e.message)}.<br>This requires the LUR model and recent weather data.</p>`;
  }
}

function renderAttribution(a) {
  const rows = Object.entries(a.blended_sources)
    .sort((x, y) => y[1] - x[1])
    .map(([k, v]) => `
      <div>
        <div style="display:flex;justify-content:space-between;font-size:12px">
          <span>${k.replace(/_/g, " ")}</span><span>${(v * 100).toFixed(1)}%</span>
        </div>
        <div class="bar"><div style="width:${(v * 100).toFixed(1)}%"></div></div>
      </div>
    `).join("");
  $("cell-attribution").innerHTML = `
    <h3>Source attribution</h3>
    ${rows || "<p class='muted small'>No positive sources resolved.</p>"}
  `;
  const ev = a.overlay_evidence || {};
  $("cell-evidence").innerHTML = `
    <h3>Evidence</h3>
    <div class="stats">
      <div class="stat"><div class="k">Fires in source region</div><div class="v">${ev.fires_in_source_region ?? "—"}</div></div>
      <div class="stat"><div class="k">Industry POIs</div><div class="v">${ev.industry_in_source_region ?? "—"}</div></div>
      <div class="stat"><div class="k">Road density</div><div class="v">${(+ev.road_density_mean || 0).toFixed(2)}</div></div>
    </div>
    <p class="muted small">Wind ${a.wind_speed_ms?.toFixed(1)} m/s · bearing from ${a.wind_bearing_from_deg?.toFixed(0) ?? "—"}° · confidence ${(a.confidence * 100).toFixed(0)}%</p>
  `;
}

function closeCell() {
  $("panel-cell").hidden = true;
  $("panel-default").hidden = false;
  map.getSource("trajectory").setData({ type: "FeatureCollection", features: [] });
}

$("loadHotspots").addEventListener("click", async () => {
  const { city, pollutant, horizon } = paramsTriple();
  setLoading("Running Gi* hotspot detection…");
  try {
    const r = await fetch(`${API}/enforce?city=${city}&pollutant=${pollutant}&horizon=${horizon}&with_attribution=false&with_brief=false`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const out = await r.json();
    lastEnforce = out;
    const allCells = new Set();
    for (const item of out.items) item.cluster.cells.forEach((c) => allCells.add(c));

    const src = map.getSource("forecast")._data;
    const features = src.features.filter((f) => allCells.has(f.properties.cell_id));
    map.getSource("hotspots").setData({ type: "FeatureCollection", features });
    setLoading(null);
  } catch (e) {
    setLoading(null);
    alert(`Hotspot detection failed: ${e.message}`);
  }
});

$("loadEnforce").addEventListener("click", async () => {
  const { city, pollutant, horizon } = paramsTriple();
  setLoading("Running full enforcement (hotspots + SHAP + Groq LLM)…");
  try {
    const r = await fetch(`${API}/enforce?city=${city}&pollutant=${pollutant}&horizon=${horizon}&top_k=3&with_attribution=true&with_brief=true`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const out = await r.json();
    lastEnforce = out;
    setLoading(null);
    showEnforce(out);
  } catch (e) {
    setLoading(null);
    alert(`Enforcement failed: ${e.message}`);
  }
});

function showEnforce(out) {
  $("panel-default").hidden = true;
  $("panel-cell").hidden = true;
  $("panel-enforce").hidden = false;
  $("enforce-meta").textContent = `${out.city} · ${out.pollutant} · +${out.horizon_h}h · ${out.hot_cells} hot cells in ${out.n_clusters} cluster(s)`;
  const blocks = out.items.map((item, i) => {
    const c = item.cluster;
    const briefHtml = item.brief
      ? `<div class="brief-md">${markdown(item.brief)}</div>`
      : `<p class="muted small">${escapeHtml(item.brief_error || "no brief")}</p>`;
    return `
      <h3>Cluster #${i + 1} · rank ${c.rank}</h3>
      <div class="stats">
        <div class="stat"><div class="k">mean p50</div><div class="v" style="color:${aqiColor(c.mean_p50)}">${c.mean_p50.toFixed(0)}</div></div>
        <div class="stat"><div class="k">cells</div><div class="v">${c.n_cells}</div></div>
        <div class="stat"><div class="k">exposed</div><div class="v">${Math.round(c.pop_exposed).toLocaleString()}</div></div>
      </div>
      <p class="muted small">centroid (${c.centroid_lat.toFixed(4)}, ${c.centroid_lon.toFixed(4)}) · LLM: ${escapeHtml(item.llm || "?")}</p>
      ${briefHtml}
    `;
  }).join("");
  $("enforce-content").innerHTML = blocks || "<p>No clusters above threshold.</p>";

  // Outline hotspot cells on map too.
  const allCells = new Set();
  for (const item of out.items) item.cluster.cells.forEach((c) => allCells.add(c));
  const src = map.getSource("forecast")._data;
  const features = src.features.filter((f) => allCells.has(f.properties.cell_id));
  map.getSource("hotspots").setData({ type: "FeatureCollection", features });
  if (features.length) {
    const bounds = new maplibregl.LngLatBounds();
    features.forEach((f) => f.geometry.coordinates[0].forEach((c) => bounds.extend(c)));
    map.fitBounds(bounds, { padding: 60, duration: 800 });
  }
}

function closeEnforce() {
  $("panel-enforce").hidden = true;
  $("panel-default").hidden = false;
  map.getSource("hotspots").setData({ type: "FeatureCollection", features: [] });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Minimal markdown renderer for LLM briefs (#, ##, ###, lists, **bold**).
function markdown(src) {
  const escaped = escapeHtml(src);
  return escaped
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*?<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`)
    .replace(/\n{2,}/g, "<br><br>")
    .replace(/\n/g, " ");
}

// --- Compare Mode ---
let compareActive = false;
let mapLeft = null, mapRight = null;

const COMPARE_STYLE = {
  version: 8,
  sources: { "carto-dark": { type: "raster", tiles: [
    "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
  ], tileSize: 256 } },
  layers: [{ id: "basemap", type: "raster", source: "carto-dark" }],
};

const FORECAST_LAYER = {
  id: "forecast-fill", type: "fill", source: "forecast",
  paint: {
    "fill-color": ["step", ["get", "p50"], "#a8e05f", 30, "#fdd64b", 60, "#ff9b57", 90, "#fe6a69", 120, "#a97abc", 250, "#a87383"],
    "fill-opacity": 0.55,
  },
};

function initCompareMap(container, city) {
  const cfg = CITY_VIEW[city];
  const m = new maplibregl.Map({ container, style: COMPARE_STYLE, center: cfg.center, zoom: cfg.zoom });
  m.on("load", async () => {
    m.addSource("forecast", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer(FORECAST_LAYER);
    loadCompareData(m, city);
  });
  return m;
}

async function loadCompareData(m, city) {
  const { pollutant, horizon } = paramsTriple();
  try {
    const r = await fetch(`${API}/forecast?city=${city}&pollutant=${pollutant}&horizon=${horizon}`);
    if (r.ok) { const gj = await r.json(); m.getSource("forecast").setData(gj); }
  } catch (e) { /* silent */ }
}

function enterCompare() {
  compareActive = true;
  document.body.classList.add("compare-active");
  $("compare-view").hidden = false;
  if (!mapLeft) mapLeft = initCompareMap("map-left", "delhi");
  else { mapLeft.resize(); loadCompareData(mapLeft, "delhi"); }
  if (!mapRight) mapRight = initCompareMap("map-right", "bengaluru");
  else { mapRight.resize(); loadCompareData(mapRight, "bengaluru"); }
}

function exitCompare() {
  compareActive = false;
  document.body.classList.remove("compare-active");
  $("compare-view").hidden = true;
}

$("btnCompare").addEventListener("click", enterCompare);
$("btnExitCompare").addEventListener("click", exitCompare);

// Refresh compare maps when pollutant/horizon change
["pollutant", "horizon"].forEach((id) => $(id).addEventListener("change", () => {
  if (!compareActive) return;
  if (mapLeft && mapLeft.getSource("forecast")) loadCompareData(mapLeft, "delhi");
  if (mapRight && mapRight.getSource("forecast")) loadCompareData(mapRight, "bengaluru");
}));
