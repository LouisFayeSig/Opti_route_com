# Déploiement Azure sécurisé

Ce document est la référence d'exploitation de l'environnement de production Opti Route.
Il décrit l'architecture réellement retenue dans le groupe de ressources `FR-RG-OPTIROUTE`.
Les secrets, identifiants de locataire, Object IDs des utilisateurs et valeurs de clés n'y sont
volontairement jamais inscrits.

Pour l'export des membres Entra et le provisionnement en masse des périmètres métier, suivre
[EXPORT_ENTRA.md](EXPORT_ENTRA.md). Le présent document couvre l'infrastructure ; cet autre guide
couvre l'opération d'administration fonctionnelle.

## Architecture cible et responsabilités

```text
Navigateur collaborateur
  │ HTTPS / OpenID Connect
  ▼
Microsoft Entra ID ── App Roles ──> Application Container App app-optiroute
                                      │ id-optiroute (identité runtime)
                                      ├─ AcrPull ───────────────> CrOptiroute (image privée)
                                      ├─ Key Vault Secrets User ─> CoffreOptiroute (secrets.toml)
                                      └─ Storage Blob Data Contributor
                                                                  │
                                                                  ▼
                                                     stockageoptiroute/opti-route-private

GitHub Actions ─ OIDC ─> id-gh-optiroute-deploy ── AcrPush / Contributor ciblé ─> image + révision
```

| Élément | Nom retenu | Rôle dans l'architecture | Données ou secret à y placer |
| --- | --- | --- | --- |
| Groupe de ressources | `FR-RG-OPTIROUTE` | Limite administrative et de coût, West Europe | Aucun secret |
| Environnement ACA | `env-optiroute` | Héberge et isole le cycle de vie de Container Apps | Réseau et logs partagés |
| Application Container Apps | `app-optiroute` | Exécute Streamlit, expose le FQDN HTTPS | Configuration, références de secrets, révisions |
| Container Registry | `CrOptiroute` / `croptiroute.azurecr.io` | Conserve les images Docker privées | Images `opti-route:vX.Y.Z` et `sha-...` |
| Stockage | `stockageoptiroute` | État persistant de l'application | Conteneur privé `opti-route-private` |
| Key Vault | `CoffreOptiroute` | Source des secrets de production | Secret `streamlit-secrets`, clé Azure Maps si utilisée |
| Log Analytics | `log-analytics-optiroute` | Journaux et diagnostic | Logs, jamais des secrets |
| Identité runtime | `id-optiroute` | Identité de l'application en exécution | Aucune clé : droits RBAC seulement |
| Identité CI/CD | `id-gh-optiroute-deploy` | Identité de GitHub Actions | Fédération OIDC, aucune clé permanente |
| App registration / enterprise app | `Opti Route` | Connexion collaborateur et App Roles | Client ID, secret OIDC dans Key Vault |

### Deux plans d'autorisation à ne pas mélanger

1. **Azure RBAC** autorise une identité technique à lire une image, un secret ou un blob.
   Il ne donne jamais à un collaborateur le droit de voir un portefeuille.
2. **Microsoft Entra App Roles + habilitations Opti Route** autorisent une personne à se connecter,
   puis calculent son périmètre métier. Un rôle Entra valide ne suffit donc pas pour un ATC, RAG ou
   DR : son profil interne doit aussi exister dans Blob Storage. Le rôle `ADMIN`, lui, a accès global.

## 1. Ressources minimales

- Azure Container Registry Basic ;
- Azure Container Apps en consommation, avec l'identité managée utilisateur `id-optiroute` ;
- un compte StorageV2 et un conteneur Blob privé `opti-route-private` ;
- une inscription d'application Microsoft Entra ID mono-tenant ;
- Azure Key Vault pour le secret OIDC et le secret de cookie ;
- Azure Maps.

PostgreSQL n'est pas nécessaire pour ce volume fonctionnel. Le portefeuille, les paramètres et les
habilitations sont de petits documents administrés par peu d'utilisateurs. Le backend `azure_blob`
les conserve séparément et permet d'utiliser le versioning et le soft-delete natifs de Blob Storage.

## 2. Inscription Entra ID

1. Créer une inscription d'application **Accounts in this organizational directory only**.
2. Ajouter une plateforme Web et l'URI
   `https://<fqdn-container-app>/oauth2callback`.
