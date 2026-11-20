# FIN-GUARD AI — Prototype VAE / PERFECT VISION / SQLite

Prototype Python de détection de transactions financièrement atypiques par autoencodeur variationnel (VAE), avec stockage SQLite, interface Streamlit, gestion d'investigations et exports.

> **Important :** un score élevé signale une atypie statistique, pas une fraude confirmée. Le modèle ne remplace ni les contrôles internes ni la validation d'un enquêteur. Le jeu synthétique sert uniquement à tester l'application.

## 1. Prérequis Windows

- Windows 10/11, Python 3.11 recommandé.
- Microsoft ODBC Driver 18 for SQL Server si connexion SQL Server.
- Accès SQL Server en lecture seule pour l'extraction.
- PERFECT VISION : les noms de tables et colonnes dépendent de votre version et de votre paramétrage. Le prototype ne présume pas du schéma.

## 2. Installation

Dans PowerShell, depuis le dossier du projet :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Si l'activation PowerShell est bloquée :
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. Initialiser et tester avec des données synthétiques

```powershell
python -m fin_guard init-db
python -m fin_guard demo
python -m fin_guard train
python -m fin_guard detect
streamlit run app.py
```

Si la commande `python -m fin_guard` ne trouve pas le package, exécuter une fois :
```powershell
$env:PYTHONPATH = "$PWD\src"
```
Puis relancer les commandes. Ou utiliser les scripts `scripts\init_database.py`, `scripts\generate_demo_data.py`, etc.

## 4. Connexion à SQL Server / PERFECT VISION

L'extracteur `src/fin_guard/sqlserver.py` est adapté au schéma métier communiqué pour PERFECT VISION. Il lit les opérations depuis `OPERATION`, joint `COMPTES`, `SOLDPERI`, `ADHERENT`, `AGENCE`, `CAIS_AGENCE` et `CAISSE`, puis applique les filtres suivants :

- `COMPTES.REF_CPTE IN ('E', 'A')` ;
- `OPERATION.COD_TYPE_OPERAT IN ('DEPO', 'RETR')` ;
- `COMPTES.NUM_CPTE LIKE '2512%'` par défaut ;
- période facultative : date de début incluse et date de fin exclusive.

La période de `SOLDPERI` est rapprochée de la période `YYYYMM` calculée à partir de `OPERATION.DATE_OPERATION`. L'identifiant adhérent est extrait de `COMPTES.NUM_CPTE` conformément à la règle `SUBSTRING(NUM_CPTE, 9, LEN(NUM_CPTE)-10)`.

### Paramétrage

1. Installer le pilote ODBC SQL Server adapté à Python (64 bits recommandé).
2. Copier `.env.example` vers `.env` et renseigner les paramètres SQL Server.
3. Utiliser un compte SQL Server dédié, en lecture seule, limité aux tables nécessaires.
4. Ne jamais partager le fichier `.env` : il contient les identifiants de connexion.

Exemple de configuration :

```ini
SQLSERVER_ENABLED=true
SQLSERVER_HOST=localhost
SQLSERVER_PORT=1433
SQLSERVER_DATABASE=PERFECT_VISION
SQLSERVER_SCHEMA=dbo
SQLSERVER_USERNAME=utilisateur_lecture_seule
SQLSERVER_PASSWORD=mot_de_passe_local
SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
SQLSERVER_TRUST_CERTIFICATE=true
```

### Synchronisation et journalisation

Depuis Streamlit, ouvrir **Synchronisation PERFECT VISION**, sélectionner les dates et lancer la synchronisation. Le traitement :

1. compte les lignes source correspondant aux filtres avant la limite d'extraction ;
2. extrait les opérations PERFECT VISION ;
3. mappe les champs source vers les champs canoniques FIN-GUARD AI ;
4. rejette les lignes dont la date, le montant ou le compte sont invalides ;
5. détecte les doublons du lot et ceux déjà enregistrés dans SQLite ;
6. importe les nouvelles transactions et met à jour le journal.

Le journal persistant est conservé dans la table SQLite `extraction_logs`, dans `data/fin_guard_ai.db`. Il mémorise la date de synchronisation, le nombre de lignes source, le nombre importé, les doublons détectés, les lignes rejetées, le statut et le détail du traitement. Les statuts utilisés sont `EN_COURS`, `TERMINE`, `PARTIEL` et `ERREUR`.

