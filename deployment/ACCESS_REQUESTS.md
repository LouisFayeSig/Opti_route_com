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
