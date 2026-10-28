# Export Entra ID et provisionnement des habilitations

Ce guide produit le fichier d'échange entre Microsoft Entra ID et Opti Route.
Il est destiné à un administrateur Opti Route déjà authentifié sur le tenant SIG PLC.

L'export est **en lecture seule** : il lit les membres directs des quatre groupes Entra,
ne crée ni utilisateur, ni groupe, ni rôle, et ne modifie aucune affectation. Il ne faut
pas confondre ce mécanisme avec l'authentification de l'application Opti Route.

## Ce que fait le processus

```text
Groupes Entra ID
        │ téléchargement natif du portail, ou lecture Microsoft Graph
        ▼
4 CSV de groupes, ou export_entra_group_members.ps1
        │ Object ID, e-mail / UPN, rôle Entra
        ▼
Administration Opti Route > import du portefeuille
        │ rapprochement avec la feuille Utilisateurs et le portefeuille
        ▼
Habilitations internes dans Blob Storage
        ▼
Périmètre de données à la connexion de chaque collaborateur
```

Les App Roles Entra prouvent le rôle de la personne au moment de la connexion. Le CSV
ne copie pas les rôles dans Entra : il permet à Opti Route de créer ou de mettre à jour le
périmètre métier interne (ATC, RAG ou DR).

## Prérequis

- Les groupes suivants existent dans **Microsoft Entra ID**, avec des membres utilisateurs directs :
  - `D-FR-APP-OPTIROUTE-ADMIN`
  - `D-FR-APP-OPTIROUTE-ATC`
  - `D-FR-APP-OPTIROUTE-DR`
  - `D-FR-APP-OPTIROUTE-RAG`
- Ils sont affectés à l'application d'entreprise **Opti Route**, chacun avec l'App Role de
  même valeur.
- Le classeur de portefeuille contient la feuille `Utilisateurs`, dont les colonnes de référence sont
  `Role Entra`, `UPN ou e-mail` et, pour les DR, `Code Region`.

Deux modes d'export sont disponibles. L'export natif du portail est à privilégier lorsqu'aucun droit
Microsoft Graph n'est accordé. Le script PowerShell reste disponible pour produire un fichier unique.

## Voie recommandée sans droit Graph : exports natifs du portail Entra

Depuis **Applications d'entreprise > Opti Route > Utilisateurs et groupes**, ouvrir successivement
les quatre groupes affectés à l'application. Sur la page **Membres** de chaque groupe, utiliser
**Opérations en bloc > Télécharger les membres**. Donner un nom au téléchargement, lancer
l'opération puis récupérer le CSV terminé dans les notifications ou dans les résultats des opérations
en bloc.

Télécharger exactement un fichier pour chacun des groupes suivants :

```text
D-FR-APP-OPTIROUTE-ADMIN
D-FR-APP-OPTIROUTE-ATC
D-FR-APP-OPTIROUTE-DR
D-FR-APP-OPTIROUTE-RAG
```

Ces CSV contiennent déjà `Object ID`, `User principal name`, `Display name` et `Member type`.
Ne pas les modifier ni les fusionner. Dans Opti Route, sélectionner **Exports natifs du portail
Entra (sans script)**, puis déposer chaque fichier dans le champ portant le même nom que son groupe.
L'application déduit le rôle du champ choisi, vérifie qu'un Object ID n'est pas présent dans deux
groupes, et produit la même prévisualisation que le script PowerShell.

Le téléchargement de la liste des membres est accessible aux utilisateurs standards comme aux
administrateurs, sous réserve que l'utilisateur puisse déjà voir le groupe. Il ne modifie jamais
les membres ni l'affectation de rôle.

## Alternative : Azure Cloud Shell PowerShell

Le script demande les autorisations Microsoft Graph déléguées `GroupMember.Read.All` et
`User.ReadBasic.All`. Si le tenant bloque le consentement utilisateur, l'équipe Entra doit
accorder ce consentement à l'opérateur ou exécuter elle-même le script. Cette approbation porte
sur la lecture annuaire par PowerShell ; elle est distincte du consentement OIDC de l'application
Opti Route et ne se répète pas pour chaque collaborateur du portefeuille.

Cloud Shell évite d'installer Azure CLI, PowerShell ou les modules Graph sur le poste Windows.
Les fichiers placés dans le dossier personnel Cloud Shell sont conservés dans son partage de
fichiers Azure, mais le CSV reste une donnée personnelle : le supprimer une fois l'import vérifié.