Pour lancer une synchronisation depuis PowerShell :

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts\sync_perfect_vision.py
```

Les identifiants techniques de transaction PERFECT VISION n'étant pas explicitement fournis dans le dictionnaire de données, le prototype construit une empreinte stable à partir de `NUM_TRANS`, `NUM_CPTE`, `DATE_OPERATION`, `MONTANT`, `COD_TYPE_OPERAT`, `COD_MODE_PAIE` et `KP_CAIS_AGENCE`. Cette empreinte évite les réimports courants, mais elle doit être remplacée par la véritable clé primaire de `OPERATION` dès que celle-ci est identifiée et vérifiée. Deux opérations parfaitement identiques sur ces champs pourraient sinon être considérées comme un doublon.

## 5. Format canonique d'import CSV

Colonnes attendues au minimum :
- `transaction_date`
- `amount`

Colonnes recommandées :
- `source_transaction_id`, `account_number`, `customer_id`, `branch_code`
- `transaction_type`, `channel`, `currency`, `balance_after`, `description`

Le chargement CSV est disponible dans l'onglet **Transactions**.

## 6. Méthode VAE

- Encodeur : représentation latente `mu` et `log_var`.
- Reparamétrisation : échantillonnage latent.
- Décodeur : reconstruction des caractéristiques.
- Fonction de coût : erreur de reconstruction + `beta × KL`.
- Découpage temporel entraînement/validation.
- Normalisation apprise uniquement sur l'ensemble d'entraînement.
- Score d'anomalie : combinaison erreur de reconstruction et divergence KL, puis rang percentile empirique.

Le percentile est un **score de classement** sur le lot analysé, non une probabilité de fraude. Les seuils 95e et 99e percentiles sont des seuils de tri initiaux, à calibrer sur les données de l'institution et le coût des faux positifs.

Pour un vrai déploiement, entraîner sur un historique contrôlé et représentatif, idéalement après revue/étiquetage d'un échantillon par les métiers. La présence d'opérations frauduleuses dans les données d'entraînement peut dégrader la détection.

## 7. SQLite et persistance

La base `data/fin_guard_ai.db` conserve transactions, scores, modèles, historique d'entraînement, investigations, notes, explications, alertes, journaux d'audit, journal détaillé des extractions PERFECT VISION (`extraction_logs`), synchronisations, rapports et qualifications humaines. Les poids VAE et scalers sont dans `models/`; les chemins et versions sont enregistrés dans SQLite.

Sauvegarde : arrêter l'application puis copier `data/fin_guard_ai.db` et le dossier `models/`. Protéger les sauvegardes par chiffrement et contrôle d'accès.

## 8. Tests

```powershell
$env:PYTHONPATH = "$PWD\src"
pytest -q
```

## 9. Limites et étapes avant production

- Adapter le mapping SQL à votre dictionnaire PERFECT VISION réel.
- Ajouter authentification/autorisation robuste (les rôles sont définis dans le cahier des charges, mais pas encore implémentés dans ce MVP).
- Renforcer chiffrement, gestion des secrets, sauvegardes, supervision et journalisation.
- Calibrer les seuils avec des cas confirmés et mesurer précision, rappel, PR-AUC, taux de faux positifs et temps de traitement.
- Ajouter une stratégie de validation humaine et de gestion des alertes.
- L'assistant d'explication actuel est déterministe et local. Aucun LLM externe n'est appelé par défaut.
- Les caractéristiques temporelles sont une première base. Une fenêtre glissante exacte et un calcul de fréquence par client/compte devront être optimisés pour les gros volumes.

## 10. Démarrage rapide

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts\init_database.py
python scripts\generate_demo_data.py
python scripts\train_model.py
python scripts\run_detection.py
streamlit run app.py
```


## Règles de surveillance des montants élevés

FIN-GUARD AI applique deux règles métier complémentaires lors de chaque
synchronisation PERFECT VISION :

1. **Montant individuel :** une opération est signalée lorsque `MONTANT >=
   15 000 000 XOF`.
2. **Cumul par adhérent :** toutes les opérations d'un adhérent sur la période
   d'extraction sont signalées lorsque la somme de leurs `MONTANT` atteint
   `15 000 000 XOF` ou plus.

Le cumul par adhérent est calculé directement dans SQL Server sur toute la
période demandée, indépendamment de la limite `TOP` utilisée pour extraire les
lignes détaillées. L'identifiant adhérent est déterminé à partir de
`SUBSTRING(COMPTES.NUM_CPTE, 9, LEN(NUM_CPTE)-10)`, conformément à la relation
avec `ADHERENT.COD_ADH`.

Les alertes sont conservées dans la table SQLite `threshold_alerts` et
consultables dans la page **Alertes seuil 15 millions**. Les alertes de seuil
sont des signaux de contrôle et non une preuve de fraude.

Le seuil est configurable dans `.env` :

```ini
FIN_GUARD_THRESHOLD_XOF=15000000
```

Le cumul correspond à la somme arithmétique de `MONTANT` (sans valeur absolue).
Il faut donc vérifier la convention de signe des dépôts et retraits dans
PERFECT VISION avant toute interprétation comptable du cumul.
