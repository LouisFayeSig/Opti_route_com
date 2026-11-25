# Demandes d'habilitation et vérification Power Automate

Le flux fourni vérifie une seule demande par appel, en lecture seule. Le Helpdesk conserve
la gestion des groupes AD et sa procédure GLPI. L'application ne reçoit aucun droit d'écriture AD.

## Configuration du Container App

| Paramètre | Valeur |
|---|---|
| `FLOW_LECTURE_AD_URL` | URL HTTPS du déclencheur du flux existant, conservée dans la configuration sécurisée Azure |
| `HELPDESK_EMAIL` | `SIG-ROBOTIT@sigplc.fr` par défaut ; remplaçable par l'adresse de l'agent |
| `APP_STORAGE_BACKEND` | `azure_blob` en production |
| `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER` | Compte et conteneur privés existants |

L'URL est déjà configurée selon la préparation du flux : vérifier qu'elle est présente sur la
nouvelle révision déployée. Ne pas la copier dans les journaux, captures ou le dépôt.
Le client réutilise `DefaultAzureCredential`, comme le client Blob existant. L'identité managée
du Container App doit être celle autorisée dans le déclencheur du flux. Pour une identité assignée
par l'utilisateur, conserver `AZURE_CLIENT_ID` avec son client ID. Aucune permission Graph
supplémentaire n'est introduite. Les droits existants `Storage Blob Data Contributor` couvrent
les écritures, ETags et le verrou Blob utilisés dans le même conteneur.

La portée de jeton est exactement `https://service.flow.microsoft.com//.default` : double slash,
pour l'audience `https://service.flow.microsoft.com/`. Jeton mis en cache et renouvelé cinq minutes
avant expiration. POST synchrone, timeout de 110 secondes, sans redirection ni nouvelle tentative
automatique. Seuls identité, action, groupe et identifiant de demande sont envoyés ; aucun code de
périmètre ne quitte l'application pour le flux.

La réponse HTTP 200 contient directement le JSON du résultat : `version: 2`, le même `demandeId`,
la même `action`, le même `groupe`, un `statut` reconnu et l'`objectId` du compte vérifié. Pour une
confirmation `APPLIQUEE`, l'Object ID est obligatoire et doit correspondre à celui de la demande
s'il est déjà connu. L'enveloppe `statusCode / headers / body` visible dans l'action Réponse de
Power Automate ne doit pas être ajoutée au contenu JSON du corps HTTP.

`utilisateur` est facultatif : absent, `{}` ou `null` (également sous forme de texte JSON) signifie
qu'aucune fiche supplémentaire n'est fournie. C'est notamment le cas lors d'une vérification par
Object ID après résolution du nom. Un objet rempli doit contenir le même Object ID que le résultat.
`candidats` est un tableau, normalement `[]` en l'absence de candidats ; `execution` identifie le run.
Le statut `APPLIQUEE` confirme les ajouts/changements et clôture les retraits lorsque le reste du
résultat est cohérent ; une erreur technique laisse la demande en attente.

## Parcours administrateur

1. Dans **Administration → Demandes de groupe AD**, saisir une demande. Pour un ajout, nom et prénom
   suffisent. Pour modifier ou retirer un compte, sélectionner son profil ou saisir son Object ID.
2. Renseigner le périmètre cible : code ATC dans les formats déjà présents dans le portefeuille,
   e-mail N+1/RAG pour RAG, UG séparées par des tirets pour DR, aucun périmètre pour ADMIN.
3. Enregistrer : un premier contrôle immédiat identifie le compte ou propose les candidats.
   Un ajout/changement non confirmé reste en attente, sans modifier le profil.
4. Ouvrir le brouillon à destination du Helpdesk puis l'envoyer dans la messagerie de l'admin.
   Objet : `Groupe AD [Nom Prénom] OptiRoute` ; corps :
   `J'aimerais [ajouter / modifier / supprimer] [Nom Prénom] [au / du] groupe [DR / ATC / ADMIN / RAG]`.
5. Après intervention du Helpdesk, vérifier la demande. Une réponse `APPLIQUEE` écrit le profil,
   puis confirme et archive la demande. Pour un retrait, la confirmation clôture la demande ;
   le profil était déjà désactivé dès l'enregistrement.

Les formulaires historiques **Habilitations** passent par le même moteur. Un changement dans le
même groupe ajuste directement le périmètre et est archivé avec preuve `ADMIN`, sans mail ni flux.
Lorsqu'un code ATC change, un ancien e-mail ou nom commercial conservé à l'identique est effacé pour
qu'il ne masque pas le nouveau code. Un nouvel e-mail ou nom explicitement renseigné est conservé.

