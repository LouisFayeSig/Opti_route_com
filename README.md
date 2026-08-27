# Opti Route Com

Application Streamlit interne pour préparer et optimiser des tournées commerciales à partir d'un portefeuille clients.

## Fonctionnalités du MVP

- import d'un portefeuille aux formats CSV, XLS, XLSX, XLSM ou XLSB ;
- détection automatique des colonnes et correspondance manuelle si leurs noms varient ;
- choix de la feuille et de la ligne d'en-tête ;
- utilisation possible d'une simple liste d'adresses, sans nom de client ni commercial ;
- filtrage facultatif du portefeuille par commercial ;
- sélection ou désélection individuelle des adresses avant le calcul ;
- départ depuis la position du navigateur, une adresse ou un client existant ;
- cache SQLite des adresses géocodées ;
- présélection des clients par rayon géographique ;
- matrice routière Azure Maps et optimisation OR-Tools ;
- affichage Azure Maps, ordre des visites et indicateurs ;
- export Excel et CSV, PDF avec capture cartographique, ainsi que partage vers Google Maps ;
- noms d'exports horodatés pour éviter les doublons ;
- mode d'estimation local lorsque la clé Azure Maps n'est pas configurée.

## Installation

Python 3.11 ou plus récent est nécessaire.

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

## Lancement

```powershell
streamlit run app.py
```

Le fichier peut être importé depuis la page. Pour proposer un fichier déjà présent sur le serveur, le placer dans `data/` ou définir son chemin dans `.env` :

```dotenv
CLIENTS_FILE=data/mon_portefeuille.xlsx
```

Le calcul des routes reste effectué par Azure Maps. Le fond interactif Streamlit utilise PyDeck par défaut afin de ne pas exposer la clé au navigateur et de ne pas dépendre des règles CORS. Le contrôle Web Azure peut être réactivé après configuration de l'origine Streamlit dans Azure Maps :

```dotenv
MAP_RENDERER=azure
```

## Colonnes clients

Les intitulés sont libres. L'application tente de reconnaître automatiquement les champs suivants, puis permet au collaborateur de corriger la correspondance :

| Champ interne | Exemples reconnus |
|---|---|
| Code client | `Code client`, `ID client`, `Compte` |
| Nom | `Client`, `Raison sociale`, `Nom compte` |
| Commercial | `Commercial`, `Vendeur`, `Responsable commercial` |
| Adresse | `Adresse`, `Rue`, `Adresse 1` |
| Code postal | `Code postal`, `CP`, `ZIP` |
| Ville | `Ville`, `Commune`, `Localité` |
| Pays | `Pays`, `Country` |
| Coordonnées | `Latitude` / `Longitude`, `Lat` / `Lon` |

Une colonne d'adresse suffit. Le code, le nom du client et le commercial sont facultatifs ; des identifiants et libellés sont générés lorsque ces champs manquent. Les coordonnées sont également facultatives : les lignes qui n'en possèdent pas sont géocodées via Azure Maps et mises en cache dans `.cache/geocoding.sqlite3`.

## Tests

```powershell
pytest
ruff check .
```

## Sécurité

Le fichier `.env`, le cache et les portefeuilles placés dans `data/` sont ignorés par Git. Pour une mise en production, préférer un jeton SAS Azure Maps limité aux origines autorisées ou une authentification Microsoft Entra ID plutôt que d'exposer une clé permanente au contrôle cartographique du navigateur.
