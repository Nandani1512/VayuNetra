"""G11: Signal-to-intervention response-time benchmark."""
import json, time, pathlib, numpy as np, httpx

BASE = "http://127.0.0.1:8000"
STEPS = {
    "forecast": "/forecast?city=delhi&pollutant=pm25&horizon=24",
    "enforce_brief": "/enforce?city=delhi&pollutant=pm25&horizon=24&top_k=1&with_attribution=false&with_brief=true",
    "advisory": "/advisory?city=delhi&pollutant=pm25&lang=en",
}
ITERATIONS = 5


def _pct(arr, q):
    return float(np.percentile(arr, q))


def run_benchmark():
    timings = {k: [] for k in STEPS}
    totals = []
    client = httpx.Client(base_url=BASE, timeout=60)

    for _ in range(ITERATIONS):
        t_total = 0.0
        for name, path in STEPS.items():
            t0 = time.perf_counter()
            client.get(path)
            elapsed = time.perf_counter() - t0
            timings[name].append(elapsed)
            t_total += elapsed
        totals.append(t_total)

    client.close()

    results = {}
    for name, vals in {**timings, "total": totals}.items():
        results[name] = {"p50": _pct(vals, 50), "p95": _pct(vals, 95), "max": max(vals)}

    # Write JSON
    out = pathlib.Path(__file__).resolve().parent.parent / "reports" / "benchmark_response_time.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))

    # Print table
    print(f"{'Step':<15} {'p50':>8} {'p95':>8} {'max':>8}")
    print("-" * 41)
    for name, m in results.items():
        print(f"{name:<15} {m['p50']:>7.3f}s {m['p95']:>7.3f}s {m['max']:>7.3f}s")

    return results


if __name__ == "__main__":
    run_benchmark()
