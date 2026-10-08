# Demande de configuration Microsoft Entra ID - Opti Route Com

**Objet :** creation et parametrage de l'application Microsoft Entra ID pour Opti Route Com.

Bonjour,

Merci de preparer l'acces Microsoft Entra ID pour l'application interne **Opti Route Com**.
L'objectif est qu'un commercial ne consulte que son propre portefeuille clients, tandis qu'un directeur
accede uniquement aux portefeuilles des commerciaux qui lui sont rattaches. Les administrateurs designes
ont un acces global.

Le fichier joint `utilisateurs_microsoft_entra_opti_route.csv` contient la liste des comptes a attribuer,
ainsi qu'une colonne a completer avec leur **Object ID Entra**.

## Actions demandees dans Entra

1. Creer une **App registration** nommee `Opti Route Com`, limitee aux comptes de l'organisation
   (*Accounts in this organizational directory only*).
2. Configurer une plateforme **Web** et les URI de redirection :
   - pilote local : `http://localhost:8501/oauth2callback` ;
   - production : `https://<URL-PRODUCTION>/oauth2callback`.
3. Creer un secret client. Sa valeur doit etre transmise par canal securise ; elle ne doit pas etre
   envoyee par e-mail ni ajoutee au depot Git.
4. Definir les App Roles suivants :

| Valeur technique | Libelle | Perimetre applicatif |
|---|---|---|
| `OptiRoute.ATC` | Opti Route - ATC | Portefeuille du seul commercial connecte |
| `OptiRoute.Director` | Opti Route - Directeur | Portefeuilles de ses commerciaux rattaches, plus son propre portefeuille eventuel |
| `OptiRoute.Admin` | Opti Route - Administrateur | Acces global, import et administration |

5. Dans **Enterprise applications -> Opti Route Com -> Users and groups**, attribuer exactement **un**
   de ces roles a chaque compte de la liste. Activer *Assignment required* afin de bloquer tout compte
   non explicitement attribue.
6. Ne pas ajouter de permissions Microsoft Graph : l'application ne consulte pas l'annuaire a l'execution.

## Informations a remettre a l'equipe Opti Route

- Directory (tenant) ID ;
- Application (client) ID ;
- secret client, par canal securise ;
- le fichier CSV joint complete avec l'**Object ID Entra** de chaque compte attribue ;
- l'URL de production definitive, afin de valider l'URI de redirection.

## Mise en correspondance dans l'application

Apres la configuration Entra, un administrateur Opti Route associera chaque Object ID retourne au
commercial ou directeur correspondant dans **Administration -> Habilitations**. L'Object ID reste la cle
d'autorisation stable ; les e-mails du portefeuille servent uniquement a calculer le perimetre metier.

Une personne presente a la fois comme commercial et comme directeur recoit uniquement le role
`OptiRoute.Director` : l'application lui laisse aussi voir son propre portefeuille si elle en possede un.
L'attribution de plusieurs roles a un meme compte est volontairement refusee.

## Synthese de la liste jointe

- **3** administrateurs ;
- **34** directeurs ;
- **70** ATC identifies par e-mail ;
- **7** entrees ATC incompletes, a identifier avant attribution.

### Comptes ATC sans e-mail dans le portefeuille

| Libelle portefeuille | Code ATC | Code agence |
|---|---:|---:|
| Atc MARSEILLE | `05` | `13` |
| Atc ANGERS | `30` | `20` |
| Atc LA ROCHELLE | `01` | `20` |
| Atc BREST | `03` | `22` |
| Atc BORDEAUX | `33` | `24` |
| Atc AUCAMVILLE | `001` | `31` |
| Atc 164 | `164` | `40` |

Ces lignes ne doivent pas etre attribuees tant que l'UPN ou l'e-mail professionnel correspondant n'a pas
ete confirme.

---

**Piece jointe :** `utilisateurs_microsoft_entra_opti_route.csv`.