Une réponse ambiguë ou sans correspondance nécessite le choix d'un candidat ou une correction du
nom, puis un nouveau contrôle. Une seule demande ouverte est autorisée par Object ID ou, tant que
celui-ci est inconnu, par nom/prénom normalisés. L'ajout d'un compte déjà actif dans un autre groupe
invite à utiliser **Modifier**. Annuler un retrait ne réactive pas un profil déjà désactivé auparavant.

## Prête-noms, départs et reprises de portefeuille

Les lignes **Utilisateurs** sans Object ID des populations ATC, RAG et DR sont conservées dans un
catalogue distinct. Elles ne créent aucun compte, aucun Object ID fictif et aucun droit de connexion.
Le catalogue est alimenté lors de l'import du portefeuille, même si l'option de provisionnement des
comptes est désactivée. La règle porte sur le rôle ATC/RAG/DR et l'absence d'Object ID, indépendamment
du libellé du prête-nom ; les lignes ADMIN sont toujours exclues.

Après déploiement, **la première ouverture de l'administration migre aussi le portefeuille déjà
chargé**, sans dépôt de fichier ni appel au flux AD. Elle retrouve les libellés explicites de rôle
et ville/repère encore présents dans les données normalisées : ATC Angers, ATC164, RAG Bordeaux,
DR Nantes. Les périmètres associés à des Object ID connus sont exclus, même si les comptes sont
désactivés ; les personnes sans habilitation ne sont pas classées automatiquement sur la seule
absence de profil. Le catalogue existant, les reprises et les annulations sont préservés.

Un marqueur versionné `access-requests/scope-bootstrap.json` rend cette migration idempotente entre
réplicas et sessions. Un portefeuille inchangé n'est pas rescanné à chaque affichage ; une nouvelle
version des données réévalue les libellés. La migration ne modifie ni les affectations ni les droits.

**Limite de reprise historique :** l'ancien import ne conservait pas la feuille Utilisateurs. Si un
prête-nom, son rôle ou son périmètre n'existe plus dans les données normalisées (notamment les anciens
DR sans champ de nom), la règle exacte ne peut pas être recalculée sans cette feuille. Dans ce cas,
utiliser **Habilitations → Prête-noms et périmètres sans compte Entra → Importer les prête-noms depuis
un classeur Excel** : un seul dépôt récupère toutes les lignes concernées, sans saisie individuelle
et sans réimporter les clients.

Le même panneau permet de créer ou compléter un périmètre sans compte. Le libellé est formé du
rôle et de la ville ou du repère : **ATC Angers**, **RAG Bordeaux**, **DR Nantes**, ou **ATC 164**.
Les libellés présents dans le classeur sont conservés. Une ligne incomplète reste visible et peut
être complétée avant sa reprise. Le périmètre correspond à un code/nom commercial pour ATC, un
e-mail N+1/RAG pour RAG, une liste d'UG pour DR.

### Départ d'un collaborateur

Dans **Demandes de groupe AD → Supprimer**, sélectionner le compte puis cocher **Conserver le
portefeuille sous un prête-nom** et saisir la ville. L'enregistrement désactive immédiatement le
compte et renomme ses affectations dans les données clients sous le libellé rôle + ville.
Les codes, UG et les clés d'e-mail existantes sont conservés à cette étape. Pour RAG, le portefeuille
commercial personnel est également renommé si son e-mail correspond à celui du responsable.
Pour DR, les champs optionnels `dr_name` et `dr_email` permettent de conserver les libellés des
responsables régionaux ; le passage au prête-nom renseigne `dr_name` sur les UG concernées.

Le prête-nom est signalé **Départ en cours** jusqu'à confirmation du retrait AD, puis devient
disponible. Annuler le retrait restaure les affectations précédentes, retire ce prête-nom du
catalogue disponible et restaure uniquement l'accès désactivé par cette demande.

### Reprise par un nouveau collaborateur ou un compte existant

Dans **Demandes de groupe AD → Ajouter** ou **Modifier**, choisir le groupe cible puis sélectionner
**Portefeuille / prête-nom à reprendre**. Le code ou périmètre est prérempli. Laisser cochée l'option
de renommage et renseigner l'e-mail du repreneur, notamment pour RAG. Les affectations sont mises à
jour au nom saisi pour le collaborateur. Pour ATC, le code renseigné est également appliqué aux
clients repris ; un e-mail vide retire l'ancienne adresse et rattache par code et nom.

Pour un ajout ou un changement de groupe, le renommage des clients et l'écriture du profil attendent
la preuve AD ou une nouvelle connexion valide. Pour un compte déjà actif dans le même groupe,
l'attribution du nouveau périmètre est appliquée directement et historisée avec preuve `ADMIN`.
Un prête-nom attribué est conservé dans l'historique du catalogue avec l'Object ID de son repreneur.

