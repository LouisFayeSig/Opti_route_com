# Opti Route Com

Application Streamlit interne pour préparer et optimiser des tournées commerciales à partir d'un portefeuille clients.

Le guide de déploiement Entra ID, Managed Identity, Blob Storage et Container Apps est disponible
dans [deployment/AZURE.md](deployment/AZURE.md).

## Fonctionnalités du MVP

- import administrateur d'un portefeuille aux formats CSV, XLS, XLSX, XLSM ou XLSB ;
- détection automatique des colonnes et correspondance manuelle si leurs noms varient ;
- choix de la feuille et de la ligne d'en-tête ;
- séparation des rôles administrateur et utilisateur ;
- stockage du portefeuille normalisé dans SQLite sans conservation du classeur brut ;
- choix obligatoire du commercial ;
- parcours de préparation en quatre étapes : prospects, départ, arrivée et revue ;
- choix utilisateur du nombre total de visites, entre le nombre de rendez-vous prévus et le maximum administrateur ;
- sélection ou désélection individuelle des adresses avant le calcul ;
- ajout d'un ou plusieurs rendez-vous déjà planifiés : ils sont inclus même hors rayon et conservés lors d'un recalcul ;
- retrait des seules visites facultatives depuis le résultat avec recalcul complet de la tournée ;
- départ depuis la position du navigateur ou un client existant, affiché à l'ordre 0 ;
- choix du retour au départ, de la dernière visite ou d'une adresse d'arrivée spécifique ;
- cache SQLite des adresses géocodées ;
- présélection des clients par rayon géographique ;
- matrice routière Azure Maps et optimisation OR-Tools ;
- affichage Azure Maps, ordre des visites et indicateurs ;
- noms des entreprises affichés directement à côté des points sur la carte ;
- export PDF avec capture cartographique numérotée, ainsi que partage vers Google Maps ;
- neutralisation des cellules pouvant être interprétées comme des formules dans les exports ;
- noms d'exports horodatés pour éviter les doublons ;
- authentification Microsoft Entra ID ou identifiant/mot de passe générique ;
- mode d'estimation local lorsque la clé Azure Maps n'est pas configurée.

## Installation

Une version Python 3.x comprise entre 3.11 inclus et 4.0 exclu est nécessaire.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Renseigner ensuite la configuration Azure Maps dans `.env` :

```dotenv
AZURE_MAPS_URI=https://atlas.microsoft.com
AZURE_MAPS_SUBSCRIPTION_KEY=remplacer_par_la_cle
```

`AZURE_MAPS_ENDPOINT` peut être utilisé à la place de `AZURE_MAPS_URI`. L'application emploie les API Azure Maps `2025-01-01` pour le géocodage, la matrice et le tracé routier.

## Authentification

L'application est verrouillée par défaut. Trois modes sont disponibles avec `AUTH_MODE` :

- `password` : identifiant et mot de passe génériques, solution minimale ;
- `entra` : authentification Microsoft Entra ID via OpenID Connect ;
- `none` : aucune authentification, uniquement pour le développement local.

### Identifiant et mot de passe génériques

Ajouter les valeurs suivantes dans `.env`, sans jamais versionner ce fichier :

```dotenv
AUTH_MODE=password
AUTH_USERNAME=collaborateur
AUTH_PASSWORD=choisir_un_mot_de_passe_long_et_unique
ADMIN_USERNAME=administrateur
ADMIN_PASSWORD=choisir_un_autre_mot_de_passe_long_et_unique
```

Les comparaisons sont faites en temps constant et cinq échecs successifs bloquent la session pendant
30 secondes. Ce mode convient à un petit usage interne derrière HTTPS. Pour une exposition plus large,
préférer Entra ID.

Le compte `ADMIN_USERNAME` dispose du panneau **Administration**. Le compte utilisateur ne voit ni
l'import ni les contrôles des contraintes et peut seulement choisir le commercial, les entreprises,
le départ et une éventuelle adresse d'arrivée.

