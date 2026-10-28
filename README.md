# Opti Route Com

Application Streamlit interne pour préparer et optimiser des tournées commerciales à partir d'un portefeuille clients.

Le guide de déploiement, d'exploitation et d'architecture Azure (Container Apps, identités,
Key Vault, Blob Storage, Entra et GitHub Actions) est disponible dans
[deployment/AZURE.md](deployment/AZURE.md). La procédure récurrente d'export des membres Entra et
de provisionnement des périmètres est dans [deployment/EXPORT_ENTRA.md](deployment/EXPORT_ENTRA.md).

## Fonctionnalités du MVP

- import administrateur d'un portefeuille aux formats CSV, XLS, XLSX, XLSM ou XLSB ;
- détection automatique des colonnes et correspondance manuelle si leurs noms varient ;
- choix de la feuille et de la ligne d'en-tête ;
- séparation des rôles administrateur et utilisateur ;
- stockage du portefeuille normalisé dans SQLite en local ou Azure Blob en production, sans conservation du classeur brut ;
- choix obligatoire du commercial ;
- parcours de préparation en trois étapes : portefeuille, entreprise de départ et rendez-vous ajoutés, puis revue ;
- choix utilisateur du nombre total de visites hors départ, entre le nombre de rendez-vous ajoutés et le maximum administrateur ;
- sélection ou désélection individuelle des adresses avant le calcul ;
- ajout d'un ou plusieurs rendez-vous déjà planifiés : ils sont inclus même hors rayon, complétés au moindre détour le long de leur trajet et conservés lors d'un recalcul ;
- retrait des seules visites facultatives depuis le résultat avec recalcul complet de la tournée ;
- départ depuis une entreprise existante, affiché à l'ordre 0 ;
- fin de tournée à la dernière visite, sans trajet de retour ;
- géocodage gratuit des adresses françaises par l'IGN / Base Adresse Nationale, avec cache SQLite ;
- présélection des clients par rayon géographique ;
- matrice routière Azure Maps et optimisation OR-Tools ;
- affichage Azure Maps, ordre des visites et indicateurs ;
- noms des entreprises affichés directement à côté des points sur la carte ;
- calque facultatif pour chaque utilisateur : entreprises non retenues en jaune dans le rayon administrateur de départ et en vert hors rayon, sans géocodage additionnel ;
- export PDF avec capture cartographique numérotée, ainsi que partage vers Google Maps ;
- neutralisation des cellules pouvant être interprétées comme des formules dans les exports ;
- noms d'exports horodatés pour éviter les doublons ;
- authentification Microsoft Entra ID ou identifiant/mot de passe générique ;
- mode d'estimation local lorsque la clé Azure Maps n'est pas configurée.

### Règle de complétion d'une tournée avec rendez-vous forcé

Le nombre demandé correspond aux visites affichées après le point bleu **Départ** : ce départ n'est
pas compté. Les rendez-vous planifiés sont toujours retenus, même hors du rayon administrateur.
L'application prend d'abord les entreprises sélectionnées dans ce rayon. Si un rendez-vous forcé est
hors rayon et que le total demandé n'est pas atteint, elle complète avec les entreprises hors rayon
qui ajoutent le moins de détour à la colonne vertébrale `départ → rendez-vous planifiés`.

Cette présélection est faite à partir de la distance géographique pour rester performante sur un
grand portefeuille ; l'itinéraire final est ensuite optimisé avec la matrice routière Azure Maps ou
son estimation locale. Un avertissement explicite est affiché si le portefeuille sélectionné ne
contient pas assez d'entreprises pour atteindre le total demandé.

## Installation

Une version Python 3.x comprise entre 3.11 inclus et 4.0 exclu est nécessaire.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Azure Maps reste nécessaire au calcul précis des itinéraires. Renseigner sa configuration dans `.env` :

```dotenv
AZURE_MAPS_URI=https://atlas.microsoft.com
AZURE_MAPS_SUBSCRIPTION_KEY=remplacer_par_la_cle
```

`AZURE_MAPS_ENDPOINT` peut être utilisé à la place de `AZURE_MAPS_URI`. L'application emploie les API Azure Maps `2025-01-01` pour la matrice et le tracé routier. Le géocodage des adresses françaises utilise le service public IGN / Base Adresse Nationale, sans clé API.

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
le départ et d'éventuels rendez-vous déjà planifiés.

