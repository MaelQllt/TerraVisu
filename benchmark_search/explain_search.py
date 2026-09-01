"""
Diagnostic du plan SQL de la recherche geo_api.

Construit le queryset EXACT que géo-api exécute (mêmes annotations de
SearchAllFieldsBackend + boost de préfixe + ORDER BY), SANS l'exécuter,
puis produit un EXPLAIN pour comprendre où part le temps
(filtre / ORDER BY / scan / tri disque).

EXPLAIN sans ANALYZE ne coûte presque rien (pas d'exécution).
Pour les temps réels, utiliser ANALYZE :
    EXPLAIN_ANALYZE=1 ... explain_search.py paca_gis dig

Usage (dans le conteneur web) :
    DJANGO_SETTINGS_MODULE=project.settings.dev /opt/venv/bin/python \
        benchmark_search/explain_search.py [layer] [terme]

Exemple :
    docker compose exec web sh -c "cd /opt/terra-visu && \
        DJANGO_SETTINGS_MODULE=project.settings.dev \
        /opt/venv/bin/python benchmark_search/explain_search.py paca_gis dig"
"""

import os
import sys
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
WORKSPACE = str(Path(SCRIPT_DIR).parent)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, WORKSPACE)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings.dev")
os.environ.setdefault("POSTGRES_PASSWORD", os.environ.get("POSTGRES_PASSWORD", ""))

import django  # noqa: E402

django.setup()

import json  # noqa: E402
from django.db import connection  # noqa: E402
from rest_framework.request import Request  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

import project.geo_api.views.feature_viewset as fv  # noqa: E402


ANALYZE = os.environ.get("EXPLAIN_ANALYZE") == "1"


class CaptureViewSet(fv.FeatureViewSet):
    """Même viewset, aucun override nécessaire : on pose self.kwargs ensuite."""

    pass


def main():
    if len(sys.argv) < 3:
        layer, term = "paca_gis", "dig"
        print(f"Usage: explain_search.py [layer] [terme]  (défaut: {layer} {term})")
    else:
        layer, term = sys.argv[1], sys.argv[2]

    factory = APIRequestFactory()
    http_request = factory.get(
        f"/api/geo-api/{layer}/feature/", {"search": term, "limit": "10"}
    )
    drf_request = Request(http_request)

    view = CaptureViewSet()
    view.kwargs = {"layer": layer}
    view.args = ()
    view.request = drf_request
    view.format_kwarg = None

    # Réplique de FeatureViewSet.list() (les étapes ne touchent pas à la DB)
    search_param = str(drf_request.query_params.get("search", "")).strip()
    queryset = view.filter_queryset(view.get_queryset())
    if search_param:
        queryset, search_boost_parts = view._add_search_boost_annotations(
            queryset, search_param
        )
    else:
        search_boost_parts = []
    extra = search_boost_parts + view._collect_auto_order_parts()
    queryset = view._apply_prefix_boost(queryset, extra_order_parts=extra)
    queryset.query.set_limits(0, 10)

    sql = str(queryset.query)

    print(f"layer={layer} term={term}  ANALYZE={ANALYZE}")
    print(f"SQL ({len(sql)} caractères) :")
    print(sql)
    print()
    print("=" * 100)
    print("PLAN D'EXECUTION :")
    print("=" * 100)

    explain_opts = "ANALYZE, " if ANALYZE else ""
    with connection.cursor() as cur:
        try:
            cur.execute(
                f"EXPLAIN ({explain_opts}BUFFERS, FORMAT TEXT) {sql}"
            )
        except Exception:
            print("EXPLAIN direct échoué - recompilation de la requête via mogrify...")
            compiler = queryset.query.get_compiler(connection=connection)
            sql_compiled, params = compiler.as_sql()
            sql_exe = cur.mogrify(sql_compiled, params).decode("utf-8")
            print(f"SQL compilé ({len(sql_exe)} caractères, exécutable) :")
            print(sql_exe)
            print()
            cur.execute(
                f"EXPLAIN ({explain_opts}BUFFERS, FORMAT TEXT) {sql_exe}"
            )
        for row in cur.fetchall():
            print(row[0])


if __name__ == "__main__":
    main()