3. Dans le manifeste, remplacer la propriété `appRoles` par le contenu de
   `deployment/entra-app-roles.json`.
4. Dans l'Enterprise Application, activer **Assignment required**.
5. Attribuer exactement un rôle par groupe Intra synchronisé dans Entra :
   `D-FR-APP-OPTIROUTE-ATC`, `D-FR-APP-OPTIROUTE-RAG`,
   `D-FR-APP-OPTIROUTE-DR` ou `D-FR-APP-OPTIROUTE-ADMIN`.
6. Ne donner aucune permission Microsoft Graph à l'application : elle n'en a pas besoin.
7. Créer un secret client, le placer dans Key Vault et prévoir sa rotation.

Le premier administrateur doit recevoir `D-FR-APP-OPTIROUTE-ADMIN` dans Entra. Ce rôle ne nécessite aucune
ligne d'habilitation dans l'application et permet donc d'initialiser les autres comptes.

### Contrôle dans le portail Entra

Dans **Microsoft Entra ID > Inscriptions d'applications > Opti Route > Rôles d'application**, les
quatre valeurs techniques doivent correspondre **caractère pour caractère** à celles configurées
dans le secret Streamlit. Dans **Applications d'entreprise > Opti Route > Utilisateurs et groupes**,
les quatre groupes existants doivent être présents, chacun avec son rôle homonyme. Il ne faut pas
recréer les utilisateurs dans Entra lorsque les groupes Active Directory ont déjà été synchronisés :
les mêmes objets Entra sont simplement membres des groupes synchronisés.

Les affectations de rôles aux groupes émettent la claim signée `roles` au moment de la connexion.
Un membre indirect via un groupe imbriqué n'est pas une alternative fiable : utiliser des membres
directs dans les quatre groupes Opti Route. L'option **Affectation obligatoire ?** de l'application
d'entreprise doit rester activée afin qu'un compte non affecté ne puisse pas accéder à l'application.

L'inscription `Opti Route` et les deux identités managées Azure sont trois objets différents :

| Objet | Utilisé par | Identifiant à utiliser |
| --- | --- | --- |
| Inscription d'application `Opti Route` | Navigateur des collaborateurs / OIDC Streamlit | Son **Application (client) ID** dans `[auth.microsoft]` |
| `id-optiroute` | Code exécuté dans le conteneur | Son **Client ID** dans `AZURE_CLIENT_ID` et son Principal ID pour RBAC |
| `id-gh-optiroute-deploy` | GitHub Actions | Son **Client ID** dans le secret GitHub `AZURE_CLIENT_ID` |

L'application Opti Route n'appelle pas Microsoft Graph pour décider le périmètre métier et ne
nécessite pas d'autorisation Graph applicative. L'export ponctuel décrit dans
[EXPORT_ENTRA.md](EXPORT_ENTRA.md) est un client Graph PowerShell séparé, utilisant son propre
consentement délégué de lecture.

## 3. Configuration OIDC Streamlit

Référencer dans Container Apps un secret nommé `streamlit-secrets`, fourni depuis Key Vault. Dans
le volume secret, définir son chemin de fichier à `secrets.toml`, puis monter le volume au chemin
`/app/.streamlit`. Streamlit lit alors directement le fichier
`/app/.streamlit/secrets.toml`. Le contenu attendu est :

```toml
[app]
AUTH_MODE = "entra"
ENTRA_TENANT_ID = "<tenant-id>"
ENTRA_ROLE_ATC = "D-FR-APP-OPTIROUTE-ATC"
ENTRA_ROLE_RAG = "D-FR-APP-OPTIROUTE-RAG"
ENTRA_ROLE_DR = "D-FR-APP-OPTIROUTE-DR"
ENTRA_ROLE_ADMIN = "D-FR-APP-OPTIROUTE-ADMIN"

[auth]
redirect_uri = "https://<fqdn-container-app>/oauth2callback"
cookie_secret = "<valeur-aléatoire-longue>"

[auth.microsoft]
client_id = "<application-client-id>"
client_secret = "<secret-client-entra>"
server_metadata_url = "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration"
```

Le fichier est fourni à l'exécution et ne doit jamais être intégré à l'image Docker ni au dépôt.
L'identité utilisée par la référence Key Vault doit recevoir **Key Vault Secrets User** sur le coffre,
sans rôle d'administration du coffre.

