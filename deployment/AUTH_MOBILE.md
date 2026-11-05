# Authentification et accès mobile

## Signalement du 5 novembre 2026

Dans Chrome mobile, la connexion demande de sélectionner un certificat d'entreprise. Le serveur
indiqué dans la demande est `device.login.microsoftonline.com:443`.

Ce signalement correspond à une demande de **certificat client pour identifier l'appareil** lors
de la connexion Microsoft. Il ne suffit pas à conclure à un défaut du certificat HTTPS d'Opti Route
ni à l'activation de l'authentification utilisateur par certificat (CBA). Microsoft documente cette
sélection sur Android et iOS lorsqu'Entra identifie un appareil enregistré pour l'accès conditionnel :
[demande de certificat dans le navigateur](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-conditions#why-do-i-see-a-certificate-prompt-in-the-browser).

La règle exacte appliquée au compte reste à confirmer dans les journaux Entra. Le code Streamlit
ne contrôle pas la fenêtre de sélection de certificat présentée par le navigateur sur le domaine
Microsoft.

Contrôles réseau effectués depuis le poste de développement le 5 novembre 2026, avec validation
TLS activée et sans authentification :

| Adresse testée | Réponse de `/_stcore/health` |
| --- | --- |
| `https://app-optiroute.grayrock-929c9395.westeurope.azurecontainerapps.io` | HTTP 200 |
| `https://pp-optiroute.grayrock-929c9395.westeurope.azurecontainerapps.io` (adresse saisie dans le signalement) | HTTP 404 |

L'adresse à diffuser est celle commençant par **app-optiroute**. Ces contrôles valident l'accès HTTPS
depuis ce poste ; ils ne reproduisent pas le parcours Microsoft ni la confiance TLS du téléphone.

## Vérifications Entra / Intune

1. Dans **Entra ID > Monitoring & health > Sign-in logs**, retrouver la connexion du collaborateur
   à **Opti Route**, à l'heure du problème. Relever le statut, le code d'erreur, le Correlation ID,
   le navigateur, la plateforme et le Device ID. Ne pas collecter de jeton ni de clé privée.
2. Ouvrir **Conditional Access**, **Device info** et **Authentication details** pour identifier
   les règles effectivement appliquées : appareil conforme, appareil enregistré, protection
   d'application, fréquence de connexion et persistance du navigateur. Une demande de certificat
   seule ne permet pas de déterminer laquelle est responsable.
3. Vérifier dans Intune que l'appareil est enregistré et conforme, que le certificat attendu est
   valide et accessible depuis le profil professionnel utilisé par Chrome. Confirmer aussi le
   composant d'authentification prévu par l'entreprise (Authenticator ou Company Portal).
4. Comparer, sur le même appareil et avec le même compte, Chrome du profil professionnel et Edge
   connecté au compte professionnel, si ces navigateurs sont autorisés par l'entreprise. Conserver
   les cookies pendant le test ; le mode privé ne transmet pas correctement l'identité de l'appareil
   pour les exigences de conformité. Voir les
   [navigateurs pris en charge](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-conditions#supported-browsers).
5. Vérifier si la sélection apparaît seulement à la première connexion ou systématiquement après
   fermeture, expiration ou déconnexion. Une demande initiale est documentée par Microsoft ; une
   répétition doit être rapprochée des journaux, du profil du navigateur et des règles de session.
6. Toute modification de l'accès conditionnel ou du déploiement des certificats relève de l'équipe
   Entra/Intune. La suppression de cette fenêtre ne peut pas être garantie par une modification
   de l'application ; ne pas désactiver les exigences de conformité pour contourner le problème.

## Session applicative et hébergement

- `AUTH_MODE=password` conserve l'identité dans la session Streamlit, sans cookie de connexion
  persistant propre à l'application. Une nouvelle session peut donc redemander les identifiants.
- `AUTH_MODE=entra` utilise le cookie d'identité Streamlit. Un jeton expiré relance désormais
  `st.login("microsoft")` pour réutiliser le SSO ; aucun portefeuille n'est accessible avant un
  nouveau jeton valide. Les règles Microsoft peuvent encore exiger une interaction.
- La déconnexion explicite efface l'état applicatif et utilise `st.logout()` en mode Entra.
  Selon le fournisseur et la version de Streamlit, ce dernier peut aussi terminer la session
  Microsoft. Les autres onglets déjà ouverts ont leur propre session : tester ce cas séparément.
- Le `cookie_secret` doit être stable et identique entre révisions et réplicas. Le renouveler à
  chaque déploiement invalide les cookies existants.
- Vérifier que le lien distribué, le `redirect_uri` dans `[auth]` et l'URI Web inscrite dans Entra
  correspondent exactement à `https://app-optiroute.grayrock-929c9395.westeurope.azurecontainerapps.io/oauth2callback`.
- Dans **Container Apps > Authentication**, vérifier si une seconde authentification plateforme
  est imposée en amont de Streamlit. L'architecture documentée repose sur l'OIDC Streamlit : une
  seconde couche doit être justifiée, car elle peut créer un second parcours de connexion.
- Vérifier le nombre de réplicas et l'affinité de session si l'état se perd après une reconnexion
  WebSocket. Les données métier persistent dans Blob, mais l'état Streamlit vit dans le processus.
- Une alerte de certificat serveur portant sur le domaine Opti Route doit être examinée à part :
  nom DNS, expiration, chaîne complète et éventuelle inspection TLS d'entreprise. Ne pas désactiver
  la validation TLS ou XSRF.

Références : [authentification Streamlit](https://docs.streamlit.io/develop/concepts/connections/authentication),
[st.login](https://docs.streamlit.io/develop/api-reference/user/st.login),
[st.logout](https://docs.streamlit.io/develop/api-reference/user/st.logout),
[appareils conformes et certificat client Entra](https://learn.microsoft.com/en-us/entra/identity/conditional-access/policy-all-users-device-compliance#known-behavior).

## Recette

Tester avec un ATC, un RAG et un DR sur un téléphone géré : ouverture du lien, connexion, fermeture
et réouverture, expiration de session puis déconnexion/reconnexion avec un autre compte. À chaque
nouvelle connexion, le calque portefeuille doit être désactivé. Après activation manuelle, il doit
rester limité au périmètre du compte.
Le masquage doit persister pendant les calculs et recalculs de la même session. Le compte suivant
ne doit retrouver ni la tournée ni les exports du précédent. Consigner séparément les demandes
Microsoft (MFA, certificat, conformité) et les erreurs applicatives.
