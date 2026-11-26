# Plan d’implémentation et suivi — synchronisation COROS

**Créé le :** 17 novembre 2026  
**Référence :** [spécification fonctionnelle v1.8](coros-sync-functional-specification.md)  
**État :** L1 livré. L2 à L8 sont implémentés. Le parcours complet et la restauration sont validés hors ligne sous Linux. Le guide d’exploitation est disponible. La connexion de production, la reprise sans invite et le renouvellement sont validés sur Linux et Windows. Le changement de compte, les frontières de minuit et la restauration sur une autre machine restent ouverts. L0 reste partiellement qualifié.

Ce document détaille les quatre étapes de livraison de la section 15 de la
spécification en lots intégrables et vérifiables. La spécification reste la
référence fonctionnelle. Une case cochée exige un livrable et une preuve de
validation ; la présence de code expérimental ne suffit pas à terminer un lot.

## 1. Point de départ et règles de suivi

Le dépôt contient un prototype séparé `fit-coros-probe`, ses ports applicatifs,
son modèle de connexion, ses adaptateurs MCP/OAuth et trousseau, et des tests
hors ligne. La spécification rapporte une connexion, une reprise, un
renouvellement et la conversion d’un FIT réels réussis sur Linux/Europe. Le
découpage d’une plage saturée retrouve le nombre attendu sur un échantillon.
Ces acquis seront réutilisés après revue ; ils ne constituent pas encore une
synchronisation de production ni une validation Windows.

Pour chaque lot, renseigner le responsable, le lien vers la PR ou le commit,
les résultats de tests et les décisions prises. États : **à faire**, **en cours**,
**bloqué**, **terminé**. Une dépendance doit être terminée avant intégration du
parcours qui en dépend ; les tests avec ports simulés peuvent être préparés avant.

