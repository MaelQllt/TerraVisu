# Benchmark geo_api vs Elasticsearch - Rapport consolidé

## Cadre expérimental

Le présent rapport compare les temps de réponse de deux moteurs de recherche, `geo_api` (recherche SQL dans PostgreSQL) et Elasticsearch (ES), sur trois jeux de données de tailles et de complexités distinctes. L'objectif est d'établir une ligne de base chiffrée en prévision du plan d'optimisation UP_SEARCH, dont le but final est de retirer Elasticsearch au profit du seul `geo_api`.

Chaque cellule (couple layer × terme × moteur) est mesurée sur **10 itérations**. Les deux moteurs renvoient les 10 premiers résultats (`size=10`) et le total des résultats (`track_total_hits:true` pour ES). Les requêtes sont construites de façon symétrique :

- `geo_api` : `GET /api/geo-api/{layer}/feature/?search={terme}&limit=10`
- Elasticsearch : `POST /{layer}/_search` avec `query_string "*{terme}*"`

Les données brutes (une ligne par itération, puis les agrégats) sont disponibles dans `benchmark_results.csv` ; le détail complet (statistiques et cinq premiers résultats de chaque cellule) figure dans `benchmark_report.json`.

Le tableau suivant présente la taille de chaque jeu de données et le nombre de champs (attributs) par entité :

| Layer | Nombre d'entités | Nombre de champs par entité |
|-------|-----------------:|----------------------------:|
| communes-simplifiees | 35 191 | 2 |
| communes-x4 | 140 764 | 2 |
| paca_gis | 1 524 412 | 59 |

---

## 1. Temps de réponse

Les temps de réponse moyens et le 95e percentile (entre parenthèses), exprimés en millisecondes, sont reportés dans le tableau ci-dessous. La dernière colonne exprime le temps d'Elasticsearch en proportion de celui de `geo_api`.

| Layer | Terme | geo_api (moyen, p95) | ES (moyen, p95) | Ratio ES/geo_api |
|-------|-------|----------------------:|-----------------:|-----------------:|
| communes-simplifiees | toulou | 325 (379) | 13 (16) | 0,04 |
| communes-simplifiees | bézi | 338 (381) | 13 (15) | 0,04 |
| communes-simplifiees | ajac | 321 (362) | 14 (17) | 0,04 |
| communes-x4 | toulou | 996 (1 125) | 38 (57) | 0,04 |
| communes-x4 | bézi | 1 031 (1 113) | 30 (34) | 0,03 |
| communes-x4 | ajac | 1 028 (1 131) | 32 (36) | 0,03 |
| paca_gis | dig | **274 377** (356 270) | 284 (334) | 0,001 |
| paca_gis | fré | **720 986** (742 570) | 300 (350) | 0,0004 |
| paca_gis | anti | **279 562** (280 368) | 305 (390) | 0,001 |

Le tableau suivant synthétise l'évolution du temps moyen de `geo_api` en fonction de la taille du jeu de données (et du nombre de champs, qui croît avec paca_gis).

| | communes-simplifiees (35k, 2 champs) | communes-x4 (140k, 2 champs) | paca_gis (1,5M, 59 champs) |
|---|:---:|:---:|:---:|
| geo_api | ~325 ms | ~1 020 ms | **~275 000 à ~721 000 ms** |
| Elasticsearch | ~13 ms | ~33 ms | ~280 à ~305 ms |
| Ratio (par rapport à communes-simplifiees) | 1 | ~3 | **~970 à ~2 400** |

### Interprétation

Trois observations se dégagent.

