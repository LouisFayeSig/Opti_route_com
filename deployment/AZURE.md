# Déploiement Azure sécurisé

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

## 3. Configuration OIDC Streamlit

Monter un secret Container Apps nommé par exemple `streamlit-secrets`, référencé depuis Key Vault,
comme fichier `/app/.streamlit/secrets.toml`. Le contenu attendu est :

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

## 4. Stockage par identité managée

Configurer les variables non secrètes suivantes sur la Container App :

```text
APP_STORAGE_BACKEND=azure_blob
AZURE_STORAGE_ACCOUNT_URL=https://<compte>.blob.core.windows.net
AZURE_STORAGE_CONTAINER=opti-route-private
AZURE_MAPS_URI=https://atlas.microsoft.com
```

Attribuer à l'identité managée le rôle **Storage Blob Data Contributor**, avec la portée la plus
étroite possible. Aucun rôle Storage n'est nécessaire pour les utilisateurs finaux : toutes les
lectures passent par le contrôle d'accès de l'application.

Sur le compte Storage :

- désactiver l'accès public des blobs ;
- activer le versioning ;
- activer le soft-delete des blobs et conteneurs pendant 30 jours ;
- supprimer les anciennes versions après 90 jours avec une règle de cycle de vie ;
- interdire l'authentification par clé partagée lorsque tous les outils d'exploitation utilisent
  Entra ID.

## 5. Variables et secrets

Le secret client OIDC, le `cookie_secret` et une éventuelle clé Azure Maps sont des secrets Key
Vault. L'URL Storage, le tenant ID, les noms des rôles et le nom du conteneur ne sont pas secrets.
`AZURE_STORAGE_CONNECTION_STRING` ne doit pas être défini en production.

## 6. Initialisation des habilitations

1. L'administrateur se connecte avec son rôle Entra `D-FR-APP-OPTIROUTE-ADMIN`.
2. Il importe le portefeuille contenant `Code ATC`, `Commercial`, `Agence` et `Code UG`.
3. Dans **Administration → Habilitations**, il associe :
   - l'Object ID Entra d'un ATC à un code ATC ;
   - l'Object ID Entra d'un RAG à son e-mail N+1 ;
   - l'Object ID Entra d'un DR à une ou plusieurs UG.
4. L'utilisateur se reconnecte. Le rôle du jeton et le rôle attendu doivent être identiques.

Retirer une habilitation bloque son périmètre au prochain rerun Streamlit. Retirer un App Role dans
Entra prend pleinement effet à l'expiration du jeton courant ou après déconnexion ; les politiques
Conditional Access du tenant peuvent réduire cette durée selon le niveau de risque souhaité.

## 7. Image et exploitation

Le `Dockerfile` exécute Streamlit avec un utilisateur Linux non-root. Pour une première mise en
production économique, utiliser 0,5 vCPU, 1 Gio de mémoire, `minReplicas=0` et `maxReplicas=1`.
Augmenter ensuite le maximum après un test de charge et de reconnexion WebSocket.

La sonde HTTP est disponible sur `/_stcore/health`. Les logs ne doivent contenir ni adresses clients,
ni claims complets, ni secrets. Conserver les logs applicatifs 30 jours et les opérations
administratives Storage selon la politique d'audit de l'entreprise.

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
ressource Container App `app-optiroute`. Elle doit avoir une crédential fédérée OIDC limitée à :

```text
issuer   = https://token.actions.githubusercontent.com
audience = api://AzureADTokenExchange
subject  = repo:LouisFayeSig/Opti_route_com:ref:refs/heads/main
```

Ne pas donner `AcrPush` à l'identité runtime `id-optiroute` : elle conserve uniquement `AcrPull`,
`Storage Blob Data Contributor` et `Key Vault Secrets User`.

Le tag `v1.0.0` ayant été créé avant l'ajout du workflow, lancer le premier build par
**Actions > Build and deploy Opti Route > Run workflow**, avec `image_tag` et `source_ref` égaux
à `v1.0.0`. Cela construit exactement le commit de la release, même si `main` a évolué depuis.

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

### Informations à relever pour IAM

Depuis la ressource `id-optiroute`, relever le `Principal ID`, `Client ID` et `Resource ID`.
Le premier sert aux attributions RBAC, le dernier sert à attacher l'identité à la Container App.
Depuis l'ACR, relever le chemin exact de l'image et son tag, par exemple
`croptiroute.azurecr.io/opti-route:v1.0.0`.