### Chaîne Key Vault → Container Apps → Streamlit

Dans `CoffreOptiroute`, enregistrer un unique secret texte multilignes nommé
`streamlit-secrets`. Le contenu est du TOML strict : chaque instruction doit être séparée par un
retour à la ligne. Un collage qui transforme le contenu en une seule ligne produit une erreur
`TOMLDecodeError` au démarrage. `client_secret` est la **valeur** du secret Entra, jamais son
identifiant ; `cookie_secret` est une valeur aléatoire longue, stable dans le temps. Sa rotation
déconnecte toutes les sessions existantes.

Dans `app-optiroute > Paramètres > Secrets`, créer ou mettre à jour le secret Container Apps
`streamlit-secrets` comme référence à Key Vault. Dans **Volumes**, sélectionner ce secret et le
publier comme fichier `secrets.toml`. Dans **Conteneurs > Montages de volume**, monter le volume
sur `/app/.streamlit` sans sous-chemin. Le résultat attendu dans la réplique est exactement :

```text
/app/.streamlit/secrets.toml
```

Après toute modification de `streamlit-secrets`, créer une nouvelle révision ou redémarrer la
révision active. La synchronisation Key Vault indique que Container Apps a lu la nouvelle valeur,
mais elle ne redémarre pas à elle seule le processus Streamlit. Vérifier sans afficher les valeurs :

```sh
python -c "import tomllib; p='/app/.streamlit/secrets.toml'; d=tomllib.load(open(p,'rb')); print(list(d)); print(d.get('app',{}).get('AUTH_MODE'))"
```

Le résultat attendu est une liste comprenant `app` et `auth`, puis `entra`.

## 4. Stockage par identité managée

Configurer les variables non secrètes suivantes sur la Container App :

```text
APP_STORAGE_BACKEND=azure_blob
AZURE_STORAGE_ACCOUNT_URL=https://<compte>.blob.core.windows.net
AZURE_STORAGE_CONTAINER=opti-route-private
AZURE_CLIENT_ID=<Client ID de id-optiroute>
AZURE_MAPS_URI=https://atlas.microsoft.com
```

Attribuer à l'identité managée le rôle **Storage Blob Data Contributor**, avec la portée la plus
étroite possible. Aucun rôle Storage n'est nécessaire pour les utilisateurs finaux : toutes les
lectures passent par le contrôle d'accès de l'application.

`AZURE_CLIENT_ID` ne contient pas un secret. Il force `DefaultAzureCredential` à sélectionner
`id-optiroute` parmi les éventuelles identités attachées au conteneur. La valeur est le Client ID
de l'identité runtime, et non le Client ID de l'inscription `Opti Route` ni celui de l'identité
GitHub. Sans cette identité attachée et ce rôle Blob, le premier accès peut rester bloqué sur
`_initialize_app_store` avec une erreur `DefaultAzureCredential`.

### Configuration exacte de l'identité runtime

1. Dans `app-optiroute > Identité`, attacher l'identité affectée par l'utilisateur
   `id-optiroute`. Sa création dans Azure ne suffit pas : elle doit être attachée à la Container App.
2. Dans `CrOptiroute > Contrôle d'accès (IAM)`, attribuer **AcrPull** à son *Principal ID*.
   Dans `app-optiroute > Conteneurs > Registre`, sélectionner l'authentification
   **Identité managée**, cette identité, puis le registre `croptiroute.azurecr.io`.
3. Dans `stockageoptiroute`, attribuer **Storage Blob Data Contributor** à son Principal ID,
   idéalement sur le conteneur `opti-route-private` (sinon sur le compte Storage).
4. Dans `CoffreOptiroute`, attribuer **Key Vault Secrets User** à cette identité, avec le modèle
   d'autorisation Azure RBAC. Ce droit permet de résoudre la référence Key Vault, pas de créer
   des secrets.
5. Dans les variables d'environnement de la Container App, enregistrer les cinq valeurs du bloc
   ci-dessus, puis créer une révision.

L'application persiste les objets suivants dans le conteneur, tous privés :

```text
state/portfolio.json                    portefeuille normalisé
state/settings.json                     réglages administratifs
access-profiles/<sha256-object-id>.json habilitation interne d'un collaborateur
```

