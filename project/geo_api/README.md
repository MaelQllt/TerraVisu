# geo_api

`geo_api` est l'API REST interne qui a progressivement remplacé Elasticsearch
comme source de recherche, de filtrage et de statistiques pour TerraVisu. Elle
s'appuie sur `django-geostore` (modèles `Layer`/`Feature`) et interroge
directement PostgreSQL/PostGIS, qui reste la seule source de vérité des
données géographiques. Contrairement à l'ancienne architecture, où le
frontend interrogeait Elasticsearch sans jamais transiter par Django, toutes
les requêtes passent ici par cette API : les données renvoyées sont donc
toujours à jour, sans délai de réindexation.

Base path : `/api/geo-api/{layer}/feature/`, où `{layer}` accepte un slug ou
un ID numérique de couche.

## Sommaire

- [Architecture](#architecture)
- [Endpoints](#endpoints)
- [Recherche](#recherche)
- [Filtres sur les propriétés](#filtres-sur-les-propriétés)
- [Pagination et sérialisation](#pagination-et-sérialisation)
- [Statistiques, distribution, discrétisation](#statistiques-distribution-discrétisation)
- [Fichiers concernés](#fichiers-concernés)
- [Limites connues](#limites-connues)

## Architecture

`FeatureViewSet` (`views/feature_viewset.py`) hérite du `FeatureViewSet` de
`django-geostore` et y ajoute, par composition de mixins, tout ce qui était
auparavant délégué à Elasticsearch :

- `StatsMixin` et `DiscretizeMixin` : statistiques, distribution et
  discrétisation (voir [Statistiques, distribution, discrétisation](#statistiques-distribution-discrétisation)).
- `PrefixBoostMixin` : boost des résultats dont un champ commence par le
  terme recherché (voir [Recherche](#recherche)).
- `AutoOrderMixin` : ajoute automatiquement un tri sur les champs utilisés
  dans un filtre par opérateur ou par intervalle, pour que les résultats les
  plus pertinents par rapport au filtre remontent en premier.
- `MultipleFieldLookupMixin` (fourni par `django-geostore`) : permet de
  retrouver une entité par sa clé primaire ou par son `identifier`.

Les requêtes passent ensuite par une chaîne de `filter_backends` (voir
[Filtres sur les propriétés](#filtres-sur-les-propriétés)), puis par
`FeaturePagination`, puis par l'un des deux serializers (voir
[Pagination et sérialisation](#pagination-et-sérialisation)).

## Endpoints

| Méthode | URL | Description |
|---|---|---|
| GET, POST | `feature/` | Liste paginée / création d'une entité |
| GET, PUT, PATCH, DELETE | `feature/{identifier}/` | Détail / modification / suppression |
| GET | `feature/count/` | Nombre d'entités filtrées |
| GET | `feature/extent/` | Bounding box de la couche (ou d'un sous-ensemble via `?identifier=`) |
| GET | `feature/distinct/{field}/` | Valeurs distinctes d'une propriété, paginées, filtrables par `?q=` |
| GET | `feature/distinct/all/{field}/` | Idem, sans pagination |
| GET | `feature/stats/{field}/` | Statistiques descriptives d'une propriété numérique |
| GET | `feature/stats/{field}/distribution/` | Histogramme, boxplot, échantillon |
| GET | `feature/discretize/{field}/` | Bornes de classes pour une légende |

Les endpoints CRUD de base (liste, création, détail, modification,
suppression), ainsi que les fonctionnalités héritées de `django-geostore`
(export GeoJSON/KML/GPX via suffixe de format, géométries et relations
supplémentaires) restent fournis par `GeostoreFeatureViewSet` : ce dossier
n'en documente que la surcouche propre à `geo_api`. Se référer au code source
de `django-geostore` pour le détail de ces endpoints hérités.

### Exemple

```
GET /api/geo-api/communes-simplifiees/feature/?search=paris&limit=2

{
    "count": 10,
    "next": "http://localhost:8000/api/geo-api/communes-simplifiees/feature/?limit=2&offset=2&search=paris",
    "previous": null,
    "results": {
        "type": "FeatureCollection",
        "features": [
            {
                "id": 98178,
                "identifier": "75056",
                "properties": { "nom": "Paris", "code": "75056", "search_match": "nom" }
            }
        ]
    }
}
```

`search_match` indique, pour chaque résultat, le champ dans lequel le terme
recherché a été trouvé.

## Recherche

Le paramètre `search` déclenche une recherche plein texte, insensible aux
accents, sur l'ensemble des propriétés de la couche.

Le comportement par défaut annote chaque propriété recherchable avec
`unaccent()` et les combine par `OR`. Sur une couche à beaucoup de
propriétés, cela produit une requête avec autant d'annotations que de
propriétés (jusqu'à une centaine sur les couches les plus larges), ce qui
correspond au goulot d'étranglement identifié comme piste future dans le
rapport de stage.

### Piste testée : indexation dédiée

Une indexation dédiée a été testée pour réduire ce coût, sur la branche
`test_index` : une colonne précalculée `search_text` (concaténation des
propriétés, servie par un index GIN trigram) pour le filtre de base, et une
table `feature_search_prefix` (une ligne par couple entité/champ, avec un
index B-tree fonctionnel sur la valeur normalisée) pour le tri par
pertinence, remplaçant un tri sur autant de colonnes que de propriétés par
un seul entier agrégé (masque de bits, `bit_or`). Les deux structures
seraient maintenues automatiquement via le signal `refresh_data_done` de
`geosource`, avec une commande de backfill (`build_search_text`) pour les
couches déjà existantes.

Cette piste n'est pas validée : elle répond en théorie au problème identifié
dans le rapport, mais n'a pas encore été confrontée à des mesures de
performance en conditions réelles ni revue en profondeur. Elle est encore
susceptible d'être retravaillée ou abandonnée au profit d'une autre
approche ; ce document sera mis à jour selon l'issue de ces tests.

## Filtres sur les propriétés

Tous les filtres suivants s'ajoutent au paramètre `search` et se combinent
entre eux (`filters.py`) :

| Paramètre | Exemple | Effet |
|---|---|---|
| `{key}=valeur` | `?nom=riège` | Contient, insensible aux accents (`NoAccentFilterBackend`) |
| `{key}=v1,v2` | `?id_occ=OCC-09,OCC-11` | Filtre `IN` |
| `{key}>=v`, `>`, `<=`, `<` | `?population>=1000` | Filtre par opérateur, casté en numérique |
| `{key}=min-max` | `?population=1000-5000` | Filtre par intervalle |
| `properties__{key}` | `?properties__population__gte=100000` | Filtre ORM Django direct sur la propriété JSON |
| `bbox` | `?bbox=min_lng,min_lat,max_lng,max_lat` | Filtre spatial (intersection) |
| `ordering` | `?ordering=-population` | Tri ; les propriétés non issues du modèle sont automatiquement préfixées par `properties__` |

Les filtres par opérateur et par intervalle s'appuient sur `cast_numeric`
(`ToFloat`), qui caste une propriété JSON en float de façon sûre : une valeur
non numérique devient `NULL` et est exclue plutôt que de faire échouer la
requête (détaillé dans
[`admin/.../StyleTab/Style/README.md`](../../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/README.md),
qui s'appuie sur le même mécanisme).

## Pagination et sérialisation

`FeaturePagination` (`pagination.py`) pagine par `limit`/`offset`, avec une
taille de page par défaut de 100 et un maximum de 100 000.

Deux serializers coexistent (`serializers.py`) :

- `FeatureListSerializer` : sans géométrie, réponse plus légère, utilisé par
  défaut pour la liste.
- `FeatureGeoSerializer` : format GeoJSON complet, utilisé dès que le
  paramètre `geometry` ou `all` est présent, ou que le suffixe de format
  `.geojson` est demandé.

Le paramètre `fields` (liste de propriétés séparées par des virgules)
restreint les propriétés renvoyées. À défaut, si des filtres par propriété
sont actifs (et que `all`/`geometry` ne sont pas demandés), seules les
propriétés filtrées sont renvoyées automatiquement.

## Statistiques, distribution, discrétisation

Les endpoints `stats/{field}/`, `stats/{field}/distribution/` et
`discretize/{field}/` sont consommés par l'éditeur de style gradué de
l'admin. Leur fonctionnement (implémentation SQL, méthodes de discrétisation,
comportement du mode manuel) est documenté dans
[`admin/.../StyleTab/Style/README.md`](../../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/README.md)
plutôt que répété ici.

## Fichiers concernés

| Fichier | Rôle |
|---|---|
| [`views/feature_viewset.py`](./views/feature_viewset.py) | `FeatureViewSet` : recherche, filtres, `distinct`, `extent`, `count` |
| [`views/stats.py`](./views/stats.py) | Endpoints `stats/` et `stats/.../distribution/` |
| [`views/discretize.py`](./views/discretize.py) | Endpoint `discretize/` |
| [`filters.py`](./filters.py) | Filtres par propriété, `cast_numeric`, classification des paramètres d'URL |
| [`mixins.py`](./mixins.py) | `AutoOrderMixin`, `PrefixBoostMixin` |
| [`pagination.py`](./pagination.py) | `FeaturePagination` |
| [`serializers.py`](./serializers.py) | `FeatureListSerializer`, `FeatureGeoSerializer` |
| [`search_text.py`](./search_text.py) | Piste testée : remplissage de `search_text` et `feature_search_prefix` |
| [`prefix_search.py`](./prefix_search.py) | Piste testée : requêtes SQL du chemin de recherche indexé |
| [`signals.py`](./signals.py) | Piste testée : réindexation automatique au rafraîchissement d'une source |
| [`management/commands/build_search_text.py`](./management/commands/build_search_text.py) | Piste testée : commande de backfill de la recherche indexée |
| [`urls.py`](./urls.py) | Montage du routeur DRF sous `{layer}/feature/` |

## Limites connues

- La recherche par défaut (annotations `unaccent()` répétées) devient
  coûteuse à mesure que le nombre de propriétés recherchables d'une couche
  augmente, sans protection par `statement_timeout` PostgreSQL sur les
  requêtes les plus lourdes.
- La piste d'indexation dédiée testée sur `test_index` n'a pas encore été
  mesurée en conditions réelles ; elle introduit par ailleurs sa propre
  limite (masque de bits sur 63 champs recherchables maximum par couche) qui
  resterait à traiter si l'approche était conservée.
