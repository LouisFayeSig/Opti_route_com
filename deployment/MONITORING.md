# Suivi d'utilisation Opti Route

## Collecte Azure

Conserver `Azure Monitor` comme destination des journaux de `env-optiroute`.
Log Analytics est le stockage et le moteur de requêtes utilisé derrière Azure Monitor.
Ne pas créer un deuxième workspace ou une seconde destination pour les mêmes événements.

Dans le portail Azure :

1. Ouvrir `env-optiroute` > Surveillance > Paramètres de diagnostic.
2. Vérifier les paramètres existants avant d'en ajouter un. Activer les catégories
   `ContainerAppConsoleLogs` et `ContainerAppSystemLogs` et l'envoi vers le workspace
   existant `log-analytics-optiroute`. Vérifier les droits sur les deux ressources.
3. Dans le workspace, vérifier la rétention de ces tables (cible initiale : 30 jours).
4. Déployer la version instrumentée de l'application. Ouvrir une session, lancer un
   calcul et demander un PDF. Attendre quelques minutes avant de consulter les logs.
5. Vérifier que `ContainerAppConsoleLogs` contient ces événements. Les tables ne
   sont pas nécessairement visibles avant la première ingestion.

Références Microsoft : [destinations et diagnostic](https://learn.microsoft.com/en-us/azure/container-apps/log-options),
[schéma de la table](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerappconsolelogs).
Avec Azure Monitor, la table s'appelle `ContainerAppConsoleLogs`, champ `Log`.
L'ancien chemin direct Log Analytics utilise `ContainerAppConsoleLogs_CL`, champ `Log_s`.
Les requêtes ci-dessous visent le chemin Azure Monitor retenu ici.

## Événements et limites

Les événements JSON `opti_route.usage.v1` sont écrits sur stdout, sans SDK ni stockage
applicatif supplémentaire. Chaque événement possède un UUID, une date UTC, un identifiant
de session, l'Object ID Entra et le tenant (ou l'identifiant du compte local), le rôle
et la méthode d'authentification. Le nom et l'e-mail ne sont pas enregistrés.

- `session_started` : une fois par session Streamlit et identité. Les interactions et
  reruns ne créent pas de connexion supplémentaire. Un rechargement, un autre onglet ou
  une reconnexion WebSocket peut créer une nouvelle session. Cela mesure les ouvertures
  de session applicative, pas les authentifications Microsoft ni la présence en ligne.
- `api_call` : chaque appel serveur Azure Maps ou IGN, avec service, opération,
  statut HTTP final, succès HTTP, durée incluant les retries et nombre de retries
  disponibles dans la réponse. Une réponse HTTP 200 invalide pour le métier peut
  néanmoins être comptée comme un succès HTTP.
- `route_calculated` / `route_recalculated` : résultat et durée des fonctions de calcul.
  Un recalcul appelle aussi le calcul : utiliser `route_calculated` pour compter tous
  les calculs, et `route_recalculated` pour identifier leur sous-ensemble de recalculs.
- `pdf_download_requested` : clic de téléchargement, sans garantie d'enregistrement
  effectif du fichier par le navigateur.

Un appel journalisé n'est pas une unité de facturation : les retries et les éléments
de matrice peuvent modifier la consommation Azure. Les hits de cache sans appel réseau,
les tuiles chargées directement par le navigateur et les opérations Blob/Key Vault ne
sont pas comptés ici. Les comptes locaux partagés ne permettent pas de distinguer les
personnes : utiliser des comptes Entra individuels pour ce besoin.

Pour les authentifications réussies ou échouées, consulter les journaux Entra filtrés
sur l'application Opti Route. Leur envoi vers le workspace se configure séparément
dans Entra, selon les droits et licences du tenant. L'accès à l'application peut encore
être refusé par ses habilitations après une authentification Entra réussie.