Le classeur Excel d'origine et le CSV Entra ne sont pas conservés par l'application : importer à
nouveau un fichier ne sert que lorsqu'une mise à jour du portefeuille ou des habilitations est voulue.

Sur le compte Storage :

- désactiver l'accès public des blobs ;
- activer le versioning ;
- activer le soft-delete des blobs et conteneurs pendant 30 jours ;
- supprimer les anciennes versions après 90 jours avec une règle de cycle de vie ;
- interdire l'authentification par clé partagée lorsque tous les outils d'exploitation utilisent
  Entra ID.

Le compte Storage peut conserver son **accès réseau public** tant qu'il n'y a ni VNet ni Private
Endpoint, mais l'accès **anonyme aux blobs** doit rester désactivé. « Public network access » et
« accès public/anonymous aux blobs » sont deux paramètres différents : le premier autorise un client
authentifié à joindre l'endpoint, le second publierait potentiellement les données sans identité.

## 5. Variables et secrets

Le secret client OIDC, le `cookie_secret` et une éventuelle clé Azure Maps sont des secrets Key
Vault. L'URL Storage, le tenant ID, les noms des rôles et le nom du conteneur ne sont pas secrets.
`AZURE_STORAGE_CONNECTION_STRING` ne doit pas être défini en production.

| Valeur | Emplacement de référence | Secret ? | Remarque |
| --- | --- | --- | --- |
| `streamlit-secrets` | Key Vault puis secret/volume Container Apps | Oui | TOML complet, monté comme fichier |
| `client_secret` OIDC | Dans `streamlit-secrets` | Oui | Renouveler avant expiration de l'inscription Entra |
| `cookie_secret` | Dans `streamlit-secrets` | Oui | Long, aléatoire, stable hors rotation planifiée |
| Clé Azure Maps | Key Vault ou secret ACA référencé comme variable | Oui | Ne jamais la placer dans Git ni dans le navigateur |
| FQDN, tenant ID, noms de rôles, URL Blob, nom de conteneur | Variables Container Apps | Non | Peuvent être visibles aux opérateurs de l'app |
| Client IDs des identités et de l'app registration | Variables / GitHub secrets par précaution | Non | Un Client ID n'est pas une clé d'accès |

Pour modifier le secret Key Vault, l'opérateur humain a besoin de **Key Vault Secrets Officer** sur
`CoffreOptiroute` (si le coffre utilise Azure RBAC). Il ne faut pas confondre ce rôle de gestion
avec `Key Vault Secrets User`, réservé à la lecture runtime. Éviter les attributions `Owner`,
`Key Vault Administrator` ou `Contributor` larges si un rôle ciblé suffit.

## 6. Initialisation des habilitations

1. L'administrateur se connecte avec son rôle Entra `D-FR-APP-OPTIROUTE-ADMIN`.
2. Il importe le portefeuille contenant `Code ATC`, `Commercial`, `Agence` et `Code UG`.
3. Dans **Administration → Habilitations**, il associe :
   - l'Object ID Entra d'un ATC à un code ATC ;
   - l'Object ID Entra d'un RAG à son e-mail N+1 ;
   - l'Object ID Entra d'un DR à une ou plusieurs UG.
4. L'utilisateur se reconnecte. Le rôle du jeton et le rôle attendu doivent être identiques.

Pour un premier chargement ou une mise à jour en masse, l'administrateur peut téléverser le CSV
produit par `scripts/export_entra_group_members.ps1` dans le panneau d'import du portefeuille.
Avec la feuille **Utilisateurs** de `Liste_client_def.xlsx`, l'application prépare une
prévisualisation des habilitations ATC, RAG et DR avant leur écriture. Les DR utilisent la colonne
`Code Region` (par exemple `20-24-31-34`). Les comptes non rapprochés et les UG absentes restent
en anomalie ; aucune habilitation existante n'est supprimée automatiquement.

Retirer une habilitation bloque son périmètre au prochain rerun Streamlit. Retirer un App Role dans
Entra prend pleinement effet à l'expiration du jeton courant ou après déconnexion ; les politiques
Conditional Access du tenant peuvent réduire cette durée selon le niveau de risque souhaité.

### Procédure récurrente de gouvernance des accès

Exécuter la séquence suivante après une arrivée, un départ, un changement de fonction, une
modification d'UG ou une mise à jour du portefeuille :