| Lot | Livrable principal | Dépendances | État | Responsable / PR / preuve |
| --- | --- | --- | --- | --- |
| L0 | Décisions techniques et contrats encore ouverts | Aucune | En cours | [Note L0](coros-sync-technical-decisions.md) ; preuves réelles partielles, minuit non qualifié |
| L1 | Contexte Synchronisation et règles de sélection | L0 : contrats internes | Terminé | [Validation L1](#l1--modèle-métier-périodes-et-sélection) |
| L2 | Connexion de production et identité du compte | L0 : identité, dépendances | En cours | [Connexion, reprise et renouvellement validés](#l2--connexion-de-production-et-compte). Changement de compte à vérifier. |
| L3 | Registre, fichiers et verrouillage | L1, L2 : identité | En cours | [Validation L3](#l3--persistance-intégrité-des-fichiers-et-exclusion-concurrente) sous Linux. CI Windows à vérifier. |
| L4 | Quota partagé entre dossiers | L2, L3, décision de récupération L0 | Terminé hors ligne | [Validation L4](#l4--budget-fit-partagé-et-compteurs) sous Linux |
| L5 | Catalogue et prévisualisation du plan de synchronisation | L1, L2, validation des dates L0 | Terminé hors ligne | [Validation L5](#l5--découverte-et-prévisualisation) sous Linux |
| L6 | Acquisition bornée et téléchargement seul | L3, L4, L5 | Terminé hors ligne | [Validation L6](#l6--acquisition-bornée-et-téléchargement-seul) sous Linux |
| L7 | Incrémental, reprise et réconciliation | L6 | Terminé hors ligne | [Validation L7](#l7--référence-incrémentale-et-reprise-après-interruption) sous Linux |
| L8 | Conversion et parcours CLI complet | L7 | Terminé hors ligne | [Validation L8](#l8--conversion-configuration-et-restitution-cli) sous Linux |
| L9 | Qualification Linux/Windows et documentation | L8 | En cours | [Guide d’exploitation](coros-sync-guide.md) et [preuves Linux et Windows](#preuves-hors-ligne-et-qualification-restante) |

**Jalons de livraison :**

- [ ] J0 — Faisabilité consolidée et décisions bloquantes levées : L0.
- [ ] J1 — Acquisition bornée utilisable avec quota et persistance : L1 à L6.
- [x] J2 — Synchronisation incrémentale et reprise fiables : L7, validé hors ligne sous Linux.
- [ ] J3 — V1 complète et qualifiée sur Linux/Windows : L8 et L9.

L1 et L2 peuvent avancer indépendamment une fois leurs contrats fixés. L5 peut
être développé avec un faux registre pendant L3/L4. Aucune demande FIT de
production n’est activée avant le quota partagé et la finalisation des fichiers.

## 2. Architecture cible et exigences communes

- Créer un contexte métier `domain/synchronization/` pour les identités distantes,
  périodes, références, états et décisions de reprise. Il reste indépendant de
  MCP, OAuth, HTTP, SQLite et des modèles Activity/Reporting.
- Placer l’orchestration dans la couche application, derrière des ports typés de
  catalogue, acquisition, registre, quota, fichiers, secrets, horloge et fuseau.
  Le découpage précis des ports est consigné en L0/L1.
- Garder les traductions MCP/OAuth, le trousseau, SQLite et les opérations système
  dans les adaptateurs d’infrastructure. La CLI assure assemblage et présentation.
- Appeler `GenerateMarkdownReport` depuis l’application avec un FIT local et les
  options existantes ; ne pas faire dépendre Activity ou Reporting de COROS.
- Étendre `tests/unit/domain/test_architecture.py` et
  [le guide d’architecture](architecture.md) pour expliciter ces frontières.

**Définition de terminé applicable à chaque lot de code :**

- [ ] Tests unitaires dans `tests/`, avec réussite et échec pour chaque nouveau module.
- [ ] Interactions externes simulées ; horloge, fuseau et réponses contrôlables.
- [ ] Tests lecture/écriture pour toute évolution du schéma ; rollback si pertinent.
- [ ] Nouveaux contrats publics typés et frontières DDD vérifiées.
- [ ] `pytest` réussi avec le seuil de couverture du dépôt (80 %).
- [ ] Contrôles Ruff, formatage et mypy réussis via `./ci.sh`.
- [ ] `changelog.md`, documentation et tableau de suivi actualisés.

Les essais réels explicitement décrits ci-dessous sont des preuves de
qualification séparées. Aucun test obligatoire ne dépend d’un compte COROS ou
du réseau ; aucune donnée personnelle, URL privée ou jeton ne rejoint le dépôt.

## 3. Lots détaillés

### L0 — Consolider les contrats et lever les décisions bloquantes

**But :** réutiliser les preuves du prototype et définir ce qui manque avant de
coder chaque parcours concerné (spécification §§14.1–14.5).

- [x] Consigner dans une note de conception les preuves acquises et leurs limites.
- [x] Vérifier une identité de compte opaque et stable après reconnexion ; arrêter
  la clé fournisseur/compte/activité/sport et le refus d’un autre compte dans un output.
- [x] Vérifier sur un échantillon les timestamps en secondes, les horaires UTC/locaux,
  le jour attendu et l’invariance des instants entre fuseaux.
- [ ] Compléter la qualification des bornes inclusives et du référentiel des jours
  COROS près de minuit ; enveloppe candidate couvrant `[début, T)` définie dans la note.
- [ ] Comparer l’empreinte avec celle d’un autre compte autorisé ; séparation
  effective des outputs à implémenter et tester en L2/L3.
- [x] Choisir la résolution du fuseau local avec règles DST sur Linux/Windows,
  le verrou OS sans attente et les dépendances minimales MCP/OAuth/trousseau.
- [x] Arrêter la récupération du registre de quota perdu/inaccessible, sans
  création implicite d’un budget neuf. Décision L0 : refuser toute
  acquisition jusqu’à restauration d’un registre fiable.
- [x] Préciser la réconciliation d’une conversion manuelle après
  `conversion_unsupported`. Décision L0 : état terminal conservé,
  sans reconnaissance automatique du rapport manuel en V1.
- [x] Fixer la langue des messages et l’emplacement du registre de quota commun
  au compte sur la machine ; définir les informations de liaison dans le trousseau.
- [x] Définir délais réseau et tentatives bornées du catalogue ; aucune relance
  automatique des demandes FIT. Documenter le recours aux URL seulement si nécessaire.
- [x] Consigner les vérifications de conditions d’usage encore nécessaires.

**Livraison partielle L0 :** [note technique](coros-sync-technical-decisions.md),
commande expérimentale `identity`, option `activities --inspect-dates`, tests
synthétiques des destinations, de la rotation, des dates et de la présentation.
Les propositions de récupération et de conversion manuelle sont validées :
refus d’acquisition jusqu’à restauration fiable et état terminal sans
réconciliation automatique. L’utilisateur confirme le 17 novembre 2026 la
stabilité de l’identité après renouvellement/reconnexion, la concordance des
horaires, le jour attendu et l’invariance UTC entre fuseaux sur Linux/Europe.
Sans activité proche de minuit, cette frontière reste ouverte ; comparaison
avec un autre compte non rapportée. J0 n’est pas atteint. Les ports de production seront introduits avec leurs lots
consommateurs, suivant les responsabilités définies dans la note.

**Preuve hors ligne (17 novembre 2026, Linux) :** `./ci.sh` réussi : Ruff,
formatage, mypy et 697 tests ; couverture 91,57 % (seuil 80 %).
Aucun appel COROS réel dans cette validation. Aucun essai Windows exécuté.

**Sortie :** note technique liée ici et décisions fonctionnelles reportées dans
la spécification si elles la complètent. Les garanties serveur non démontrées
restent explicites ; le garde-fou local de quota permet d’avancer sans connaître
la fenêtre serveur exacte. Une identité ou une traduction de dates non établies
bloquent les lots concernés.

### L1 — Modèle métier, périodes et sélection

**But :** rendre testables les règles de synchronisation sans infrastructure.

- [x] Définir identités, activité distante, période, référence UTC, états séparés
  d’acquisition/conversion, erreurs typées et résultats de planification.
- [x] Figer T et le fuseau pour une exécution ; calculer `--since`, `--last-days`,
  et la reprise au début du jour local contenant le repère UTC.
- [x] Appliquer `[début, T)` au début de l’activité et signaler les dates absentes.
- [x] Distinguer première utilisation, période explicite ultérieure et
  `--init-coros-reference` ; formaliser les invariants d’avancement du repère.
- [x] Formaliser la sélection des travaux en attente : toutes dates en incrémental,
  fenêtre courante en explicite, exclusion des conversions non prises en charge.
- [x] Fournir les ports d’horloge et de fuseau avec des fakes, et les contrats de
  données normalisées du catalogue et du registre.

**Validation hors ligne (17 novembre 2026, Linux).** `./ci.sh` réussi avec Ruff,
formatage, mypy et 757 tests. Couverture globale de 92,03 % pour un seuil de 80 %.
Les tests couvrent les [périodes et changements de fuseau](../tests/unit/domain/synchronization/test_periods.py),
la [sélection et les décisions sur le repère](../tests/unit/domain/synchronization/test_planning.py),
les [ports temporels](../tests/unit/application/test_synchronization.py) et les
[frontières DDD](../tests/unit/domain/test_architecture.py).

**Décisions.** Un minuit local ambigu est résolu à sa première occurrence.
Un minuit inexistant ou hors plage déclenche une erreur typée. Une activité sans
date de début exploitable rend la découverte incomplète et bloque l’avancement
du repère. La qualification réelle des requêtes aux frontières de jours relève de L5.

**AC :** 01–05, 08–10, 23, 30, 31, 36 (règles métier).

### L2 — Connexion de production et compte

**But :** exposer une session utilisable par la synchronisation sans interaction.

- [x] Revoir puis adapter le prototype aux ports de production ; définir le sort
  de `fit-coros-probe` et la séparation de ses sessions expérimentales.
- [x] Implémenter les cas d’usage login, état local et logout ; confirmer le compte
  et sa région, sans prendre le nom de profil ou le jeton comme identité.
- [x] Persister les rotations avant réutilisation ; gérer expiration/révocation,
  trousseau absent/verrouillé et erreurs OAuth sans fuite de secrets.
- [x] Préserver login comme seul parcours navigateur ; la reprise échoue avec une
  instruction de reconnexion si nécessaire. Logout conserve les données locales.
- [ ] Vérifier les limites de stockage Credential Manager avec des jetons réels
  avant validation Windows ; ne jamais proposer un secours en clair.

**Validation hors ligne (17 novembre 2026, Linux).** `./ci.sh` réussi avec Ruff,
formatage, mypy et 806 tests. Couverture globale de 92,67 % pour un seuil de 80 %.
Les tests simulent OAuth, le trousseau, l’horloge et deux comptes. Un essai local
avec `portalocker` vérifie aussi le verrou entre deux processus.
[Tests de connexion](../tests/unit/test_coros_connection_production.py),
[tests de trousseau](../tests/unit/infrastructure/test_coros_vault.py),
[tests de verrouillage](../tests/unit/infrastructure/test_coros_lock.py)

**AC :** 20, 28, 39 (connexion).

**Protocole de qualification réelle avec un compte autorisé sur Linux et Windows.**

1. Installer `python -m pip install '.[coros]'`. Exécuter `fit-to-md coros login`
   avec la région du compte, fermer le terminal, puis exécuter `fit-to-md coros status`
   dans une nouvelle session. Vérifier la région et la disponibilité locale.
2. Dans le même environnement Python, utiliser le script ci-dessous dans trois
   nouveaux processus, d’abord sans argument, puis avec `--refresh`, puis sans
   argument. Aucun de ces appels ne doit ouvrir le navigateur ni demander le
   déverrouillage. Les trois empreintes doivent correspondre et la deuxième
   exécution doit enregistrer le jeton de renouvellement remplacé. Sous Windows,
   relever la taille UTF-16 de la session reconstituée. Au-delà de 2 560 octets,
   elle est répartie entre plusieurs entrées du Gestionnaire d’identifiants.
   Vérifier que la reprise et la rotation réussissent même au-delà de cette
   taille, puis que `logout` supprime les entrées de session.
3. Avec un second compte autorisé de la même région, vérifier qu’un nouveau
   `login` avant `logout` est refusé et que la première session reste utilisable.
   Faire `logout`, vérifier que `status` indique l’absence de session, puis
   connecter le second compte. Vérifier que son empreinte est différente.
   Consigner uniquement les résultats et les tailles. Garder jetons, sujets,
   empreintes et URL d’autorisation hors du dépôt.

```python
import hashlib
import json
import sys
import time

from fit_to_md.application.coros_connection import CorosSessionManager
from fit_to_md.infrastructure.coros_gateway import CorosGateway, HttpsTransport
from fit_to_md.infrastructure.coros_lock import SessionFileLock
from fit_to_md.infrastructure.coros_vault import VaultConnectionStore, system_backend

backend = system_backend(extra="coros")
store = VaultConnectionStore(backend)
previous = store.load()
assert previous is not None
now = time.time
if sys.argv[1:] == ["--refresh"]:
    now = lambda: max(time.time(), previous.session.expires_at - 60)

def gateway(region: str) -> CorosGateway:
    return CorosGateway(
        region, HttpsTransport(), now=time.time,
        monotonic=time.monotonic, sleep=time.sleep, client_name="FitToMd",
    )

account, session = CorosSessionManager(gateway, store, SessionFileLock(), now).resume()
print(hashlib.sha256(json.dumps([account.issuer, account.subject]).encode()).hexdigest())
if sys.argv[1:] == ["--refresh"]:
    assert session.refresh_token != previous.session.refresh_token
    assert session.expires_at > time.time()
    print("Session renewed")
if sys.platform == "win32":
    value = backend.get_password("fit-to-md/coros/connection", "session")
    assert value is not None
    print(f"Reassembled session size: {len(value.encode('utf-16-le'))} bytes")
```

### L3 — Persistance, intégrité des fichiers et exclusion concurrente

**But :** disposer d’un stockage fiable avant toute acquisition de production.

- [x] Créer le schéma SQLite versionné : compte, référence, candidats, chemins,
  taille/empreinte FIT, états, options de conversion, erreurs et tentatives.
- [x] Tester transactions courtes, sauvegarde avant migration, rollback et refus
  des versions inconnues ou registres illisibles sans réinitialisation.
- [x] Associer un output existant à un seul compte ; créer uniquement ses
  sous-dossiers requis et ne jamais importer automatiquement les anciens FIT.
- [x] Implémenter le verrou système du dossier sans attente, libéré après crash.
- [x] Réserver le chemin avant acquisition ; écrire les temporaires sur le même
  système de fichiers puis finaliser sans écraser un fichier existant non associé.
- [x] Mémoriser le nom à la minute au premier téléchargement ; conserver ce chemin
  après changement de fuseau ; appliquer les mêmes garanties aux rapports.
- [x] Préparer la lecture seule du registre pour dry-run, sans création de base,
  migration ni création de suivi lorsqu’il n’existe pas.

**Validation :** `./ci.sh` réussit sous Linux avec 844 tests et 92,41 % de couverture.
Les [tests du registre et du verrou](../tests/unit/infrastructure/synchronization/test_output.py)
et les [tests de publication](../tests/unit/infrastructure/synchronization/test_files.py)
couvrent le stockage et ses échecs sous Linux. Le scénario à deux processus
vérifie le refus en écriture et en dry-run, puis la libération après arrêt forcé.
La CI Windows exécute la même suite sur un volume local. Son résultat reste à
confirmer. La migration utilise une version antérieure simulée dans les tests.

Le dry-run ouvre le verrou existant sans le créer et refuse immédiatement un
dossier occupé. La publication vise les volumes locaux compatibles avec le
renommage atomique sans remplacement. Le suivi L7 exploitera l’empreinte FIT
enregistrée avant publication pour réconcilier un arrêt entre renommage et commit.

**AC :** 12, 24–26, 38, 39 (stockage).

### L4 — Budget FIT partagé et compteurs

**But :** garantir le plafond local même avec plusieurs outputs simultanés.

- [x] Implémenter le registre SQLite de quota commun au compte ; conserver son
  chemin dans le trousseau et le réutiliser depuis tout nouveau dossier.
- [x] Réserver transactionnellement une unité avant chaque demande FIT ; exclure
  les FIT locaux valides avant réservation. Garder les réponses perdues comptées.
- [x] Limiter à 50 unités sur 24 heures glissantes UTC ; maintenir séparément
  les compteurs par jour et par type d’appel pour le bilan.
- [x] Tester les réservations concurrentes entre outputs : le verrou d’un dossier
  ne remplace pas l’atomicité du compteur commun.
- [x] Appliquer la politique L0 en cas de compteur perdu/inaccessible et le refus
  serveur prioritaire ; exposer une éventuelle échéance sans attente prolongée.

**Validation :** `./ci.sh` réussit sous Linux avec 885 tests et 92,66 % de
couverture. Les [tests du quota](../tests/unit/infrastructure/synchronization/test_quota_storage.py)
couvrent le seuil exact, minuit local, le changement d’heure, le redémarrage,
la concurrence entre deux processus et outputs, le timeout, l’initialisation
interrompue et la perte du registre ou de sa liaison. Le parcours avec transport
simulé vérifie la réutilisation locale et le refus HTTP 429 avec échéance.
`coros init-quota` initialise le quota explicitement. Le lot 6 raccorde le
quota à `sync --download-only`. La validation Windows reste ouverte et le
signal métier MCP de quota reste à qualifier.

**AC :** 18, 32, 33 (budget).

### L5 — Découverte et prévisualisation

**But :** produire un plan déterministe et qualifier sa complétude.

- [x] Traduire les réponses reconnues du prototype en contrats internes ;
  distinguer catalogue vide, réponse inconnue, erreur HTTP/MCP/métier.
- [x] Envoyer des dates COROS explicites couvrant la fenêtre locale puis filtrer
  précisément les instants ; gérer les activités impossibles à classer.
- [x] Découper les plages saturées en jours disjoints ; remplacer le parent par
  ses enfants, vérifier les bornes et dédupliquer les identités.
- [x] Marquer une journée saturée sans continuation, des bornes incohérentes ou
  une réponse inexploitable comme découverte incomplète, sans avancer le repère.
- [x] Planifier connus, nouveaux, reprises et conflits du plus ancien au plus
  récent avec départage stable ; borner les tentatives du catalogue.
- [x] Implémenter dry-run avec/sans registre : métadonnées et renouvellement
  possibles, aucun FIT/URL, aucun changement des activités ou références.

**Validation :** `./ci.sh` réussit sous Linux avec 951 tests et 92,91 % de
couverture. Les [tests de découverte](../tests/unit/application/test_synchronization_discovery.py)
couvrent le découpage, les doublons, les bornes et les résultats partiels.
Les [tests de transport](../tests/unit/infrastructure/test_coros_catalogue.py)
couvrent les formats reconnus, les erreurs et les relances bornées. Les
[tests CLI](../tests/unit/cli/test_coros_preview.py) vérifient les appels de
métadonnées et l’intégrité du registre SQLite et des fichiers avec un plan
complet ou partiel, ainsi que l’absence de création dans un dossier vierge.

`coros sync --dry-run` utilise l’enveloppe de dates candidate. Un catalogue
vide avec bornes confirmées termine avec succès. Une réponse vide dont les
bornes ne sont pas confirmées et une journée encore saturée donnent un plan
provisoire. La vérification réelle autour de minuit reste ouverte.

**AC :** 02–04, 11, 21–23, 27 (catalogue).

### L6 — Acquisition bornée et téléchargement seul

**But :** livrer `sync --since/--last-days --download-only` avec les garde-fous.

- [x] Enregistrer les candidats avant traitement et intégrer découverte, fichiers,
  registre, compte et budget ; ne pas annoncer un plan complet en cas d’incomplétude.
- [x] Acquérir par identité, une activité par demande ; valider association,
  structure et intégrité du FIT avant finalisation et mise à jour du registre.
- [x] Distinguer FIT invalide et FIT valide non convertible ; ne pas perdre un
  téléchargement à cause d’une limite du convertisseur.
- [x] Garder le transport direct si suffisant ; si des URL sont nécessaires,
  traiter expiration/redirections sans exposer les URL ni transmettre le jeton MCP.
- [x] Poursuivre après erreur individuelle récupérable ; arrêter les appels en
  cas de révocation et les mutations en cas d’échec du stockage.
- [x] À quota épuisé, conserver les candidats et arrêter les acquisitions ; aucune
  nouvelle tentative FIT automatique après échec dans la même commande.
- [x] Brancher le parcours CLI borné avec validation préalable des options/output
  et sans modifier la syntaxe des conversions locales.

**Validation hors ligne sous Linux (21 novembre 2026).** `./ci.sh` réussit
avec Ruff, le contrôle du formatage, mypy et 985 tests. La couverture atteint
92,97 % pour un seuil de 80 %. Les [tests du parcours CLI](../tests/unit/cli/test_coros_download.py)
couvrent le flux catalogue → FIT → disque → registre, les relances sans doublon,
la révocation, le quota partagé, les échecs individuels, le stockage défaillant
et le code 130. Les [tests d’orchestration](../tests/unit/application/test_synchronization_acquisition.py)
vérifient l’ordre des opérations et l’arrêt sur erreur du registre.

Un catalogue incomplet conserve les candidats datés pour une relance et ne
déclenche aucune demande FIT. Le téléchargement direct est le seul transport
utilisé. Après une interruption entre renommage et confirmation, le fichier et
son empreinte restent disponibles pour la réconciliation du lot 7. La
qualification réelle de COROS et la validation Windows restent ouvertes.

**AC :** 05–07, 12–14, 18–21, 28, 35 (acquisition).

### L7 — Référence incrémentale et reprise après interruption

**But :** rendre les commandes relançables indépendamment du succès des fichiers.

- [x] Lors de la première période explicite, enregistrer la borne de départ après
  validation/authentification ; la conserver si la découverte échoue.
- [x] Après découverte complète, enregistrer les candidats et avancer le repère
  vers T dans une même transaction, avant les traitements individuels.
- [x] Ne pas changer un repère existant lors d’une période explicite ultérieure ;
  implémenter le repositionnement sans acquisition et sans réinitialiser le quota.
- [x] Reprendre les travaux en attente selon le mode, même hors fenêtre en
  incrémental ; conserver les conversions non prises en charge comme terminales.
- [x] Réconcilier le FIT final présent après crash avant toute nouvelle demande ;
  rejeter les temporaires, réacquérir les FIT manquants/corrompus.
- [x] Tester les interruptions avant/après renommage, commit des candidats et
  avancement du repère ; conserver les résultats déjà finalisés.

**Validation hors ligne sous Linux.** `./ci.sh` réussit avec Ruff, mypy et
1 003 tests. La couverture atteint 93,07 %. Les
[tests CLI](../tests/unit/cli/test_coros_download.py)
couvrent l’initialisation, le catalogue incomplet, l’avancement malgré une
acquisition partielle, les imports tardifs, le changement de fuseau et le
repositionnement avec conservation du quota. Ils simulent aussi les interruptions
de transaction, le journal SQLite laissé par un processus arrêté et la reprise
avant ou après publication d’un FIT. Un FIT endommagé déjà confirmé est
archivé dans `.fit-to-md/damaged-fits/` avant réacquisition. La qualification
réelle de COROS et la validation Windows restent ouvertes.

**AC :** 06–10, 13–17, 23, 27, 30–33, 36, 38 (reprise).

### L8 — Conversion, configuration et restitution CLI

**But :** livrer le parcours normal avec rapports et bilan exact.

- [x] Convertir depuis le FIT local avec `GenerateMarkdownReport` et les réglages
  existants : config, zones cardiaques, tours et enrichissements.
- [x] Persister séparément les succès/échecs de conversion et les options ;
  régénérer un rapport absent, sans retélécharger un FIT valide.
- [x] Après téléchargement seul, créer les rapports à la relance normale ; au
  quota, continuer la conversion des FIT disponibles.
- [x] Marquer `conversion_unsupported` terminal ; appliquer la décision L0 sur la
  conversion manuelle. Un enrichissement dégradé conserve le succès du rapport.
- [x] Préserver les rapports existants/édités et ignorer les révisions distantes ;
  refuser les collisions, sans suffixe ni régénération lors d’un changement d’options.
- [x] Finaliser login/status/logout/sync : état local distingué d’une connexion
  vérifiée, chemins, référence, fuseau et travaux en attente.
- [x] Valider conflits d’options, dates, output CLI/config et priorité CLI avant
  réseau/mutation ; conserver les périodes explicites en CLI uniquement.
- [x] Préserver `fit-to-md activity.fit`, le mode dossier hors ligne et `./coros/`.
- [x] Restituer période, fuseau, complétude, compteurs séparés et reports pour quota
  sur stdout ; progression/diagnostics sur stderr ; codes 0/1/2/130 conformes.

**Validation :** `./ci.sh` réussit sous Linux avec 1 023 tests et 93,14 % de couverture.
Les [tests CLI](../tests/unit/cli/test_coros_download.py) couvrent la conversion,
les reprises, les collisions, le quota, les sorties et les réglages partagés.
Les [tests du registre](../tests/unit/infrastructure/synchronization/test_output.py)
vérifient la lecture des options antérieures. Si le processus s’interrompt
entre la publication d’un rapport et son enregistrement, la relance signale
un conflit et conserve le fichier.
**AC :** 01, 05–07, 12, 15–22, 26, 28–31, 34–37.

### L9 — Qualification et livraison

**But :** clôturer la V1 avec preuves traçables et documentation exploitable.

- [x] Exécuter `./ci.sh` et vérifier les scénarios hors ligne sous Linux (DST,
  chemins, trousseau simulé, concurrence et interruption).
- [x] Exécuter les scénarios hors ligne sous Windows.
- [x] Qualifier sous Windows le login réel, la reprise sans invite et le
  renouvellement de la session de production avec le trousseau système.
- [x] Qualifier sous Linux le login réel, la reprise sans invite et le
  renouvellement de la session de production avec le trousseau système.
- [ ] Vérifier la séparation après reconnexion avec un autre compte sur les deux
  OS. Essai en attente d’un second compte autorisé.
- [x] Comparer localement le FIT témoin à l’export Training Hub.
- [ ] Comparer les ensembles d’identités sur d’autres plages lorsque possible.
- [ ] Vérifier les frontières dates/fuseaux contre Training Hub. Essai en attente
  d’activités proches de minuit.
- [x] Vérifier hors ligne le refus d’une journée saturée.
- [x] Documenter installation/dépendances, commandes, codes de sortie, budget,
  imports tardifs, limites du catalogue et erreurs de connexion.
- [x] Documenter la sauvegarde et la restauration à l’arrêt, la reconnexion sur
  une autre machine et la récupération du quota. Vérifier sur une copie sous
  Linux et relier les tests de migration et de perte des registres.
- [x] Mettre à jour README, architecture, changelog et statut de la spécification.
- [ ] Vérifier la restauration sur une autre machine, avec reconnexion et
  récupération de la liaison de quota via le trousseau système.
- [ ] Rassembler les preuves de qualification restantes avant de cocher J3.

#### Preuves hors ligne et qualification restante

- Linux, Python 3.12, le 22 novembre 2026. `./ci.sh` réussit avec 1 023 tests et
  93,14 % de couverture. Les tests couvrent les [périodes et le DST](../tests/unit/domain/synchronization/test_periods.py),
  la [saturation](../tests/unit/application/test_synchronization_discovery.py),
  les [trousseaux simulés](../tests/unit/infrastructure/test_coros_vault.py),
  l’[exclusion interprocessus et la migration](../tests/unit/infrastructure/synchronization/test_output.py)
  ainsi que la [reprise après interruption](../tests/unit/cli/test_coros_download.py).
- Essai ponctuel de restauration (AC‑38), sous Linux avec transport et
  trousseau simulés. Après une synchronisation partielle, le dossier complet a
  été copié à l’arrêt puis restauré ailleurs sur la même machine. `status` a
  retrouvé la référence UTC, le fuseau et un FIT et un rapport en attente. Le
  FIT acquis et le rapport édité ont conservé leur contenu. Après suppression
  de la base de quota temporaire, `sync` a échoué avant tout appel distant.
  Après restauration de la base à son emplacement lié, le compteur a été
  conservé et seule l’activité en attente a été téléchargée et convertie.
  Les tests automatisés couvrent le rollback de migration, le journal SQLite
  interrompu et le [refus d’un quota perdu ou incohérent](../tests/unit/infrastructure/synchronization/test_quota_storage.py).
- Windows (AC‑39). Le [workflow CI](../.github/workflows/ci.yml) prévoit des
  tests de nommage, registre, publication et concurrence. Les essais ciblés sur
  le correctif couvrent aussi les périodes et le DST, le trousseau simulé et
  les interruptions CLI. Le prototype L0 apporte une preuve partielle sous Linux.
  Le 26 novembre 2026, les scénarios hors ligne Windows ont été signalés réussis.
  Le premier login réel a échoué car la session dépassait 2 560 octets.
  La session peut désormais être répartie entre plusieurs entrées du trousseau.
  Après correction, l’utilisateur a confirmé sous Windows le login réel, la
  reprise sans invite et le renouvellement forcé de la session de production.
  L’utilisateur a également confirmé que ces essais avaient déjà été réalisés
  avec la CLI de production sous Linux. La séparation après changement de compte
  reste à vérifier sur un second compte autorisé.
- Training Hub. L’utilisateur a confirmé la comparaison locale d’un FIT témoin
  avec l’export de la même activité. La comparaison des ensembles d’activités
  sur d’autres plages reste à documenter. Les bornes de dates dans deux fuseaux
  restent à vérifier lorsque des activités proches de minuit seront disponibles.
  Consigner les résultats anonymisés selon le [protocole L0](coros-sync-technical-decisions.md).

**Sortie :** V1 livrable sur Linux et Windows. Les limites serveur non établies
restent documentées et couvertes par les comportements conservateurs validés.
**AC :** 01–39 en régression, avec qualification spécifique de 38 et 39.

## 4. Matrice de couverture des critères d’acceptation

Le lot principal porte la preuve finale du scénario ; les lots contributeurs
apportent ses règles ou composants. Remplacer « — » par les chemins de tests et,
pour la qualification manuelle, le lien vers un compte rendu sans données privées.

| AC | Lot principal | Contributeurs | Validé | Preuve |
| --- | --- | --- | --- | --- |
| 01 | L8 | L1 | [x] hors ligne | [Référence manquante en CLI](../tests/unit/cli/test_coros_download.py) et [suggestion de sept jours](../tests/unit/domain/synchronization/test_periods.py) |
| 02–04 | L5 | L1, L0 | [x] hors ligne | [Bornes et périodes calendaires](../tests/unit/domain/synchronization/test_periods.py), [prévisualisation et filtrage](../tests/unit/cli/test_coros_preview.py) |
| 05 | L8 | L1, L6 | [x] hors ligne | [Sélecteurs invalides avant accès au trousseau](../tests/unit/cli/test_coros_preview.py) |
| 06–07 | L8 | L6, L7 | [x] hors ligne | [Relance et conversion d’une seule nouvelle activité](../tests/unit/cli/test_coros_download.py) |
| 08–10 | L7 | L1, L5 | [x] hors ligne | [Reprise incrémentale et imports tardifs](../tests/unit/cli/test_coros_download.py) |
| 11 | L5 | L0 | [x] hors ligne | [Tests de saturation](../tests/unit/application/test_synchronization_discovery.py) |
| 12 | L8 | L3, L6 | [x] hors ligne | [Collision de chemin à la minute](../tests/unit/infrastructure/synchronization/test_output.py) |
| 13–14 | L7 | L3, L6 | [x] hors ligne | [Interruptions avant et après publication](../tests/unit/cli/test_coros_download.py) |
| 15–17 | L8 | L7 | [x] hors ligne | [Reprise de conversion, réutilisation et réparation](../tests/unit/cli/test_coros_download.py) |
| 18 | L8 | L4, L6, L7 | [x] hors ligne | [Report des demandes et conversion au plafond](../tests/unit/cli/test_coros_download.py) |
| 19 | L8 | L6 | [x] hors ligne | [Échec individuel et poursuite du lot](../tests/unit/cli/test_coros_download.py) |
| 20 | L2 | L6 | [x] hors ligne | [Renouvellement, révocation et diagnostics](../tests/unit/test_coros_connection_production.py) |
| 21–22 | L8 | L3, L5 | [x] hors ligne | [Catalogue vide et activités déjà connues](../tests/unit/cli/test_coros_download.py), [prévisualisation avec et sans registre](../tests/unit/cli/test_coros_preview.py) |
| 23 | L7 | L1, L5 | [x] hors ligne | [Changement de fuseau et date absente](../tests/unit/cli/test_coros_download.py) |
| 24–25 | L3 | L9 | [ ] Windows | [Tests Linux du registre et de l’exclusion](../tests/unit/infrastructure/synchronization/test_output.py) |
| 26 | L8 | L3 | [x] hors ligne | [Collision et préservation des rapports](../tests/unit/cli/test_coros_download.py) |
| 27 | L7 | L5, L6 | [x] hors ligne | [Conservation des candidats datés et reprise](../tests/unit/cli/test_coros_download.py) |
| 28 | L8 | L2 | [x] hors ligne | [Prérequis de synchronisation](../tests/unit/cli/test_coros_download.py) et [trousseau indisponible](../tests/unit/test_coros_connection_production.py) |
| 29–30 | L8 | L0, L7 | [x] hors ligne | [Compatibilité de la conversion locale](../tests/unit/cli/test_cli.py), [priorité CLI, dossier `./coros/` et état terminal](../tests/unit/cli/test_coros_download.py) |
| 31 | L7 | L1, L8 | [x] hors ligne | [Repositionnement et conservation du quota](../tests/unit/cli/test_coros_download.py) |
| 32–33 | L4 | L6, L7 | [x] hors ligne | [Tests du quota partagé](../tests/unit/infrastructure/synchronization/test_quota_storage.py) |
| 34–35 | L8 | L6 | [x] hors ligne | [Bilans et validation du dossier avant accès distant](../tests/unit/cli/test_coros_download.py) |
| 36 | L7 | L1, L8 | [x] hors ligne | [Référence explicite et incrémentale](../tests/unit/cli/test_coros_download.py) |
| 37 | L8 | L3 | [x] hors ligne | [Rapport édité et révision distante ignorée](../tests/unit/cli/test_coros_download.py) |
| 38 | L9 | L3, L4, L7 | [x] hors ligne sous Linux | [Restauration, rollback et refus d’un registre inutilisable](#preuves-hors-ligne-et-qualification-restante) |
| 39 | L9 | L0, L2, L3 | [ ] Frontières de dates en conditions réelles | [Preuves Linux et Windows et vérifications restantes](#preuves-hors-ligne-et-qualification-restante) |

## 5. Journal de suivi

| Date | Lot / décision | Résultat, blocage ou prochaine action | Référence |
| --- | --- | --- | --- |
| 2026-11-17 | Plan initial | Lots et couverture proposés à partir de la spécification v1.8 et du prototype présent dans le dépôt. Aucun lot de production déclaré terminé. | Ce document |
| 2026-11-17 | L1 terminé | Modèle, périodes et sélection validés hors ligne sur Linux avec 757 tests et 92,03 % de couverture. | [Validation L1](#l1--modèle-métier-périodes-et-sélection) |
| 2026-11-21 | L3 implémenté | Stockage et exclusion validés hors ligne sur Linux. Qualification Windows ouverte. | [Validation L3](#l3--persistance-intégrité-des-fichiers-et-exclusion-concurrente) |
| 2026-11-21 | L4 implémenté | Registre par compte, réservation concurrente et refus HTTP 429 validés hors ligne sur Linux. | [Validation L4](#l4--budget-fit-partagé-et-compteurs) |
| 2026-11-21 | L5 implémenté | Prévisualisation activée et validée hors ligne. Qualification réelle des frontières de minuit ouverte. | [Validation L5](#l5--découverte-et-prévisualisation) |
| 2026-11-21 | L6 implémenté | Téléchargement seul sur période explicite validé hors ligne sous Linux. | [Validation L6](#l6--acquisition-bornée-et-téléchargement-seul) |
| 2026-11-21 | L7 implémenté | Référence incrémentale et reprise après interruption validées hors ligne sous Linux. | [Validation L7](#l7--référence-incrémentale-et-reprise-après-interruption) |
| 2026-11-22 | L8 implémenté | Synchronisation avec rapports, configuration partagée et suivi d’output validés hors ligne sous Linux. | [Validation L8](#l8--conversion-configuration-et-restitution-cli) |
| 2026-11-22 | L9 documentation et restauration | Guide publié dans le dépôt. Suite Linux et restauration locale avec adaptateurs simulés réussies. Qualification Windows et essais réels ouverts. | [Guide](coros-sync-guide.md) et [preuves L9](#preuves-hors-ligne-et-qualification-restante) |
| 2026-11-26 | L9 qualification partielle | Login, reprise sans invite et renouvellement de production réussis sous Linux et Windows. Comparaison du FIT témoin réussie. Second compte et frontières de minuit en attente. | [Preuves L9](#preuves-hors-ligne-et-qualification-restante) |