Premièrement, le temps de `geo_api` croît de manière **fortement super-linéaire** avec la taille du jeu de données. Sur paca_gis, qui ne compte qu'environ 44 fois plus d'entités que communes-simplifiees, le temps de réponse est multiplié par un facteur compris entre ~900 et ~2 400 (jusqu'à 12 minutes pour une seule requête). Cette croissance tient pour l'essentiel au nombre de champs : les 59 champs de paca_gis entraînent la génération de 177 annotations de recherche (un `Unaccent(KeyTextTransform(...))` par champ, complété d'un boost sur ~118 colonnes), chaque annotation ajoutant au coût du filtrage et du tri. C'est ce qui distingue structurellement paca_gis des deux autres jeux, pourtant plus grands en proportion que communes-simplifiees mais limités à deux champs.

Deuxièmement, parmi les requêtes sur paca_gis, `fré` est environ 2,6 fois plus lent que `dig` et `anti` (12 minutes contre 4 min 30). Cette requête correspond au plus grand nombre de résultats (1,5 million) ; elle produit donc davantage de lignes à filtrer et à ordonner. L'examen du plan d'exécution (EXPLAIN) confirme que le scan et le filtre représentent environ 95 % du temps global, le tri s'avérant négligeable.

Troisièmement, Elasticsearch présente un temps de réponse **quasi constant** (entre ~280 et ~390 ms) quelle que soit la requête. Sur la plage considérée, ce moteur est donc insensible au volume de données et au nombre de champs.

---

## 2. Pertinence des résultats

Outre les performances, il importe de comparer le contenu des résultats renvoyés par les deux moteurs. Deux critères sont examinés : le nombre total de résultats et l'ordre des cinq premiers résultats.

### 2.1 Nombre total de résultats

| Layer | Terme | geo_api | ES | Écart |
|-------|-------|--------:|---:|-------|
| communes-simplifiees | toulou | 5 | 5 | identique |
| communes-simplifiees | bézi | 15 | 7 | ES compte moins (−8) |
| communes-simplifiees | ajac | 9 | 9 | identique |
| communes-x4 | toulou | 20 | 20 | identique |
| communes-x4 | bézi | 60 | 28 | ES compte moins (−32) |
| communes-x4 | ajac | 36 | 36 | identique |
| paca_gis | dig | 6 491 | 6 491 | identique |
| paca_gis | fré | **1 524 406** | **22 751** | ES compte ~67 fois moins |
| paca_gis | anti | 38 329 | 38 329 | identique |

Les divergences portent exclusivement sur les termes **accentués** (`bézi`, `fré`). Elles s'expliquent par une différence de normalisation du texte : `geo_api` applique la fonction `Unaccent()`, et fait donc correspondre le préfixe quelle que soit l'accentuation ou la casse (`fré`, `fre`, `FRE`, etc.) ; Elasticsearch, dépourvu d'une telle normalisation, ne retient que les documents contenant la chaîne accentuée exacte. Pour les termes sans accent (`toulou`, `ajac`, `dig`, `anti`), les deux moteurs renvoient le même total.

Il en résulte une implication directe pour le produit : le comportement de recherche des deux moteurs **n'est pas équivalent** sur les termes accentués. Le passage à `geo_api` modifierait le nombre de résultats affichés à l'utilisateur pour de tels termes. Cette différence doit être actée avant toute suppression d'Elasticsearch.

### 2.2 Ordre des cinq premiers résultats

`geo_api` applique un renforcement (boost) sur les correspondances par préfixe - contrainte produit selon laquelle, par exemple, `toulou` doit renvoyer Toulouse en tête - alors qu'Elasticsearch n'applique pas ce traitement. Les premiers résultats divergent donc sensiblement.

| Layer | Terme | geo_api (top 5) | ES (top 5) | Commentaire |
|-------|-------|-----------------|------------|-------------|
| communes-simplifiees | toulou | 66213;31555;39533;40318;31575 | 39533;31555;31575;40318;66213 | mêmes cinq objets, ordre différent |
| communes-simplifiees | bézi | 34032;62127;31067;16027;16028 | 34310;32403;34178;34032;34139 | ensembles différents, un seul objet commun |
| communes-x4 | toulou | 66213;66213-2;66213-3;66213-4;31555 | 39533;31555;31575;40318;66213 | les variantes de Toulouse sont placées en tête |
| paca_gis | dig | 2938 (Barcelonnette); 8468xx (Marseille 2), ×3 | 228/104/2501 (Aiglun, Barcelonnette) | ensembles et ordre différents |
| paca_gis | fré | 719921/22 (Orgon); 12402xx (St-Cyr-sur-Mer), ×3 | 5868 (Castellane); 6710 (Céreste); 7802/03… | ensembles et ordre différents |
| paca_gis | anti | 1249xx (Antibes), ×4 | 567-570 (Allos); 2192 (Banon) | Antibes en tête pour geo_api uniquement |

Lorsque le terme ne comporte ni accent ni boost déterminant (`toulou`, `ajac`), les deux moteurs renvoient les **mêmes objets**, seul l'ordre diffère. Dès qu'intervient le boost par préfixe, l'ordre diverge nettement : `geo_api` place systématiquement en tête les correspondances par préfixe pertinent (Antibes pour `anti`, Toulouse pour `toulou`), ce qu'Elasticsearch ne fait pas. La pertinence n'est donc pas reproduite à l'identique par Elasticsearch ; il conviendra soit de la préserver dans `geo_api` si l'on vise l'équivalence, soit d'assumer un changement de comportement.

---

## 3. Conclusion

Trois enseignements se dégagent de cette campagne de mesures.

1. **Performance.** Elasticsearch est plus rapide que `geo_api` d'un facteur compris entre ~25 et ~2 400, l'écart étant maximal sur le jeu de données le plus volumineux et le plus riche en champs. Sur paca_gis, une simple requête `geo_api` peut dépasser dix minutes. Un tel coût est rédhibitoire pour un usage interactif, et justifie pleinement l'optimisation prévue (colonne générée associée à un index GIN trigram) dans le cadre d'UP_SEARCH.

2. **Comportement.** Les deux moteurs ne sont pas strictement équivalents, sur deux points documentés : la gestion des accents (geo_api, grâce à `Unaccent()`, renvoie davantage de résultats qu'Elasticsearch) et le renforcement par préfixe (geo_api place les correspondances pertinentes en tête). Avant de retirer Elasticsearch, il conviendra de trancher entre la reproduction de ce comportement dans `geo_api` et l'assomption d'un changement de pertinence.

3. **Données.** Les fichiers bruts (`benchmark_results.csv`, `benchmark_report.json`) permettent de prolonger l'analyse à volonté : distribution de chaque itération, dispersions, ou extension de la campagne à d'autres termes ou layers.