1. Aller sur [portal.azure.com](https://portal.azure.com) avec le compte professionnel ou ADM.
2. Dans la barre supérieure, cliquer sur l'icône `>_` **Cloud Shell** puis choisir **PowerShell**.
   Accepter la création du stockage Cloud Shell si Azure le propose.
3. Dans la barre d'outils de Cloud Shell, choisir **Gérer les fichiers** puis **Téléverser**.
   Téléverser le fichier local `scripts/export_entra_group_members.ps1`.
4. Dans le terminal, vérifier son emplacement, puis installer les deux modules si nécessaire :

```powershell
$scriptPath = "$HOME/export_entra_group_members.ps1"
Test-Path $scriptPath

Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force -AllowClobber
Install-Module Microsoft.Graph.Groups -Scope CurrentUser -Force -AllowClobber
```

`True` doit être affiché par `Test-Path`. Si le fichier porte un autre nom après
téléversement, adapter `$scriptPath`.

5. Relever l'**ID du locataire (tenant)** dans **Microsoft Entra ID > Vue d'ensemble** puis lancer :

```powershell
$tenantId = "<ID-du-locataire-Entra>"
& $scriptPath `
  -TenantId $tenantId `
  -OutputPath "$HOME/opti-route-entraid-users.csv"
```

6. La fenêtre de connexion Microsoft demande le compte professionnel. Accepter uniquement les
autorisations de lecture indiquées ci-dessus. Si l'écran indique *Approbation requise*, ne pas
contourner le blocage : demander à l'équipe Entra d'autoriser les scopes pour cette exportation.
7. Le message final attendu est semblable à :

```text
Exported 123 Entra users to /home/<utilisateur>/opti-route-entraid-users.csv
```

8. Dans **Gérer les fichiers > Télécharger**, récupérer `opti-route-entraid-users.csv` sur le
poste. Ne pas le déposer dans le dépôt Git ni l'envoyer par e-mail non chiffré.

Le changement de souscription via `Set-AzContext` est sans effet sur la lecture des groupes :
Graph est rattaché au tenant choisi dans la fenêtre de connexion. Il est donc facultatif pour cet export.

### Commande de contrôle sans afficher les données

Avant de télécharger le fichier, ce contrôle ne montre que le nombre de lignes et les colonnes :

```powershell
$csv = Import-Csv "$HOME/opti-route-entraid-users.csv"
"Utilisateurs exportés : $($csv.Count)"
$csv | Group-Object app_role | Select-Object Name, Count
$csv | Get-Member -MemberType NoteProperty | Select-Object -ExpandProperty Name
```

Les colonnes attendues sont `app_role`, `entra_group`, `object_id`, `display_name`,
`user_principal_name` et `email`.

### Nettoyage après import validé

Après validation dans Opti Route, supprimer la copie Cloud Shell et la copie locale conformément
à la politique de conservation de l'entreprise :

```powershell
Remove-Item -LiteralPath "$HOME/opti-route-entraid-users.csv"
Remove-Item -LiteralPath $scriptPath
```

Cette suppression est irréversible dans Cloud Shell : ne l'exécuter qu'après avoir vérifié que
les habilitations ont été appliquées dans l'application.

## Import dans Opti Route

1. Se connecter à Opti Route avec un compte portant l'App Role
   `D-FR-APP-OPTIROUTE-ADMIN`.
2. Ouvrir **Administration > Portefeuille d'entreprises** et sélectionner le fichier
   `Liste_client_def.xlsx` (ou son fichier de mise à jour) contenant la feuille `Utilisateurs`.
3. Dans le même panneau, ouvrir **Provisionner les habilitations depuis l'export Entra** puis choisir :
   - **Exports natifs du portail Entra (sans script)** et déposer les quatre CSV dans les champs des
     groupes correspondants ; ou
   - **CSV consolidé produit par le script PowerShell** et déposer le fichier unique.
4. Lire la prévisualisation avant l'enregistrement. Elle annonce une action `Create` ou `Update`,
   le rôle signé Entra, le collaborateur, son Object ID et le périmètre calculé.
5. Cliquer sur **Enregistrer le portefeuille et appliquer N habilitation(s)**. L'écriture se fait
   dans le stockage applicatif Azure Blob, jamais dans Entra ID.

Les règles de rapprochement sont les suivantes :

| Rôle Entra | Source dans le classeur | Périmètre créé dans Opti Route |
| --- | --- | --- |
| ATC | E-mail / UPN dans `Utilisateurs`, puis portefeuille | Son e-mail commercial et son code ATC si univoque |
| RAG | E-mail / UPN dans `Utilisateurs`, puis portefeuille | Tous les clients dont l'e-mail N+1 correspond |
| DR | `Code Region`, par exemple `20-24-31-34` | Les UG listées et présentes dans le portefeuille actif |
| ADMIN | App Role Entra uniquement | Accès global ; aucune ligne de périmètre nécessaire |

Le processus est volontairement conservateur : une habilitation existante n'est jamais supprimée
automatiquement. Les anomalies sont affichées pour correction, notamment une adresse non retrouvée,
un rôle multiple, une région vide ou une UG absente du portefeuille. Corriger la donnée source puis
relancer l'export et l'import ; ne pas créer manuellement un deuxième rôle pour la même personne.

## Contrôles après import

- Pour un ATC, se connecter avec son compte et vérifier qu'il ne voit que son portefeuille.
- Pour un RAG, vérifier les commerciaux rattachés à son e-mail N+1 et son propre portefeuille éventuel.
- Pour un DR, vérifier les seules UG de `Code Region`.
- Pour un ADMIN, vérifier l'accès au panneau Administration et à l'import.
- Si la page indique « aucune habilitation Opti Route », vérifier d'abord dans l'application
  l'existence d'un profil correspondant à l'Object ID affiché, puis le rôle Entra reçu dans la session.

Un ajout ou retrait de groupe dans Entra ne modifie pas immédiatement le périmètre interne :
relancer l'export puis l'import. À l'inverse, un rôle Entra retiré bloque la connexion une fois la
session renouvelée, même si un ancien profil interne est toujours conservé.

## Limites connues et sécurité

- Seuls les **membres directs utilisateurs** des groupes sont exportés. Les groupes imbriqués et
  les identités non-utilisateur ne sont pas développés.
- Un utilisateur ne doit appartenir qu'à un seul des quatre groupes Opti Route. Le script signale
  les Object IDs qui apparaissent plusieurs fois ; il faut corriger Entra avant la production.
- Le CSV contient des Object IDs, noms et adresses e-mail. Il est ignoré par Git via `exports/` et
  doit être traité comme une donnée métier confidentielle.
- Le script ne requiert ni secret client Opti Route, ni clé de stockage, ni rôle Azure `Owner`.

Références Microsoft : [Microsoft Graph PowerShell](https://learn.microsoft.com/powershell/microsoftgraph/installation),
[gestion des App Roles](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps).