### Microsoft Entra ID

1. Dans Entra ID, créer une inscription d'application de type Web, limitée au tenant de l'entreprise.
2. Ajouter l'URI de redirection `http://localhost:8501/oauth2callback` en local ou
   `https://adresse-de-production/oauth2callback` en production.
3. Créer un secret client.
4. Copier `.streamlit/secrets.toml.example` vers `.streamlit/secrets.toml` et renseigner le tenant,
   l'identifiant d'application, le secret client, l'URI de redirection et une valeur aléatoire longue
   pour `cookie_secret`.
5. Définir `AUTH_MODE=entra` dans `.env`, puis redémarrer Streamlit.

L'autorisation utilise trois **App Roles Entra**, présents dans le claim signé `roles` du jeton :

- `OptiRoute.ATC` : accès au seul code ATC configuré dans l'application ;
- `OptiRoute.Director` : accès à tous les ATC des agences configurées ;
- `OptiRoute.Admin` : accès global, import du portefeuille, habilitations et paramètres.

Le fichier [deployment/entra-app-roles.json](deployment/entra-app-roles.json) contient les trois
définitions prêtes à reporter dans le manifeste de l'inscription d'application. Dans **Enterprise
applications → Utilisateurs et groupes**, attribuer exactement un de ces rôles à chaque utilisateur
ou groupe autorisé. Configurer ensuite :

```dotenv
ENTRA_TENANT_ID=identifiant-du-tenant
ENTRA_ROLE_ATC=OptiRoute.ATC
ENTRA_ROLE_DIRECTOR=OptiRoute.Director
ENTRA_ROLE_ADMIN=OptiRoute.Admin
```

L'application refuse par défaut un jeton sans rôle reconnu, provenant d'un autre tenant ou contenant
plusieurs rôles Opti Route. Chaque compte doit donc recevoir exactement un rôle.
Les anciennes listes `ADMIN_EMAILS` ne donnent plus aucun droit en mode Entra.

Après la première connexion de l'administrateur, utiliser l'onglet **Administration →
Habilitations** pour associer l'Object ID Entra (`oid`) de chaque ATC à son code ATC, et chaque
directeur à une ou plusieurs agences. Le rôle signé par Entra et le rôle attendu dans l'habilitation
doivent correspondre ; une incohérence bloque l'accès. L'adresse e-mail n'est pas utilisée comme clé
d'autorisation, car elle peut changer.

Le fichier `.streamlit/secrets.toml` est ignoré par Git. Seul le fichier d'exemple sans secret est
versionné. En production, placer ces valeurs dans le gestionnaire de secrets de l'hébergeur.
Streamlit active automatiquement ses protections CORS et XSRF lorsque cette configuration OIDC est
présente.

### Déploiement sur Streamlit Community Cloud

Le fichier `.env` est local et ignoré par Git : il n'est donc jamais copié dans le conteneur Cloud.
Dans l'application déployée, ouvrir **Settings → Secrets** et coller du TOML, par exemple pour le mode
mot de passe :

```toml
[app]
AUTH_MODE = "password"
AUTH_USERNAME = "collaborateur"
AUTH_PASSWORD = "choisir-un-mot-de-passe-long-et-unique"
ADMIN_USERNAME = "administrateur"
ADMIN_PASSWORD = "choisir-un-autre-mot-de-passe-long-et-unique"
AZURE_MAPS_URI = "https://atlas.microsoft.com"
AZURE_MAPS_SUBSCRIPTION_KEY = "votre-cle-azure-maps"
MAP_RENDERER = "pydeck"
```

Pour Entra ID, utiliser `AUTH_MODE = "entra"` dans `[app]`, puis ajouter les sections `[auth]` et
`[auth.microsoft]` fournies dans `.streamlit/secrets.toml.example`. Enregistrer les secrets et
redémarrer l'application. Le code accepte également les mêmes noms au niveau racine des secrets,
mais la section `[app]` est recommandée pour les regrouper.