Pour une recette avant Entra ID, des comptes locaux supplémentaires peuvent être configurés avec
`LOCAL_TEST_ACCOUNTS_JSON`. Ils reproduisent les rôles et périmètres finaux sans être enregistrés
dans la base applicative. Exemple (à placer uniquement dans `.env` ou dans les secrets de
l'hébergeur) :

```dotenv
LOCAL_TEST_ACCOUNTS_JSON=[{"username":"test-atc","password":"mot-de-passe-long","display_name":"Test ATC","role":"atc","atc_code":"31-02-004"},{"username":"test-rag","password":"autre-mot-de-passe-long","display_name":"Test RAG","role":"rag","rag_email":"c.vernay@litt.fr"},{"username":"test-dr","password":"un-autre-mot-de-passe-long","display_name":"Test DR","role":"dr","ugs":["10","20"]}]
```

Ces comptes sont temporaires : les retirer des secrets dès le basculement vers `AUTH_MODE=entra`.

### Microsoft Entra ID

1. Dans Entra ID, créer une inscription d'application de type Web, limitée au tenant de l'entreprise.
2. Ajouter l'URI de redirection `http://localhost:8501/oauth2callback` en local ou
   `https://adresse-de-production/oauth2callback` en production.
3. Créer un secret client.
4. Copier `.streamlit/secrets.toml.example` vers `.streamlit/secrets.toml` et renseigner le tenant,
   l'identifiant d'application, le secret client, l'URI de redirection et une valeur aléatoire longue
   pour `cookie_secret`.
5. Définir `AUTH_MODE=entra` dans `.env`, puis redémarrer Streamlit.

L'autorisation utilise quatre **App Roles Entra**, présents dans le claim signé `roles` du jeton
et alignés sur les groupes Intra :

- `D-FR-APP-OPTIROUTE-ATC` : accès au seul code ATC configuré dans l'application ;
- `D-FR-APP-OPTIROUTE-RAG` : accès aux portefeuilles rattachés à son e-mail N+1 ;
- `D-FR-APP-OPTIROUTE-DR` : accès à toutes les agences des UG configurées ;
- `D-FR-APP-OPTIROUTE-ADMIN` : accès global, import du portefeuille, habilitations et paramètres.

Le fichier [deployment/entra-app-roles.json](deployment/entra-app-roles.json) contient les quatre
définitions prêtes à reporter dans le manifeste de l'inscription d'application. Dans **Enterprise
applications → Utilisateurs et groupes**, attribuer exactement un de ces rôles à chaque utilisateur
ou groupe autorisé. Configurer ensuite :

```dotenv
ENTRA_TENANT_ID=identifiant-du-tenant
ENTRA_ROLE_ATC=D-FR-APP-OPTIROUTE-ATC
ENTRA_ROLE_RAG=D-FR-APP-OPTIROUTE-RAG
ENTRA_ROLE_DR=D-FR-APP-OPTIROUTE-DR
ENTRA_ROLE_ADMIN=D-FR-APP-OPTIROUTE-ADMIN
```

L'application refuse par défaut un jeton sans rôle reconnu, provenant d'un autre tenant ou contenant
plusieurs rôles Opti Route. Chaque compte doit donc recevoir exactement un rôle.
Les anciennes listes `ADMIN_EMAILS` ne donnent plus aucun droit en mode Entra.

Après la première connexion de l'administrateur, provisionner de préférence les habilitations par
l'import du CSV Entra décrit plus bas et dans [deployment/EXPORT_ENTRA.md](deployment/EXPORT_ENTRA.md).
L'onglet **Administration → Habilitations** reste utile pour corriger ponctuellement un compte : il
associe l'Object ID Entra (`oid`) de chaque ATC à son code ATC, chaque RAG à son e-mail N+1 et chaque
DR à une ou plusieurs UG. Le rôle signé par Entra et le rôle attendu dans l'habilitation doivent
correspondre ; une incohérence bloque l'accès. L'adresse e-mail sert à calculer le périmètre, mais
l'Object ID reste la clé d'autorisation stable.

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
IGN_GEOCODING_ENDPOINT = "https://data.geopf.fr/geocodage/search"
IGN_GEOCODING_MAX_REQUESTS_PER_SECOND = "40"
GEOCODING_AZURE_FALLBACK = "true"
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
AZURE_CLIENT_ID=Client-ID-de-id-optiroute
```

Attribuer à l'identité managée de la Container App le rôle **Storage Blob Data Contributor**, limité
au conteneur ou au compte utilisé. Ne pas fournir de clé de stockage ni de connection string en
production. Le conteneur privé doit être créé par l'infrastructure avant le démarrage. Le backend
enregistre séparément le portefeuille normalisé, les paramètres et chaque habilitation. Activer sur
le compte Storage le versioning, le soft-delete des blobs et des conteneurs, ainsi qu'une règle de
cycle de vie. `AZURE_CLIENT_ID` sélectionne explicitement l'identité runtime attachée à la Container
App ; ce n'est ni le Client ID OIDC des collaborateurs, ni celui de GitHub Actions.
`AZURE_STORAGE_CONNECTION_STRING` est uniquement prévu pour Azurite ou un test local.

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
| UG | `Code UG`, `UG`, `Unité de gestion` |
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
sont facultatives : au moment où l'administrateur enregistre un import, les lignes qui n'en possèdent
pas sont géocodées par le service public IGN / Base Adresse Nationale et mises en cache dans
`.cache/geocoding.sqlite3`. L'application se limite à 40 appels par seconde, sous la limite publique
de 50 appels/IP/seconde. Le portefeuille enrichi est alors stocké ; les tournées et le calque ne
relancent donc pas de géocodage pour ces clients. En cas d'échec IGN/BAN, Azure Maps est sollicité
en repli lorsqu'il est configuré (`GEOCODING_AZURE_FALLBACK=true`) ; il n'est jamais appelé pour une
adresse déjà géocodée ou retrouvée dans le cache.

Lorsqu'une adresse reste introuvable après les deux recherches, l'administrateur peut la corriger
directement depuis le panneau d'import, puis relancer uniquement les adresses concernées. L'import
signale aussi les caractères pouvant indiquer un encodage endommagé, sans modifier automatiquement
les libellés : la correction doit être reportée dans le fichier source.

### Portefeuille final `Liste_client_def.xlsx`

La feuille **Listing client** est reconnue sans correspondance manuelle :

| Colonne source | Utilisation |
|---|---|
| `rai_soc` | nom du client |
| `RUE`, `cp`, `ville` | adresse a geocoder |
| `Nom`, `code`, `Email Nom (E)` | commercial, reference ATC complete et perimetre |
| `Code UG`, `code ATC` | informations de decomposition de la reference : UG et portefeuille |
| `N+1`, `Email N+1 (H)` | RAG et portefeuille de ses commerciaux |

Le N+1 et son e-mail peuvent etre renseignes une seule fois par commercial : ils sont
propages a toutes les lignes de son portefeuille a l'import. Dans **Administration >
Habilitations**, l'administrateur associe toujours l'Object ID Entra a un collaborateur,
Puis sélectionne son e-mail ATC ou RAG issu du portefeuille, ou les UG d'un DR. Le rôle signé par
Entra reste obligatoire ; l'e-mail importe sert a definir le perimetre, pas a authentifier
un utilisateur. Un ATC ne voit que les clients de son e-mail professionnel, un RAG
voit les clients des commerciaux rattachés à son e-mail N+1 et un DR toutes les agences
des UG qui lui sont attribuées. L'ancien filtrage par agence
reste disponible pour les portefeuilles historiques qui ne contiennent pas ces e-mails.

Pour éviter cette saisie compte par compte, l'administrateur peut téléverser, dans le panneau
d'import du portefeuille, le CSV généré par `scripts/export_entra_group_members.ps1`. Si le
classeur contient la feuille **Utilisateurs** de `Liste_client_def.xlsx`, Opti Route rapproche les
Object IDs et e-mails Entra avec le portefeuille et affiche une prévisualisation avant écriture :

- ATC : e-mail professionnel et portefeuille ATC ;
- RAG : e-mail N+1 ;
- DR : `Code Region` de la feuille **Utilisateurs**, par exemple `20-24-31-34` ;
- ADMIN : aucun profil métier, car l'accès est global.

Le bouton d'enregistrement crée ou met à jour uniquement les habilitations validées. Les comptes
sans correspondance et les UG absentes sont signalés, et aucune habilitation existante n'est
supprimée automatiquement. Cette opération n'écrit jamais dans Microsoft Entra ID.

La procédure Cloud Shell, les consentements Graph nécessaires, le format CSV et les contrôles de
sécurité sont détaillés dans [deployment/EXPORT_ENTRA.md](deployment/EXPORT_ENTRA.md).

Pour `Liste_client_def.xlsx`, la reference utilisee par Opti Route est la colonne `code`
complete : par exemple `10-03-82` pour Yvon RONDEL. `10` est l'UG, `03` l'agence et
`82` le portefeuille ; le code agence conserve dans l'application est donc `10-03`.
Le seul `code ATC` (`82`) n'est pas utilise comme identifiant, car il peut etre present
dans plusieurs agences.

Le calque des possibilites est visible par tous, mais uniquement sur le portefeuille
auquel la session a deja droit. Son activation ne geocode aucune nouvelle adresse : les
entreprises sans coordonnees sont simplement absentes du calque. L'import produit aussi
un bouton de telechargement d'une copie Excel enrichie des colonnes `Latitude` et `Longitude`.
Cette copie doit remplacer le fichier source pour conserver les coordonnees dans le temps ;
un navigateur ne peut pas modifier silencieusement le fichier d'origine sur le poste de
l'administrateur. Une adresse deja presente dans le cache ne genere pas un nouvel appel au geocodeur.

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