1. L'équipe identité modifie l'appartenance du collaborateur dans **un seul** groupe Entra Opti Route
   et vérifie son affectation au bon App Role de l'application d'entreprise.
2. Un administrateur Opti Route exécute l'[export Entra](EXPORT_ENTRA.md), puis importe le CSV et
   le portefeuille mis à jour dans l'application.
3. Il lit les anomalies de rapprochement et valide les créations ou mises à jour proposées.
4. Il teste le périmètre avec un compte représentatif du rôle concerné.
5. Pour un départ ou un retrait immédiat, retirer d'abord l'affectation Entra (blocage de connexion
   au renouvellement de session), puis supprimer le profil interne si la conservation du périmètre
   historique n'est plus nécessaire.

Ne pas saisir les membres de groupes un par un dans Opti Route si l'export fonctionne : le fichier
source `Utilisateurs` est la référence métier pour les e-mails et les régions DR, tandis qu'Entra est
la référence des Object IDs et du rôle de connexion. Ce rapprochement évite de maintenir deux listes
manuellement, tout en gardant une prévisualisation humaine avant écriture.

## 7. Image et exploitation

Le `Dockerfile` exécute Streamlit avec un utilisateur Linux non-root. Pour une première mise en
production économique, utiliser 0,5 vCPU, 1 Gio de mémoire, `minReplicas=0` et `maxReplicas=1`.
Augmenter ensuite le maximum après un test de charge et de reconnexion WebSocket.

La sonde HTTP est disponible sur `/_stcore/health`. Les logs ne doivent contenir ni adresses clients,
ni claims complets, ni secrets. Conserver les logs applicatifs 30 jours et les opérations
administratives Storage selon la politique d'audit de l'entreprise.

### Paramètres Container Apps à maintenir

| Paramètre | Valeur / décision | Pourquoi |
| --- | --- | --- |
| Ingress | Externe, HTTPS géré par Container Apps | Les collaborateurs atteignent le FQDN public de l'application |
| Port cible | `8501` | Port d'écoute de Streamlit dans l'image |
| Sondes startup, readiness et liveness | HTTP, port `8501`, chemin `/_stcore/health` | Évite d'envoyer du trafic à Streamlit avant qu'il soit prêt |
| Révisions | Mode *Single* pour une exploitation simple | La dernière révision saine prend 100 % du trafic ; rollback facile |
| Échelle initiale | `minReplicas=0`, `maxReplicas=1` pour le démarrage économique | Ajuster après mesure de la charge et des sessions WebSocket |
| Image | Tag versionné `vX.Y.Z`, jamais seulement `latest` | Permet l'audit et le retour arrière |

Dans **Révisions et réplicas**, ne basculer 100 % du trafic que vers une révision dont l'état est
*Exécution* et dont `https://<fqdn>/_stcore/health` répond. En mode Single, la mise à jour désactive
normalement les anciennes révisions ; en cas de révision non saine, laisser la dernière saine avec le
trafic, corriger la configuration ou l'image puis publier une nouvelle révision. Une sonde
`connection refused` signifie que le processus n'écoute pas encore sur le port cible ou s'est arrêté ;
ce n'est pas une erreur d'authentification Entra.

Pour voir les journaux dans un portail français, utiliser `app-optiroute > Révisions et réplicas`,
ouvrir la révision active puis **Afficher les journaux**. Le flux en direct peut être indisponible
selon les droits ; les événements de réplique et le workspace `log-analytics-optiroute` restent les
sources de diagnostic. Ne copier dans un ticket que le message d'erreur utile, jamais le contenu des
secrets, des jetons ou du CSV Entra.

### Exploitation et retour arrière

Chaque publication GitHub produit une image `vX.Y.Z` et une image `sha-<commit>`. Pour revenir en
arrière, sélectionner une image de version connue dans **Conteneurs**, enregistrer une nouvelle
révision et confirmer sa sonde de santé. Ne jamais retagger une ancienne image avec le même tag de
version : un tag doit rester immuable. Les données Blob ne sont pas automatiquement rétablies lors
d'un rollback applicatif ; utiliser le versioning Storage pour une récupération de données distincte.

## 8. GitHub Actions (image et déploiement)

