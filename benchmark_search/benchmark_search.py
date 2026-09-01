"""
Benchmark comparatif : geo_api (PostgreSQL) vs Elasticsearch
sur différents jeux de données / layers.

Moteurs interrogés (reproduction du code existant) :
  - geo_api : GET  /api/geo-api/{layer}/feature/?search={terme}&limit=10
  - ES      : POST /{layer}/_search  avec query_string "*{terme}*", size=10, track_total_hits

Sorties : JSON (résumé + détail) et CSV (1 ligne par itération + agrégats).

Exécution (dans le conteneur web, où ES et le web sont joignables) :
    docker compose exec web /opt/venv/bin/python \
        /opt/terra-visu/benchmark_search/benchmark_search.py

Options :
    --layer <name>   Ne benchmarker que certains layers (répétable).
                     Utile pour lancer paca_gis séparément vu sa lenteur.
    --iterations <n> Nombre d'itérations par (layer, moteur, terme).
                     Pour une passe de validation rapide, mettre 1 ou 2.

Exemples :
    # tout (long)
    /opt/venv/bin/python benchmark_search.py

    # seulement communes-simplifiees et communes-x4 (test rapide)
    /opt/venv/bin/python benchmark_search.py \
        --layer communes-simplifiees --layer communes-x4 --iterations 2

    # paca_gis seul (très long pour geo_api)
    /opt/venv/bin/python benchmark_search.py --layer paca_gis
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GEO_API_BASE = "http://web:8000/api/geo-api"
ES_BASE = "http://elasticsearch:9200"

ITERATIONS = 30
RESULT_SIZE = 10
FIRST_N = 5
REQ_TIMEOUT = 1800  # secondes, puisqu'une recherche geo_api peut excéder 300 s

# Pour chaque layer : termes de recherche (préfixes partiels), et le champ
# d'exemple affiché si le moteur le fournit (omis sinon).
LAYERS = {
    "communes-simplifiees": {
        "terms": ["toulou", "bézi", "ajac"],
        "display_field": "nom",
    },
    "communes-x4": {
        "terms": ["toulou", "bézi", "ajac"],
        "display_field": "nom",
    },
    "paca_gis": {
        "terms": ["dig", "fré", "anti"],
        "display_field": "libelleCommuneEtablissement",
    },
}

ENGINE_LABELS = {
    "geo_api": "geo_api",
    "elasticsearch": "elasticsearch",
}


def summary(values):
    p95 = (
        round(statistics.quantiles(values, n=100)[94], 2)
        if len(values) >= 2
        else round(values[0], 2)
    )
    return {
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": p95,
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "count": len(values),
    }


def bench_geoapi(layer, term):
    """Une recherche geo_api. Retourne (durée_ms, description résultats)."""
    url = f"{GEO_API_BASE}/{layer}/feature/"
    params = {"search": term, "limit": RESULT_SIZE}
    started = time.perf_counter()
    resp = requests.get(url, params=params, timeout=REQ_TIMEOUT)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    resp.raise_for_status()
    payload = resp.json()

    results = payload.get("results", [])
    description = {
        "total_results": payload.get("count"),
        "first_n": [],
    }
    for item in results[:FIRST_N]:
        entry = {"identifier": item.get("identifier")}
        props = item.get("properties", {})
        display_field = props.get(LAYERS[layer]["display_field"])
        if display_field is not None:
            entry[LAYERS[layer]["display_field"]] = display_field
        description["first_n"].append(entry)

    return elapsed_ms, description


def bench_es(layer, term):
    """Une recherche Elasticsearch (même requête que le front). Retourne (durée_ms, description)."""
    url = f"{ES_BASE}/{layer}/_search"
    body = {
        "size": RESULT_SIZE,
        "track_total_hits": True,
        "query": {"query_string": {"query": f"*{term}*"}},
    }
    started = time.perf_counter()
    resp = requests.post(url, json=body, timeout=REQ_TIMEOUT)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    resp.raise_for_status()
    payload = resp.json()

    hits = payload.get("hits", {}).get("hits", [])
    total = payload.get("hits", {}).get("total", {})
    description = {
        "total_results": total.get("value") if isinstance(total, dict) else total,
        "first_n": [],
    }
    for hit in hits[:FIRST_N]:
        src = hit.get("_source", {})
        entry = {"_feature_id": src.get("_feature_id")}
        if entry["_feature_id"] is None:
            entry = {"id": hit.get("_id")}
        display_field = src.get(LAYERS[layer]["display_field"])
        if display_field is not None:
            entry[LAYERS[layer]["display_field"]] = display_field
        description["first_n"].append(entry)

    return elapsed_ms, description


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Benchmark geo_api vs Elasticsearch")
    parser.add_argument(
        "--layer", action="append", default=[],
        help="Layer(s) à benchmarker (répétable). Défaut : tous.",
    )
    parser.add_argument(
        "--iterations", type=int, default=ITERATIONS,
        help=f"Nombre d'itérations par (layer, moteur, terme). Défaut : {ITERATIONS}.",
    )
    return parser.parse_args(argv)


def run(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    iterations = args.iterations
    selected = list(args.layer) or list(LAYERS.keys())
    unknown = [l for l in args.layer if l not in LAYERS]
    if unknown:
        print(f"WARNING: layers inconnus ignorés : {unknown} "
              f"(valides : {list(LAYERS.keys())})")
        selected = [l for l in selected if l in LAYERS]

    started_at = datetime.now(timezone.utc).isoformat()
    results = []
    csv_rows = []

    output_dir = Path(__file__).resolve().parent
    output_dir.mkdir(exist_ok=True)
    json_path = output_dir / "benchmark_report.json"
    csv_path = output_dir / "benchmark_results.csv"

    import csv as _csv

    def save_incremental():
        """Écrit le CSV et le JSON avec l'état courant (sauvegarde robuste)."""
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["layer", "term", "engine", "metric", "value_agregat", "duration_ms"])
            w.writerows(csv_rows)
        report = {
            "benchmark": {
                "generated_at": started_at,
                "iterations": iterations,
                "result_size": RESULT_SIZE,
                "layers": selected,
            },
            "results": results,
        }
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    for layer in selected:
        conf = LAYERS[layer]
        display_field = conf["display_field"]
        for term in conf["terms"]:
            for engine, func in (("geo_api", bench_geoapi), ("elasticsearch", bench_es)):
                durations = []
                errors = 0
                final_description = None
                for it in range(1, iterations + 1):
                    ok = False
                    try:
                        elapsed_ms, description = func(layer, term)
                        ok = True
                    except requests.exceptions.RequestException as exc:
                        errors += 1
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"{layer:<20} | {term:<8} | {engine:<14} | "
                            f"it {it:>2}/{iterations} | ERREUR : {type(exc).__name__} "
                            f"(sautée, l'itération est notée error)",
                            flush=True,
                        )
                        csv_rows.append((layer, term, engine, f"it_{it:02d}_error", "", ""))

                    if ok:
                        durations.append(elapsed_ms)
                        final_description = description
                        ok_val = round(elapsed_ms, 2)
                        # Suivi temps réel (Option A)
                        done = it
                        remaining = iterations - it
                        avg_ms = statistics.mean(durations)
                        avg_s = avg_ms / 1000.0
                        eta_s = avg_s * remaining
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"{layer:<20} | {term:<8} | {engine:<14} | "
                            f"it {done:>2}/{iterations} | {elapsed_ms:>10.0f} ms | "
                            f"moy {avg_s:>7.1f} s | ETA {eta_s/60:>6.1f} min",
                            flush=True,
                        )
                    else:
                        ok_val = "error"

                    csv_rows.append((layer, term, engine, f"it_{it:02d}", "", ok_val))
                    save_incremental()

                # Sauvegarde après le bloc (stats agrégées)
                if durations:
                    stats = summary(durations)
                    stats["errors"] = errors
                    results.append({
                        "layer": layer,
                        "term": term,
                        "engine": engine,
                        "duration_ms": stats,
                        "total_results": final_description["total_results"],
                        "first_5": final_description["first_n"],
                    })

                    for metric, value in stats.items():
                        csv_rows.append((layer, term, engine, metric, value, ""))
                    if final_description["total_results"] is not None:
                        csv_rows.append((
                            layer, term, engine, "total_results",
                            final_description["total_results"], "",
                        ))
                    first_ids = ";".join(
                        str(r.get("identifier") or r.get("_feature_id") or r.get("id"))
                        for r in final_description["first_n"]
                    )
                    csv_rows.append((layer, term, engine, "first_5_id", first_ids, ""))
                    csv_rows.append(("", "", "", "", "", ""))
                    save_incremental()

    report = save_incremental()

    print(f"Benchmark terminé : {len(results)} mesures")
    print(f"  JSON : {json_path}")
    print(f"  CSV  : {csv_path}")
    print()
    print("Résumé (mean ms, engine geo_api vs elasticsearch) :")
    print(f"{'layer':<22}{'term':<10}{'geo_api':>12}{'elasticsearch':>16}{'ratio ES/geo':>14}")
    for layer in selected:
        conf = LAYERS[layer]
        for term in conf["terms"]:
            def _find(engine):
                for r in results:
                    if r["layer"] == layer and r["term"] == term and r["engine"] == engine:
                        return r
                return None
            g = _find("geo_api")
            e = _find("elasticsearch")
            if g is None or e is None or not g["duration_ms"] or not e["duration_ms"]:
                gm = g["duration_ms"]["mean"] if g and g["duration_ms"] else "n/a"
                em = e["duration_ms"]["mean"] if e and e["duration_ms"] else "n/a"
                print(f"{layer:<22}{term:<10}{str(gm):>12}{str(em):>16}{'n/a':>14}")
                continue
            ratio = e["duration_ms"]["mean"] / g["duration_ms"]["mean"]
            print(f"{layer:<22}{term:<10}{g['duration_ms']['mean']:>10.2f}ms{e['duration_ms']['mean']:>12.2f}ms{ratio:>12.2f}x")

    return report


if __name__ == "__main__":
    run()