Ne pas ajouter les paramètres HTTP, corps, adresses, jetons ou messages d'exception
aux événements. Les identifiants utilisateurs permettent une analyse individuelle :
limiter les droits de lecture du workspace aux personnes chargées de l'exploitation.

## Requêtes Log Analytics

Exécuter chaque bloc séparément. Les journées utilisent l'heure de Paris et suivent
les changements heure d'été / heure d'hiver. La période est limitée à 30 jours.
La déduplication par UUID protège contre une double ingestion du même événement.

### Sessions et utilisateurs actifs par jour

```kusto
ContainerAppConsoleLogs
| where TimeGenerated >= ago(30d) and ContainerAppName == "app-optiroute"
| extend e = parse_json(Log)
| where tostring(e.schema) == "opti_route.usage.v1" and tostring(e.event) == "session_started"
| summarize take_any(*) by EventId=tostring(e.event_id)
| extend Jour=startofday(datetime_utc_to_local(todatetime(e.timestamp), "Europe/Paris"))
| summarize Sessions=count(), UtilisateursActifs=dcount(tostring(e.user_id)) by Jour
| order by Jour asc
```

### Appels par utilisateur, jour et service

```kusto
ContainerAppConsoleLogs
| where TimeGenerated >= ago(30d) and ContainerAppName == "app-optiroute"
| extend e = parse_json(Log)
| where tostring(e.schema) == "opti_route.usage.v1" and tostring(e.event) == "api_call"
| summarize take_any(*) by EventId=tostring(e.event_id)
| extend Jour=startofday(datetime_utc_to_local(todatetime(e.timestamp), "Europe/Paris"))
| summarize Appels=count(), Erreurs=countif(tobool(e.success) == false),
    DureeMoyenneMs=avg(todouble(e.duration_ms)), P95Ms=percentile(todouble(e.duration_ms), 95),
    RetriesObserves=sum(toint(e.retry_count))
    by Jour, Utilisateur=tostring(e.user_id), Service=tostring(e.service), Operation=tostring(e.operation)
| order by Jour desc, Appels desc
```

### Calculs et téléchargements par utilisateur

```kusto
ContainerAppConsoleLogs
| where TimeGenerated >= ago(30d) and ContainerAppName == "app-optiroute"
| extend e = parse_json(Log)
| where tostring(e.schema) == "opti_route.usage.v1"
| where tostring(e.event) in ("route_calculated", "route_recalculated", "pdf_download_requested")
| summarize take_any(*) by EventId=tostring(e.event_id)
| extend Jour=startofday(datetime_utc_to_local(todatetime(e.timestamp), "Europe/Paris"))
| summarize Nombre=count(), Echecs=countif(tobool(e.success) == false)
    by Jour, Utilisateur=tostring(e.user_id), Evenement=tostring(e.event)
| order by Jour desc
```

Enregistrer ces requêtes dans le workspace, puis les ajouter à un Azure Workbook pour
obtenir un tableau de bord unique. `dcount` est une estimation des utilisateurs distincts.
Les journaux ne reconstituent pas l'usage antérieur au déploiement et à l'activation de
la collecte. Ils ne constituent pas un journal d'audit à livraison garantie.

## Validation de la version

Avec l'environnement de développement installé :

```powershell
poetry run pytest tests/test_telemetry.py tests/test_azure_maps.py tests/test_ign_geocoding.py tests/test_optimizer_planner_exports.py
poetry run ruff check app.py src/opti_route/telemetry.py src/opti_route/azure_maps.py src/opti_route/ign_geocoding.py src/opti_route/planner.py tests/test_telemetry.py
```

Les tests couvrent le comptage unique des sessions, le changement d'identité,
l'isolation entre threads, les appels en erreur, les retries et l'absence de données
de requête ou de messages privés dans les événements. Vérifier également dans Azure
qu'une interaction Streamlit ne génère pas un second `session_started` et que les
appels de deux comptes distincts portent leurs identifiants respectifs.