### Modifier uniquement les droits de périmètre

Dans **Habilitations**, sélectionner **Compte dont le périmètre doit être modifié**. Le compte,
le rôle et les champs métier sont préremplis. Modifier le code/nom/e-mail ATC, l'e-mail N+1/RAG,
ou les UG du DR, puis enregistrer. RAG et DR disposent d'une saisie libre en complément des listes
du portefeuille. Le maintien du même groupe applique la modification directement, sans mail ni
appel au flux ; le changement de groupe suit le cycle AD.

### Persistance et prévention des conflits

Les prête-noms sont des documents `access-scopes/<id>.json` dans le même stockage privé que les
profils. Les affectations modifiées sont enregistrées dans le portefeuille normalisé de
l'application. Dans **Portefeuille d'entreprises → Sauvegarde à jour du portefeuille**, cliquer
sur **Préparer la sauvegarde à jour**, puis télécharger :

- **le portefeuille à jour (.xlsx)** : Listing client, Utilisateurs, Habilitations, Prete-noms,
  Demandes AD et une feuille Lire-moi ;
- **la sauvegarde complète (.zip)** : le même Excel et `etat_application.json`, contenant les
  profils métier stockés, les états d'accès (y compris les désactivations ADMIN), le catalogue,
  les demandes en cours et archivées, les clients et les réglages métier.

La copie est construite sur les données persistantes sous verrou, au moment du clic, puis datée
en heure de Paris. Elle ne dépend pas du dernier fichier chargé ou des anciennes données affichées
en cache. Régénérer la copie après toute nouvelle modification. Les chaînes Excel sont enregistrées
comme texte littéral afin de préserver les valeurs et d'éviter l'interprétation comme formules.

**Le fichier Excel source dans OneDrive ou sur le poste de l'admin n'est pas écrasé automatiquement.**
Le téléchargement fournit sa copie métier actualisée. Cette reconstruction contient les colonnes
utilisées par l'application ; elle ne peut pas restituer les feuilles ou colonnes non importées du
classeur original. Les secrets de connexion, URL du flux et clés Azure ne sont pas exportés.

Le nouvel Excel peut être utilisé comme source de clients en sélectionnant **Listing client** et,
pour conserver les droits existants, **Ne pas modifier les habilitations**. Le provisionnement à
partir de Utilisateurs conserve les règles Entra habituelles : un e-mail/UPN est nécessaire pour
ATC, RAG et DR. Les profils par code seul et les désactivations restent intégralement conservés
dans Habilitations et dans le JSON. **L'import Excel classique ne restaure pas automatiquement les
documents du JSON** ; conserver l'archive pour une reprise complète de l'état métier.

Chaque renommage conserve les clients concernés et les valeurs avant/après pour permettre sa
reprise après interruption et son annulation. Deux réaffectations ouvertes ne peuvent pas porter
sur les mêmes clients. Un import complet est bloqué tant qu'une demande réaffecte un portefeuille ;
il redevient disponible après clôture ou annulation. Les écritures partagent le verrou et les ETags
du cycle d'habilitation. Les documents sont limités à 16 Mio pour les journaux d'affectation.

## Contrôles et sessions

Un contrôle automatique des demandes en attente a lieu à l'ouverture de leur onglet, avec un délai
partagé de 15 minutes. Aucun audit global des groupes n'est exécuté. Un contrôle manuel traite au
plus 30 demandes séquentiellement, s'arrête après trois erreurs techniques consécutives et ne démarre
plus d'appel après deux minutes de traitement. Le dernier appel démarré reste soumis à son timeout.
Les demandes de plus de sept jours sont signalées ; l'historique affiche les trois derniers mois.
Il n'y a pas de tâche autonome lorsque personne n'utilise l'application.

Une nouvelle connexion Entra peut confirmer un ajout ou un changement de groupe : Object ID exact,
rôle applicatif unique attendu, jeton encore valide et émis après la création de la demande. Ce
contrôle est effectué une fois par identité/jeton dans chaque session. Il ne résout pas les noms et
ne confirme jamais un retrait. Le mode mot de passe ne constitue pas une preuve AD.

La désactivation locale est contrôlée avant l'administration et avant l'affichage du portefeuille,
à chaque réexécution Streamlit. Une session laissée immobile affiche encore ce qui était déjà reçu ;
sa prochaine interaction est refusée. Après un changement confirmé, une session portant encore
l'ancien rôle est bloquée et demande une reconnexion Microsoft, y compris pour un ancien ADMIN.

## Stockage et concurrence

