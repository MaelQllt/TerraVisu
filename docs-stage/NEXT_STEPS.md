# Plan de reprise

Ce document liste les points restants identifiés dans la documentation
technique ([vue d'ensemble](./README.md)), classés par risque et impact en
production plutôt que par effort ou par ordre chronologique. Chaque item
indique l'état actuel, le risque associé, et une action suggérée.

## Sommaire

- [Palier 1 : risque de production actuel](#palier-1--risque-de-production-actuel)
- [Palier 2 : migration Elasticsearch restante](#palier-2--migration-elasticsearch-restante)
- [Palier 3 : cohérence et dette de qualité](#palier-3--cohérence-et-dette-de-qualité)

## Palier 1 : risque de production actuel

Ces deux points sont des garde-fous techniques : ils ne nécessitent pas de
décision produit, seulement du temps d'implémentation.

### 1. Pas de `statement_timeout` PostgreSQL

**État actuel** : aucun des endpoints `geo_api` (recherche, `stats/`,
`distribution/`, `discretize/`) n'est protégé par une limite de temps
d'exécution côté base de données.

**Risque** : une requête coûteuse sur une couche volumineuse (recherche non
indexée, discrétisation sur des centaines de milliers d'entités) peut
monopoliser une connexion PostgreSQL sans limite, avec un impact potentiel
sur les autres requêtes en attente.

**Action suggérée** : ajouter un `statement_timeout` raisonnable, au niveau
des endpoints les plus exposés ou de la connexion applicative.

Détaillé dans [`geo_api`](../project/geo_api/README.md) et
[l'éditeur de style gradué](../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/README.md).

### 2. Piste d'indexation dédiée (`test_index`) non benchmarkée

**État actuel** : une indexation dédiée (`search_text` + index GIN trigram,
table `feature_search_prefix`) a été testée sur la branche `test_index` pour
répondre au coût de la recherche sur les couches à beaucoup de propriétés.

**Risque** : fusionner cette piste sans l'avoir mesurée en conditions réelles
ajoute une dépendance de maintenance (deux structures supplémentaires à tenir
à jour, un signal, une commande de backfill) pour un gain non confirmé.

**Action suggérée** : benchmarker sur un jeu de données représentatif d'une
production réelle avant toute fusion, ou abandonner la piste si le chemin de
recherche standard s'avère suffisant en pratique.

Détaillé dans [`geo_api`](../project/geo_api/README.md#recherche).

## Palier 2 : migration Elasticsearch restante

Ces quatre dépendances à Elasticsearch sont une dette connue, sans risque
immédiat, mais elles empêchent de retirer Elasticsearch du projet. Classées
du plus isolé au plus lourd.

### 3. Highlight de recherche (`searchService.js`)

Le highlight des résultats sur la carte appelle encore
`elasticsearch.msearch()`, alors que la recherche elle-même passe déjà par
`geo_api`. Le plus isolé des trois : bon point d'entrée pour continuer la
migration sans dépendre des deux autres.

### 4. Filtre PivotQL de l'admin (`SourceFilterField.js`)

Compile encore les filtres vers une requête Elasticsearch
(`pivotql-compiler-elasticsearch`). Nécessite de faire correspondre le
langage de filtres PivotQL aux filtres par propriété déjà exposés par
`geo_api` (documentés dans
[`geo_api`](../project/geo_api/README.md#filtres-sur-les-propriétés)).

### 5. Module Sheet (`front/src/views/Sheet`, `useEsClient.js`)

Interroge encore Elasticsearch directement. À traiter après les deux
précédentes.

### 6. Panneau de filtres interactif (`FiltersPanel`)

Un import commenté révèle une migration commencée puis abandonnée :

```js
// front/src/terra-front/modules/Visualizer/LayersTree/LayersTreeItem/FiltersPanel/FiltersPanelContent/index.js
import FiltersPanelContent from './FiltersPanelContent';
console.log('[FiltersPanel] version ES');

// // version geo-api
// import FiltersPanelContent from './FiltersPanelContentGeoAPI';
// console.log('[FiltersPanel] version geo-api');
```

Un fichier `FiltersPanelContentGeoAPI.js` existe déjà à côté, mais l'import
actif pointe toujours vers la version Elasticsearch, avec un `console.log`
de debug laissé en production. À vérifier : l'état d'avancement réel de
`FiltersPanelContentGeoAPI.js` avant de basculer l'import.

## Palier 3 : cohérence et dette de qualité

Pas de risque immédiat, mais ces points coûtent cher à maintenir s'ils
restent indéfiniment en l'état.

### 7. Coexistence des deux implémentations de Jenks

Décision à trancher : garder les deux méthodes dans l'interface (au risque de
dérouter l'administrateur) ou migrer progressivement les couches existantes
vers la nouvelle implémentation. Détaillé dans
[l'éditeur de style gradué](../admin/src/modules/RA/DataLayer/components/tabs/StyleTab/Style/README.md#discrétisation).

### 8. Deux parseurs CSV distincts

La prévisualisation à l'import (`_preview_csv`, module `csv` de Python) et
l'import réel (`CSVSource.get_file_as_sheet`, `pyexcel`) sont deux parseurs
indépendants, avec un risque de divergence de comportement entre les deux.
Détaillé dans
[la prévisualisation de fichier](../admin/src/modules/RA/DataSource/components/FILE_PREVIEW.md#cas-du-csv).

### 9. `source_filter` : expressions avec OR/NOT non appliquées à la table et à la recherche

**État actuel** : `Layer.source_filter` (expression PivotQL admin) est
compilé côté client vers `geo_api` (`front/src/views/Visualizer/pivotqlToGeoApi.js`)
pour que la vue tabulaire et la recherche respectent le même filtre que le
style carte. Seules les expressions en ET simple (`==`, `<`, `<=`, `>`, `>=`,
`IN` combinés par `and`) sont supportées : `geo_api` n'a aujourd'hui aucun
moyen d'exprimer un `or` ou une négation dans ses paramètres de requête.

**Risque** : une expression avec `or`/`not`/`!=` continue de filtrer
correctement la carte (le compilateur Mapbox GL, lui, sait le faire), mais la
vue tabulaire et la recherche affichent alors l'intégralité des données, sans
filtre. Le repli est désormais visible (`console.warn` côté front,
`"source_filter: expression not supported by geo_api filters..."`), donc ce
n'est plus un échec silencieux, mais l'incohérence entre carte et données
reste possible tant qu'un tel filtre est utilisé.

**Action suggérée** : si des filtres avec `or`/`not` sont réellement utilisés
en production, étendre `geo_api` pour accepter l'AST PivotQL complet
(transmis en JSON par le front, déjà disponible via le parseur existant) et
le compiler côté backend en filtre Django `Q` (`&&`→`Q & Q`, `||`→`Q | Q`,
`!`→`~Q`). Sinon, documenter que seules les expressions en ET simple sont
supportées pour ce champ.

### 10. Plan de benchmark jamais exécuté

Aucune mesure de performance en conditions réelles n'a été faite à ce jour :
les constats cités dans les autres documents restent fondés sur la lecture du
code. Exécuter ce plan servirait de base factuelle pour trancher les points 1
et 2 en particulier.

---

Retour à la [vue d'ensemble](./README.md).
