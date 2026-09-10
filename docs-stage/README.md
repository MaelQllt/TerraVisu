# TerraVisu : remplacement d'Elasticsearch par geo_api

TerraVisu s'appuyait depuis longtemps sur Elasticsearch pour la recherche, le
filtrage et les statistiques affichées côté carte, interrogé directement par
le frontend sans jamais transiter par le backend Django. Cette dépendance
avait accumulé de la dette technique : une limite native de 10 000 résultats
non gérée, tronquant silencieusement les grands jeux de données, des
dépendances vieillissantes côté frontend, et des requêtes de recherche
difficiles à écrire et à maintenir (bibliothèque `bodybuilder` au-dessus du
DSL Elasticsearch).

Le travail engagé a construit `geo_api`, une API REST interne bâtie sur
`django-geostore` et PostgreSQL/PostGIS, qui remplace progressivement
Elasticsearch module par module. Cette même API a aussi rendu possibles
plusieurs apports directement cartographiques (éditeur de style gradué,
prévisualisation des données à l'import) qui n'existaient pas auparavant.

Ce document sert de point d'entrée : il résume ce qui a changé, où trouver
le détail de chaque brique, et l'état actuel de ce qui reste à faire.

## Sommaire

- [Ce qui a changé](#ce-qui-a-changé)
- [Documentation détaillée](#documentation-détaillée)
- [État de la migration](#état-de-la-migration)
- [Limites et pistes en cours](#limites-et-pistes-en-cours)
- [Repères dans le code](#repères-dans-le-code)

## Ce qui a changé

| | Avant | Après |
|---|---|---|
| Recherche carte | `elasticsearch.msearch()` côté client, géométrie incluse, données à jour uniquement après réindexation | Appels REST `geo-api/{layer}/feature/`, géométrie récupérée à part, données toujours à jour |
| Vue tabulaire | Pagination/tri/filtrage via `bodybuilder` + requêtes ES, résultats plafonnés à 10 000 | Un seul appel REST paramétré, pas de plafond |
| Emprise de couche | Agrégation `geo_bounds` ES | `ST_Extent` PostGIS |
| Style d'une couche | Bornes de classes saisies à l'aveugle, pas de distribution visible | Statistiques, distribution, discrétisation et palettes normalisées avant application |
| Import de données | 3 modèles de source fichier séparés (GeoJSON, Shapefile, GeoPackage) | Modèle unifié `GeoFileSource`, détection du format par extension |
| Validation d'un import | À l'aveugle | Aperçu (métadonnées, échantillon, emprise sur carte) avant validation |

## Documentation détaillée

| Document | Contenu |
|---|---|
| [`geo_api`](../project/geo_api/README.md) | Architecture de l'API, endpoints, filtres, recherche |
| [Éditeur de style gradué](../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/README.md) | Statistiques, distribution, discrétisation, palettes Dicopal |
| [Prévisualisation de fichier à l'import](../admin/src/modules/RA/DataSource/components/FILE_PREVIEW.md) | Aperçu d'un fichier avant import, GeoPackage multi-couches, sources PostGIS |
| [Progression d'import et téléchargement](../admin/src/modules/RA/DataSource/components/PROGRESS_DOWNLOAD.md) | Barre de progression pendant un rafraîchissement, téléchargement du fichier source |
| [Plan de reprise](./NEXT_STEPS.md) | Les points ci-dessous, classés par risque/impact, avec une action suggérée pour chacun |

## État de la migration

La migration s'est faite module par module, derrière un indicateur de
fonctionnalité (`USE_GEO_API` ou `SEARCH_WITH_ELASTICSEARCH` côté frontend), plutôt qu'en un seul bloc.
Certaines dépendances à Elasticsearch restent actives :

- le filtre PivotQL de l'admin
  (`admin/src/modules/RA/DataLayer/components/SourceFilterField.js`), qui
  compile encore les filtres vers une requête Elasticsearch
  (`pivotql-compiler-elasticsearch`) ;
- le module Sheet côté public
  (`front/src/views/Sheet/utils/useEsClient.js`), qui interroge encore
  Elasticsearch directement ;
- le highlight des résultats sur la carte
  (`front/src/views/Visualizer/View/Search/searchService.js`), qui appelle
  encore `elasticsearch.msearch()` alors que la recherche elle-même passe
  déjà par `geo_api`.

Ces trois points n'ont pas de date de résorption fixée à ce stade.

## Limites et pistes en cours

Version priorisée et actionnable de cette liste : voir le
[plan de reprise](./NEXT_STEPS.md).

- **Recherche sur les couches à beaucoup de propriétés** : la recherche par
  défaut (une annotation `unaccent()` par propriété recherchable) devient
  coûteuse sur les couches les plus larges. Une indexation dédiée a été
  testée sur la branche `test_index` (voir la doc `geo_api`), mais n'a pas
  encore été mesurée en conditions réelles ni confirmée comme solution
  retenue.
- **Pas de `statement_timeout` PostgreSQL** en protection des requêtes de
  recherche, d'agrégation ou de discrétisation les plus coûteuses.
- **Deux implémentations de Jenks coexistent** dans l'éditeur de style
  gradué (l'historique, `ST_ClusterKMeans`, et une nouvelle plus fidèle aux
  outils de référence), sans arbitrage tranché entre les deux.
- **Deux parseurs CSV distincts** existent entre la prévisualisation à
  l'import et l'import réel, avec un risque de divergence de comportement
  entre les deux.
- **Aucun plan de benchmark n'a été exécuté** : les constats de performance
  mentionnés dans les différents documents restent, pour l'instant, fondés
  sur la lecture du code plutôt que sur des mesures.

## Repères dans le code

| Dossier | Contenu |
|---|---|
| [`project/geo_api/`](../project/geo_api/) | API REST interne (recherche, filtres, statistiques, discrétisation) |
| [`project/terra_layer/style/`](../project/terra_layer/style/) | Calcul des bornes de classes et des styles Mapbox GL |
| [`project/geosource/`](../project/geosource/) | Modèles de source de données, import, prévisualisation |
| [`admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/`](../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/) | Éditeur de style gradué (frontend admin) |
| [`admin/src/modules/RA/DataSource/components/`](../admin/src/modules/RA/DataSource/components/) | Formulaires de source, prévisualisation, rapport d'import (frontend admin) |