Une nouvelle release conserve les demandes en cours si `APP_STORAGE_BACKEND=azure_blob` et si le
compte et le conteneur de stockage restent les mêmes. Le workflow de déploiement change l'image ;
il ne supprime pas les blobs des demandes, profils ou portefeuilles. Une session navigateur peut
devoir être rechargée, puis une demande existante peut être vérifiée à nouveau, sans la recréer.
Un SQLite placé dans le système de fichiers éphémère du conteneur ne fournit pas cette garantie.

Les modifications sont enregistrées au fil des opérations et les demandes ont un historique.
Les plans de renommage conservent les valeurs avant/après pour leur reprise ou annulation.
**La sauvegarde complète Excel/ZIP est déclenchée manuellement** par Préparer la sauvegarde à jour :
il n'existe pas encore de copie complète automatique avant chaque modification ou chaque release.

Le compteur **Clients prévus pour le renommage** décrit le plan préparé à l'enregistrement, y
compris quand la demande est encore en attente. Zéro signifie qu'aucun plan de renommage de clients
n'est enregistré (option non cochée ou aucun client retrouvé). Ce chiffre ne mesure ni l'adhésion
au groupe AD, ni à lui seul l'accès effectif du nouveau profil à son périmètre.

Les formats des profils et imports existants sont conservés :

- Azure : profils métier `access-profiles/<sha256 de l'oid normalisé>.json`, format existant v2.
- État d'accès séparé : `access-state/<même hash>.json`, avec activation, groupe confirmé,
  propriétaire de la demande et métadonnées ADMIN. Permet de bloquer ADMIN sans profil métier.
- Demandes : `access-requests/pending/<32 caractères hexadécimaux>.json`.
- Archives : `access-requests/done/YYYY-MM/<id>.json` ; pas de suppression automatique d'archives.
- Coordination : `access-requests/.lock` et `access-requests/check-control.json`.
- Développement SQLite : mêmes documents JSON dans la table `access_documents`, profils inchangés.

Les écritures de documents et suppressions sont conditionnées par ETag. Un bail Blob de 60 secondes,
renouvelé avant les écritures, coordonne les changements entre réplicas ; les appels au flux ont lieu
hors de ce verrou. SQLite utilise une transaction `BEGIN IMMEDIATE`. Un conflit demande de réessayer.
Le profil est écrit avant la clôture. Des marqueurs permettent de reprendre une application ou un
retrait interrompus sur Azure, où plusieurs blobs ne forment pas une transaction atomique. Une
réponse du flux arrivée après une annulation ou une modification de demande est ignorée.

Un import ne peut pas écraser un profil concerné par une demande ouverte. Les autres imports restent
disponibles et ne réactivent pas les comptes désactivés. Le classeur importé n'est pas réécrit :
l'application persiste ses données normalisées et les profils, comme auparavant.

Les événements stdout `access_flow_check` indiquent demande, action, groupe, statut, HTTP, durée et
numéro d'exécution. Ils n'incluent ni URL du flux, ni jeton, ni candidats. Le contexte habituel de
télémétrie identifie l'auteur de l'opération.

## Recette Azure après déploiement

Les tests locaux utilisent un flux et un stockage Azure simulés ; ils ne prouvent pas la connexion
réelle à Power Automate. Sur une révision de recette, avec des comptes de test :

1. Créer un ajout ATC avec un nom unique : Object ID récupéré, profil absent tant que `EN_ATTENTE`,
   brouillon vers `SIG-ROBOTIT@sigplc.fr`, codes absents de l'entrée du flux.
2. Faire appliquer le groupe par le Helpdesk ; vérifier : `CONFIRMEE`, preuve `AD`, profil correct,
   archive présente. Répéter le contrôle : aucun doublon.
3. Tester homonyme et nom introuvable, puis choix d'un candidat ou correction du nom.
4. Tester ATC → RAG/DR avec nouveau périmètre : conserver l'ancien profil avant confirmation,
   puis appliquer le nouveau. Confirmer aussi par une nouvelle connexion au bon rôle.
5. Tester un retrait : accès refusé à la prochaine interaction, profil conservé ; annulation réactive
   uniquement l'accès désactivé par cette demande. Confirmer le retrait après intervention AD.
6. Tester un code seul : application directe, preuve `ADMIN`, aucun appel au flux et aucun brouillon.
7. Tester une indisponibilité du flux et un refus d'authentification : aucune confirmation,
   message maîtrisé, aucun secret dans les journaux ; réessayer une fois le service rétabli.
8. Ouvrir deux sessions admin et vérifier/annuler la même demande : aucune double application.
   Vérifier qu'un compte non admin n'accède ni aux commandes ni aux contrôles automatiques.

La récupération d'identité et la validation d'audience doivent être confirmées dans cette recette.
Aucun changement Azure ni envoi de mail n'est réalisé par les tests du dépôt.