## Lancement

```powershell
streamlit run app.py
```

L'import est réservé à l'administrateur. Le portefeuille actif est stocké par défaut dans
`.cache/opti_route.sqlite3`. Le classeur original n'est pas conservé. Sur un hébergement dont le
système de fichiers est éphémère, notamment Streamlit Community Cloud, cette base peut disparaître
au redémarrage ou au redéploiement.

Sur Azure Container Apps, activer le backend Blob persistant :

```dotenv
APP_STORAGE_BACKEND=azure_blob
AZURE_STORAGE_ACCOUNT_URL=https://nomducompte.blob.core.windows.net
AZURE_STORAGE_CONTAINER=opti-route-private
```

Attribuer à l'identité managée de la Container App le rôle **Storage Blob Data Contributor**, limité
au conteneur ou au compte utilisé. Ne pas fournir de clé de stockage ni de connection string en
production. Le conteneur privé doit être créé par l'infrastructure avant le démarrage. Le backend
enregistre séparément le portefeuille normalisé, les paramètres et chaque habilitation. Activer sur
le compte Storage le versioning, le soft-delete des blobs et des conteneurs, ainsi qu'une règle de
cycle de vie. `AZURE_STORAGE_CONNECTION_STRING` est uniquement prévu pour Azurite ou un test local.

Le calcul des routes reste effectué par Azure Maps. Le fond interactif Streamlit utilise PyDeck par défaut afin de ne pas exposer la clé au navigateur et de ne pas dépendre des règles CORS. Le contrôle Web Azure peut être réactivé après configuration de l'origine Streamlit dans Azure Maps :

```dotenv
MAP_RENDERER=azure
```

## Colonnes clients

Les intitulés sont libres. L'application tente de reconnaître automatiquement les champs suivants,
puis permet à l'administrateur de corriger la correspondance :

| Champ interne | Exemples reconnus |
|---|---|
| Code client | `Code client`, `ID client`, `Compte` |
| Nom | `Client`, `Raison sociale`, `Nom compte` |
| Code ATC | `Code ATC`, `Matricule ATC`, `Code commercial` |
| Commercial | `Commercial`, `Vendeur`, `Responsable commercial` |
| Agence | `Agence`, `Nom agence`, `Agence de référence` |
| Adresse agence | `Adresse agence`, `Adresse du site` |
| Adresse | `Adresse`, `Rue`, `Adresse 1` |
| Code postal | `Code postal`, `CP`, `ZIP` |
| Ville | `Ville`, `Commune`, `Localité` |
| Pays | `Pays`, `Country` |
| Coordonnées | `Latitude` / `Longitude`, `Lat` / `Lon` |

Une colonne d'adresse suffit et le code ainsi que le nom du client peuvent être générés. En revanche,
le commercial est désormais obligatoire pour permettre le filtrage utilisateur. Le code ATC est
utilisé pour les habilitations ; pour assurer la compatibilité avec les anciens fichiers, le nom du
commercial est utilisé comme code de repli lorsqu'aucune colonne dédiée n'est fournie. Les coordonnées
sont facultatives : les lignes qui n'en possèdent pas sont géocodées via Azure Maps et mises en cache
dans `.cache/geocoding.sqlite3`.

## Tests

```powershell
pytest
ruff check .
```

## Sécurité

Le fichier `.env`, `.streamlit/secrets.toml`, le cache et les portefeuilles placés dans `data/` sont
ignorés par Git. Les imports sont limités en taille, les archives Office anormales sont refusées et
seules les données normalisées sont enregistrées avec des requêtes SQL paramétrées. Les valeurs sont
neutralisées dans les fonctions d'export tableur lorsqu'elles pourraient être interprétées comme des
formules. Pour une mise en production, préférer Entra ID et un stockage Azure chiffré et persistant.