Le workflow `.github/workflows/deploy-container-app.yml` se déclenche à chaque tag Git commençant
par `v` (par exemple `v1.0.0`) ou manuellement. Il construit l'image depuis le `Dockerfile`, publie
deux tags immuables dans `croptiroute.azurecr.io` : le tag de version et `sha-<commit>`, puis met à
jour `app-optiroute` si celle-ci existe.

Le workflow ne contient aucun secret Azure. Créer dans **GitHub > Settings > Secrets and variables
> Actions** les trois secrets suivants, associés à une identité de déploiement distincte de
`id-optiroute` :

```text
AZURE_CLIENT_ID        = Client ID de l'identité GitHub de déploiement
AZURE_TENANT_ID        = Tenant ID Microsoft Entra
AZURE_SUBSCRIPTION_ID  = ID de l'abonnement SIG France
```

L'identité GitHub doit disposer de `AcrPush` sur `CrOptiroute` et de `Contributor` sur la seule
ressource Container App `app-optiroute`. Dans la ressource identité managée, créer une crédential
fédérée OIDC avec le scénario **GitHub Actions deploying Azure resources** et les valeurs :

```text
Organization = LouisFayeSig
Repository   = Opti_route_com
Entity type  = Environment
Environment  = production
Name         = github-production
```

Azure remplit automatiquement `issuer`, `audience` et le sujet
`repo:LouisFayeSig/Opti_route_com:environment:production`. Dans GitHub, protéger l'environnement
`production` avec un approbateur et limiter les déploiements à `main` et aux tags de release.

Ne pas donner `AcrPush` à l'identité runtime `id-optiroute` : elle conserve uniquement `AcrPull`,
`Storage Blob Data Contributor` et `Key Vault Secrets User`.

Le tag `v1.0.0` ayant été créé avant l'ajout du workflow, lancer le premier build par
**Actions > Build and deploy Opti Route > Run workflow**, avec `image_tag` et `source_ref` égaux
à `v1.0.0`. Cela construit exactement le commit de la release, même si `main` a évolué depuis.

### Chaîne CI/CD en pratique

1. Une correction est fusionnée dans `main` et testée localement.
2. Une release/tag Git de forme `vX.Y.Z` déclenche le workflow ou celui-ci est lancé manuellement
   avec `image_tag` et `source_ref`.
3. GitHub émet un jeton OIDC éphémère pour l'environnement GitHub `production`.
4. Azure accepte ce jeton uniquement si la fédération de `id-gh-optiroute-deploy` correspond au sujet
   `repo:LouisFayeSig/Opti_route_com:environment:production`.
5. L'identité publie l'image dans ACR, puis met à jour uniquement `app-optiroute`.
6. Le workflow attend la sonde Streamlit `/_stcore/health`. Une erreur de cette étape signifie que
   l'image est publiée mais que la révision doit être investiguée avant diffusion aux utilisateurs.

Les trois secrets GitHub ne sont pas des mots de passe Azure : `AZURE_CLIENT_ID` est le Client ID de
`id-gh-optiroute-deploy`, `AZURE_TENANT_ID` est l'ID du locataire et `AZURE_SUBSCRIPTION_ID` est l'ID
de l'abonnement. L'absence de `AZURE_CLIENT_SECRET` est volontaire : OIDC évite une clé Azure durable
dans GitHub. Les secrets de runtime (OIDC Streamlit, cookie, Maps) ne passent jamais par ce workflow.

## 9. Checklist opérateur avant la Container App

### Stockage `stockageoptiroute`

- Réseau : conserver `Public network access = Enabled` tant qu'aucun VNet / Private Endpoint n'est
  en place ;
- Configuration : `Allow Blob anonymous access = Disabled`, HTTPS obligatoire et TLS 1.2 minimum ;
- Data protection : activer le versioning Blob, le soft-delete Blob et conteneur pendant 30 jours ;
- créer le conteneur privé `opti-route-private` ;
- n'utiliser ni clé de compte, ni `AZURE_STORAGE_CONNECTION_STRING` en production.

### Données métier et habilitations

Avant le premier import, préparer les données suivantes :

| Rôle | Donnée à saisir dans l'administration Opti Route |
| --- | --- |
| ATC | Object ID Entra et Code ATC |
| RAG | Object ID Entra et adresse e-mail RAG |
| DR | Object ID Entra et un ou plusieurs Code UG |

Le portefeuille importé doit posséder une colonne `Code UG`. Les Object IDs sont ceux des comptes
utilisateurs Entra, et non les Client IDs des applications ou identités managées.

