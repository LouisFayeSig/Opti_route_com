# Déploiement Azure sécurisé

## 1. Ressources minimales

- Azure Container Registry Basic ;
- Azure Container Apps en consommation, avec une identité managée système ;
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
5. Attribuer exactement un rôle par utilisateur ou groupe :
   `OptiRoute.ATC`, `OptiRoute.Director` ou `OptiRoute.Admin`.
6. Ne donner aucune permission Microsoft Graph à l'application : elle n'en a pas besoin.
7. Créer un secret client, le placer dans Key Vault et prévoir sa rotation.

Le premier administrateur doit recevoir `OptiRoute.Admin` dans Entra. Ce rôle ne nécessite aucune
ligne d'habilitation dans l'application et permet donc d'initialiser les autres comptes.

## 3. Configuration OIDC Streamlit

Monter un secret Container Apps nommé par exemple `streamlit-secrets`, référencé depuis Key Vault,
comme fichier `/app/.streamlit/secrets.toml`. Le contenu attendu est :

```toml
[app]
AUTH_MODE = "entra"
ENTRA_TENANT_ID = "<tenant-id>"
ENTRA_ROLE_ATC = "OptiRoute.ATC"
ENTRA_ROLE_DIRECTOR = "OptiRoute.Director"
ENTRA_ROLE_ADMIN = "OptiRoute.Admin"

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

1. L'administrateur se connecte avec son rôle Entra `OptiRoute.Admin`.
2. Il importe le portefeuille contenant `Code ATC`, `Commercial` et `Agence`.
3. Dans **Administration → Habilitations**, il associe :
   - l'Object ID Entra d'un ATC à un code ATC ;
   - l'Object ID Entra d'un directeur à une ou plusieurs agences.
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