Le script `scripts/export_entra_group_members.ps1` extrait automatiquement les membres directs des
quatre groupes Opti Route, avec leur Object ID, dans `exports/opti-route-entraid-users.csv`. Il est
strictement en lecture et ne modifie aucun groupe. Le dossier `exports/` est ignoré par Git car le
CSV contient des données personnelles.

Avant le premier lancement, installer les modules Graph (sans privilège administrateur local) :

```powershell
Install-Module Microsoft.Graph.Authentication -Scope CurrentUser
Install-Module Microsoft.Graph.Groups -Scope CurrentUser
```

Puis lancer :

```powershell
.\scripts\export_entra_group_members.ps1
```

Le consentement Entra en lecture aux scopes délégués `GroupMember.Read.All` et
`User.ReadBasic.All` peut devoir être accordé par l'équipe identité. Le script signale les
utilisateurs présents dans plusieurs groupes de rôle, une configuration à corriger avant
production.

### Informations à relever pour IAM

Depuis la ressource `id-optiroute`, relever le `Principal ID`, `Client ID` et `Resource ID`.
Le premier sert aux attributions RBAC, le dernier sert à attacher l'identité à la Container App.
Depuis l'ACR, relever le chemin exact de l'image et son tag, par exemple
`croptiroute.azurecr.io/opti-route:v1.0.0`.

## 10. Matrice RBAC minimale

| Principal | Portée | Rôle minimal | Usage |
| --- | --- | --- | --- |
| `id-optiroute` | `CrOptiroute` | `AcrPull` | Télécharger l'image au démarrage |
| `id-optiroute` | Conteneur Blob `opti-route-private` | `Storage Blob Data Contributor` | Lire et écrire état, portefeuille et profils |
| `id-optiroute` | `CoffreOptiroute` | `Key Vault Secrets User` | Résoudre le secret monté dans l'application |
| `id-gh-optiroute-deploy` | `CrOptiroute` | `AcrPush` | Publier les images de release |
| `id-gh-optiroute-deploy` | Ressource `app-optiroute` seulement | `Contributor` | Créer la révision issue de GitHub Actions |
| Exploitant humain | `CoffreOptiroute` | `Key Vault Secrets Officer` | Créer et renouveler `streamlit-secrets` |
| Exploitant humain | Groupe de ressources / ressources ciblées | `Contributor` selon besoin | Configurer sans gérer les attributions RBAC |

Une personne `Contributor` peut créer et modifier beaucoup de ressources, mais ne peut généralement
pas créer des attributions de rôles (`Microsoft.Authorization/roleAssignments/write`). Une demande
IAM doit donc être faite à un `Owner` ou `User Access Administrator`, avec la portée précise du
tableau. Ne demander ni `Owner`, ni `Global Administrator`, ni l'activation du compte administrateur
ACR pour ce scénario.

## 11. Vérification de mise en production

Avant d'annoncer un déploiement, vérifier dans cet ordre :

1. Le workflow GitHub est vert, l'image de version est visible dans `CrOptiroute` et la révision
   correspondante est saine.
2. Le FQDN HTTPS charge Streamlit et `/_stcore/health` répond.
3. Le conteneur utilise l'image voulue, le port cible `8501`, l'identité `id-optiroute` et les
   variables Azure Blob indiquées plus haut.
4. Le fichier monté `/app/.streamlit/secrets.toml` est syntaxiquement valide, sans afficher ses
   valeurs, et le callback OIDC est exactement `https://<fqdn>/oauth2callback`.
5. Une connexion de chaque rôle de test fournit le bon App Role ; l'ADMIN accède à l'administration,
   et ATC/RAG/DR ont un profil interne provisionné.
6. L'import de portefeuille persiste après une nouvelle révision et après une reconnexion : cela
   confirme l'utilisation de Blob plutôt que du système de fichiers éphémère.
7. Les logs ne révèlent aucun secret et les alertes de coût / disponibilité sont configurées selon la
   politique de l'équipe.

Références Microsoft : [identités managées pour Container Apps](https://learn.microsoft.com/azure/container-apps/managed-identity),
[secrets Container Apps](https://learn.microsoft.com/azure/container-apps/manage-secrets),
[OIDC GitHub Actions](https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect)
et [App Roles Entra](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps).
