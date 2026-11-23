# Spécification fonctionnelle — Récupération des activités COROS

**Projet :** fitToMd  
**Date :** 16 novembre 2026  
**Version :** 1.8 — règles de synchronisation et de reprise validées

**Statut :** V1 implémentée et validée hors ligne sous Linux. La qualification
Windows et les essais réels de production restent ouverts.

## 1. Objectif

Permettre à l’utilisateur de récupérer ses fichiers FIT COROS depuis la ligne de
commande, puis de produire leurs rapports Markdown sans téléchargement individuel
dans Training Hub.

Trois besoins constituent le cœur de la fonctionnalité :

1. Récupérer uniquement les activités qui ne sont pas encore présentes localement.
2. Récupérer les activités depuis une date absolue.
3. Récupérer les activités sur une période relative, notamment les sept derniers jours.

Une même commande doit pouvoir être relancée sans créer de doublons et reprendre
les opérations inachevées. Les FIT originaux sont conservés pour permettre une
nouvelle conversion hors ligne.

Ce document décrit le comportement cible et conserve le contexte des décisions.
Les commandes disponibles et les procédures de reprise figurent dans le
[guide d’exploitation COROS](coros-sync-guide.md). Les preuves acquises et les
vérifications restantes sont consignées dans le [suivi du lot L9](coros-sync-implementation-plan.md#l9--qualification-et-livraison).

## 2. Situation initiale et accès COROS

Avant le projet de synchronisation, FitToMd convertissait un fichier ou les FIT
directement contenus dans un dossier. Le traitement par dossier ignorait les
rapports existants et continuait lorsqu’un fichier échouait. La V1 ajoute la
connexion à un compte COROS, le téléchargement et le suivi des activités
synchronisées à côté de cette conversion locale.

COROS documente un accès MCP individuel en OAuth 2.0, sans candidature
préalable, et expose des outils de catalogue et de récupération de FIT.
Il ne fournit pas de notifications poussées pour ce type d’accès. Voir le
[guide développeur COROS](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP)
et la [revue du contrat en section 14.1](#141-contrat-et-accès-coros--revue-documentaire-et-validations-bloquantes).

La CLI utilise sa propre autorisation. Le prototype a démontré une connexion
autonome sous Linux pour un compte européen (section 14.1). La qualification de
la connexion de production reste à compléter. La revue du catalogue n'a établi
aucune pagination des activités. Les sources s'accordent sur 50 fichiers FIT,
avec des descriptions différentes de la période de quota (section 11.1).

## 3. Périmètre

### 3.1 Inclus dans la première version proposée

- Connexion, consultation de l’état de connexion et déconnexion.
- Sélection incrémentale, par date absolue ou par nombre de jours.
- Téléchargement des FIT et génération de Markdown par défaut.
- Mode téléchargement seul, pour dissocier acquisition et conversion.
- Conservation locale des FIT et d’un registre de suivi.
- Détection des doublons, reprise, gestion des quotas et bilan d’exécution.
- Exécution manuelle sur Linux et Windows.
- Bilan texte et compteur quotidien local des appels.
- Réutilisation des réglages de conversion existants.

### 3.2 Hors périmètre initial

- Écriture, modification ou suppression d’activités dans COROS.
- Synchronisation bidirectionnelle, serveur permanent et notifications temps réel.
- Interface graphique ou analyse par un modèle d’IA.
- Gestion simultanée de plusieurs comptes ; le stockage doit néanmoins les isoler.
- Modifications distantes : ignorées, sans détection ni mise à jour des rapports.
- Tâches planifiées et prise en charge de macOS.
- Borne `--until`, filtre par sport, historique complet implicite et durées en heures/semaines.
- Commande de forçage de téléchargement ou de régénération.
- Suivi des déplacements de fichiers ou de plusieurs destinations par activité.
- Import automatique d’anciens FIT locaux dans le registre.
- Interprétation libre de phrases comme « depuis lundi dernier ».
- Suppression locale en miroir des suppressions effectuées chez COROS.

## 4. Vocabulaire et règles d’identité

| Terme | Définition fonctionnelle |
| --- | --- |
| Activité distante | Activité enregistrée dans le compte COROS connecté. |
| Nouvelle activité | Identité distante absente du registre local de ce compte. |
| Activité à reprendre | Identité connue dont une opération reste inachevée ou a échoué temporairement. Les conversions non prises en charge sont exclues des reprises automatiques. |
| Synchronisation | Découverte, sélection, téléchargement puis conversion éventuelle. |
| Référence incrémentale | Point de départ de la prochaine découverte, avancé après découverte complète. |
| Registre | État persistant permettant de distinguer découverte, acquisition et conversion. |

L’identité repose sur le fournisseur, le compte et l’identifiant stable fourni
par COROS, complété du sport si le contrat API l’exige. Le titre, la date et le
nom du fichier ne suffisent pas. Le nommage à la minute est retenu pour cet
usage individuel ; l’identité distante reste utilisée dans le registre.

Une activité ancienne envoyée tardivement à COROS peut être nouvelle pour
FitToMd. « Nouvelle » ne signifie donc pas « commencée après la dernière commande ».

## 5. Interface CLI proposée

```bash
# Connexion explicite ; peut ouvrir le navigateur
fit-to-md coros login

# Première initialisation du quota local pour un compte connecté
fit-to-md coros init-quota

# État de connexion, chemins et suivi de synchronisation
fit-to-md coros status

# Première récupération et initialisation de la référence
fit-to-md coros sync --since 2026-11-01 --output reports/

# Récupération incrémentale après initialisation
fit-to-md coros sync --output reports/

# Sept derniers jours calendaires, aujourd’hui compris
fit-to-md coros sync --last-days 7 --output reports/

# Acquisition des FIT sans génération de rapports
fit-to-md coros sync --last-days 7 --download-only --output reports/

# Prévisualisation sans télécharger de FIT ni modifier le suivi
fit-to-md coros sync --last-days 7 --dry-run --output reports/

# Repositionnement explicite de la référence, sans acquisition
fit-to-md coros sync --init-coros-reference 2026-11-01 --output reports/

# Suppression des identifiants de connexion locaux
fit-to-md coros logout
```

### 5.1 Options de sélection et d’exécution

| Option | Comportement proposé |
| --- | --- |
| Aucune option de période | Mode incrémental ; nécessite une référence initialisée. |
| `--since YYYY-MM-DD` | Depuis le début du jour indiqué jusqu’au lancement. |
| `--last-days N` | Les N derniers jours calendaires, aujourd’hui compris. |
| `--download-only` | Télécharge les FIT ; ne génère aucun rapport. |
| `--dry-run` | Consulte les métadonnées et affiche les actions prévues. |
| `--output DIR` | Dossier existant obligatoire pour FIT, rapports et registre, y compris en téléchargement seul ; fourni directement ou via configuration. |
| `--init-coros-reference YYYY-MM-DD` | Initialise ou repositionne la référence sans télécharger ni convertir. |

`--since` et `--last-days` sont mutuellement exclusifs. N doit être un entier
strictement positif. Une date invalide, future ou un dossier de sortie invalide
provoque une erreur avant toute requête distante ou mutation locale.
`--init-coros-reference` est incompatible avec `--since`, `--last-days`,
`--download-only` et `--dry-run`. Il conserve les activités et les fichiers déjà
connus ; il ne réinitialise pas le compteur de quota.

Le format absolu accepté en première version est exclusivement `YYYY-MM-DD`.
Les options existantes de conversion, notamment `--config`, les zones cardiaques,
le rapport par tours et les enrichissements, restent utilisables lors de la
génération. Les options fournies directement prévalent sur la configuration.
Les sélecteurs de période restent explicites en CLI pour éviter qu’un fichier
de configuration ne masque le mode incrémental.

Les commandes locales existantes, comme `fit-to-md activity.fit`, conservent leur
syntaxe et leur fonctionnement hors ligne. Le routage du mot `coros` doit préserver
la possibilité de convertir un dossier local homonyme, par exemple `./coros/`.

## 6. Sémantique des dates

### 6.1 Fuseau de référence

Décision : utiliser le fuseau horaire local de la machine qui exécute la commande,
figé au lancement pour toute sa durée. Le fuseau du compte COROS n'est pas utilisé
comme référence utilisateur. Aucun paramètre `--timezone` n'est prévu en V1.

Le fuseau effectif et les bornes résolues figurent dans les bilans texte et
les prévisualisations. À l'exécution suivante, un changement de fuseau de la
machine s'applique aux nouvelles périodes calendaires ; les repères incrémentaux
sont conservés comme instants UTC pour éviter leur réinterprétation.

Les règles de changement d'heure de la machine doivent être respectées sur Linux
et Windows. Un simple décalage UTC actuel ne suffit pas pour une période qui
traverse un changement d'heure. Le moyen de résoudre ces règles sous Windows
reste à valider techniquement.

Le fuseau sert à sélectionner les activités. Il ne modifie pas les horodatages
du FIT ni les règles actuelles d'affichage des rapports.

### 6.2 Bornes de sélection

L’heure de lancement T est figée une fois par commande. Toutes les requêtes de découverte se rapportent à cette même borne supérieure. Une activité est sélectionnée
si son instant de début appartient à l’intervalle `[début, T)`.

Une activité commencée avant la borne basse mais terminée après celle-ci est
exclue. Une activité franchissant minuit n’est sélectionnée qu’une fois. Une
activité sans date de début exploitable est signalée comme impossible à classer,
sans date inventée ni téléchargement automatique.

Le catalogue documente des paramètres de date au format `yyyyMMdd`, et sa
date de fin par défaut dépend du fuseau du compte COROS. L'adaptateur devra
envoyer des bornes explicites couvrant la fenêtre locale, puis filtrer
les instants de début selon le fuseau de la machine. La manière dont COROS
interprète les dates explicites et sélectionne les activités proches de minuit
reste à vérifier avant de figer cette traduction.

### 6.3 Période absolue

`--since 2026-11-01` signifie depuis le 1er novembre 2026 à 00:00:00 dans le
fuseau effectif, jusqu’à T. La borne basse est inclusive.

### 6.4 Période relative

Décision : « sept derniers jours » désigne sept dates calendaires,
aujourd’hui compris, et non une durée glissante de 168 heures.

Exemple : le 16 novembre 2026 à 15:30 en `Europe/Paris`, `--last-days 7` couvre
le 10 novembre à 00:00 jusqu’au 16 novembre à 15:30. `--last-days 1` couvre
aujourd’hui depuis minuit. Les changements d’heure suivent le calendrier du
fuseau : une journée peut durer 23 ou 25 heures.

Une commande relancée demain recalcule la fenêtre relative. Les activités déjà
acquises dans cette fenêtre restent exclues des téléchargements.

## 7. Modes de synchronisation

### 7.1 Première utilisation

Sans référence enregistrée, `coros sync` explique qu'une période initiale est
nécessaire et suggère `--last-days 7 --output DIR`. Les sept jours constituent
la suggestion retenue ; ils ne sont pas appliqués implicitement. Le premier
lancement nécessite donc une période explicite ou une initialisation préalable.

La première synchronisation avec période explicite enregistre sa borne basse
comme référence de départ, puis avance cette référence après découverte complète
selon la règle ci-dessous. Un `--dry-run` ou un échec de validation/authentification
ne l'initialise pas. Une erreur de découverte laisse la référence de départ
inchangée et permet de réessayer la même période.

### 7.2 Mode incrémental

Décision : ne pas redécouvrir tout l'historique depuis la référence initiale.
Conserver l'instant UTC T de la dernière découverte complète.
La prochaine découverte reprend au début de la journée locale contenant cet
instant, puis va jusqu'au nouveau lancement. Cette relecture bornée à une journée
limite les problèmes de frontières et permet de dédupliquer les résultats.
Elle évite un balayage historique croissant à chaque commande.

Le marqueur n'avance qu'après découverte complète de la période et
enregistrement transactionnel des candidats. Les échecs de téléchargement/conversion restent
reprenables depuis le registre même après son avancement. Un échec de catalogue
ne l'avance pas. Une borne positionnée par `--init-coros-reference` s'applique
jusqu'à la prochaine découverte incrémentale complète.

Une activité envoyée tardivement dont le début est antérieur à cette fenêtre
peut être manquée. Elle reste récupérable avec `--since` ou `--last-days`, ou
par repositionnement explicite de la référence. La couverture et cette limite
sont documentées ; aucune exhaustivité sur les imports tardifs n'est promise.
Aucun filtre de création ou curseur de changement n'est documenté. La V1
ne dépend donc pas d'un tel mécanisme.

### 7.3 Sélection explicite et repositionnement

Après initialisation, `--since` et `--last-days` bornent uniquement l'exécution
courante et ne changent pas la référence incrémentale, même si leur période est
plus ancienne ou si leur découverte réussit.

L'option retenue pour modifier explicitement la référence est
`--init-coros-reference`. Syntaxe retenue :
`coros sync --init-coros-reference YYYY-MM-DD --output DIR`.
Elle positionne la référence au début du jour choisi dans le fuseau de la machine,
sans acquisition ni conversion. Le bilan montre l'ancienne et la nouvelle valeur.
Les activités déjà connues et les compteurs de quota sont conservés.

### 7.4 Travail inachevé

Le mode incrémental reprend les éléments inachevés du registre indépendamment
de leur date. Une période explicite ne reprend que les éléments de sa fenêtre.
Une activité acquise avec `--download-only` peut être convertie ensuite sans
nouveau téléchargement.

Un FIT non pris en charge reçoit un état terminal `conversion_unsupported` :
aucune nouvelle tentative automatique, y compris après une mise à jour du logiciel.
L'utilisateur peut relancer explicitement la conversion locale existante, par
exemple `fit-to-md reports/fits/ACTIVITE.fit --output reports/ACTIVITE.md`,
vers un fichier qui n'existe pas encore. Aucune commande COROS de forçage ni de
régénération n'est ajoutée. Le registre conserve `conversion_unsupported` comme
état terminal, sans reconnaissance automatique du rapport manuel en V1 ; le
téléchargement reste acquis.

## 8. Déroulement d’une exécution

1. Valider les arguments, la configuration et les destinations locales.
2. Vérifier la connexion et renouveler les jetons si nécessaire.
3. Figer T, résoudre le fuseau et calculer la période effective.
4. Acquérir une protection exclusive du dossier ; refuser immédiatement une
   deuxième synchronisation concurrente avec un message explicatif.
5. Couvrir toute la période avec le mécanisme de découverte validé (section 14.1) ;
   dédupliquer les résultats distants et détecter les réponses tronquées.
6. Construire le plan : éléments connus, nouveaux, à reprendre ou en anomalie.
7. En prévisualisation, afficher ce plan et terminer sans mutation métier.
8. Enregistrer les candidats puis les traiter du plus ancien au plus récent,
   avec un ordre stable entre activités au même instant.
9. Télécharger et valider chaque FIT manquant ; finaliser son écriture de façon atomique.
10. Générer les rapports demandés à partir des FIT disponibles ; enregistrer chaque résultat.
11. Si cette découverte est incrémentale et complète, avancer sa référence après
    enregistrement des candidats ; une requête explicite ultérieure ne la change pas.
12. Afficher le bilan, libérer la protection exclusive et retourner un code de sortie.

La découverte doit être complète avant d'annoncer un plan complet. Si elle
échoue ou qu'une réponse potentiellement tronquée ne peut pas être résolue,
indiquer que le catalogue est incomplet et conserver les travaux déjà
enregistrés. Ne pas avancer le marqueur de couverture.

## 9. Stockage, doublons et cohérence

### 9.1 Organisation fonctionnelle

Décision : un dossier `--output` existant est obligatoire pour les sauvegardes.
Proposition d'organisation, dont les sous-dossiers sont créés par FitToMd :

```text
reports/                         # existe avant la commande
  YYYY-MM-DD HH-MM.md             # rapports
  fits/YYYY-MM-DD HH-MM.fit       # FIT originaux
  .fit-to-md/coros.sqlite3        # registre des activités, référence et version du schéma
```

Le nommage à la date, heure et minute est retenu pour l'usage individuel demandé,
sans suffixe d'identité. Par précaution d'intégrité, si deux identités distinctes
aboutissent malgré tout au même chemin, la seconde est signalée en conflit et
n'écrase pas la première. Aucun mécanisme de renommage automatique n'est prévu.
La date du nom est calculée dans le fuseau machine au premier téléchargement,
puis le chemin mémorisé est réutilisé même si ce fuseau change.

Les secrets OAuth sont conservés uniquement dans le trousseau système, jamais
comme fichiers du dossier output. Le registre contient seulement une référence
vers l'entrée du trousseau. Si celui-ci est indisponible, les commandes nécessitant
les identifiants COROS échouent clairement, sans secours en texte clair.

Un dossier correspond à un compte et à une seule destination. Aucun suivi de
plusieurs destinations, déplacement manuel ou changement de dossier n'est prévu.
Un nouveau dossier constitue un autre suivi : ne pas lui transférer implicitement
l'état d'un dossier précédent. Le compteur quotidien doit néanmoins rester
commun au compte sur la même machine (section 11.1).

### 9.2 Informations minimales du registre

| Information | Finalité |
| --- | --- |
| Version du schéma, fournisseur, identité du compte | Lire, migrer et isoler l’état. |
| Référence incrémentale UTC et fuseau de résolution | Reproduire la prochaine sélection. |
| Identité distante, sport, début, date de découverte | Sélectionner et dédupliquer. |
| État d’acquisition, chemin FIT, taille et empreinte | Contrôler l’existence et l’intégrité du fichier. |
| État de conversion, chemin et options utilisées | Distinguer FIT acquis, rapport disponible et conversion non prise en charge. |
| Dernière erreur et tentative | Expliquer et reprendre les échecs. |
| Dernière découverte complète et travaux en attente | Ne pas confondre couverture et succès individuel. |

Les secrets et les URL temporaires donnant accès aux FIT ne figurent pas dans
ce registre.

### 9.2.1 Stockage retenu : SQLite et fichiers finalisés par renommage

Utiliser une base SQLite locale, via le module `sqlite3` de Python, sans serveur.
Une transaction courte enregistre chaque changement d'état. Une table versionne
le schéma ; une migration est transactionnelle et précédée d'une sauvegarde.
Une version inconnue provoque une erreur, pas une remise à zéro.

Une protection interprocessus du dossier empêche deux commandes de travailler
en même temps. Proposition : un verrou géré par le système d'exploitation,
libéré automatiquement à la fin ou au crash du processus, et acquis sans attente.
La seconde commande sort immédiatement avec le code 1 et indique qu'une
synchronisation utilise déjà ce dossier. Le mécanisme Linux/Windows sera choisi
à la conception technique ; ne pas s'appuyer sur la seule présence d'un fichier
qui pourrait rester après un crash.

Chaque FIT est écrit dans un temporaire situé sur le même système de fichiers,
validé, puis renommé à son emplacement définitif. Avant le téléchargement, le
registre mémorise l'identité et le chemin prévu. La reprise peut ainsi vérifier
un FIT final présent après une interruption avant de le redemander. SQLite et
le système de fichiers n'offrent pas une transaction unique : cette réconciliation
est nécessaire. Les rapports suivent aussi une écriture temporaire finalisée.

Pour sauvegarder simplement : fermer FitToMd puis copier le dossier output
entier, base comprise. Les secrets restent dans le trousseau et nécessitent une
nouvelle connexion sur une autre machine. Un registre perdu se restaure depuis
la sauvegarde ; pas de reconstruction automatique à partir des FIT ni de remise
à zéro silencieuse. Sans sauvegarde, utiliser un nouveau dossier implique un
nouveau suivi et peut entraîner de nouveaux téléchargements. Un mécanisme de
réparation automatique est hors périmètre V1.

### 9.3 États et reprise

Une activité peut être découverte, en attente de FIT, FIT disponible, rapport
disponible ou en échec à une étape précise. Un échec de conversion ne remet pas
en cause l’acquisition réussie. Une erreur de téléchargement ne crée pas un FIT
considéré comme complet.

Après interruption, un fichier temporaire n’est jamais accepté comme acquisition
réussie. Si le processus s’arrête entre l’écriture finale du FIT et la mise à jour
du registre, la reprise réconcilie le fichier avec son identité et son intégrité
avant d’envisager un nouveau téléchargement.

Si un FIT enregistré a disparu ou est corrompu, l’activité redevient à acquérir.
Si seul le rapport manque, il est régénéré depuis le FIT local. Un registre
illisible doit provoquer une erreur explicite, sans réinitialisation silencieuse.

### 9.4 Rapports et collisions

Une identité distante est téléchargée au plus une fois tant que son FIT valide
existe. Les anciens FIT manuels ne sont pas importés dans le registre.
Les modifications distantes sont ignorées.

Les fichiers existants ne sont pas écrasés automatiquement. Un fichier déjà
occupé et non associé à la même identité provoque un conflit explicite. Aucun
suffixe automatique n'est ajouté au nommage par heure et minute retenu.

Les rapports édités manuellement ne sont pas vérifiés par empreinte. Modifier
les options ne régénère pas un rapport existant ; il n'existe aucune option COROS
de forçage. Les commandes locales actuelles restent disponibles pour une
conversion manuelle, sous la responsabilité de l'utilisateur.

Le déplacement des fichiers et le changement de destination ne sont pas gérés.
La récupération de fichiers manquants ne constitue pas une recherche de fichiers
déplacés : FitToMd vérifie seulement les chemins déjà enregistrés.

## 10. Connexion et systèmes supportés

`login` lance l’autorisation COROS et confirme le compte associé. FitToMd ne
demande ni ne conserve le mot de passe COROS. Les jetons doivent être protégés
par le trousseau système. Son accès doit fonctionner sans interaction lors des
commandes de synchronisation ; s’il est indisponible, la fonctionnalité échoue.

`status` indique la disponibilité locale de la connexion, la référence, le fuseau,
les emplacements et le nombre d’éléments en attente. Il distingue un état local
d’une connexion effectivement vérifiée auprès de COROS.

`logout` retire les jetons locaux, conserve FIT, rapports et registre, et
ne révoque pas l’accès distant chez COROS. Un compte différent ne
peut pas réutiliser le registre du compte précédent.

`sync` ne déclenche pas de connexion interactive implicite : si une nouvelle
autorisation est nécessaire, il indique de lancer `login`. Le renouvellement
automatique des jetons est autorisé lorsque le protocole le permet.

Aucune tâche planifiée n'est prévue. La synchronisation ne pose pas de question
interactive ; seule la connexion explicite peut nécessiter le navigateur.
Les plateformes supportées sont Linux et Windows. L’aide, les erreurs et les
bilans COROS sont en anglais. Aucun appel à un modèle d'IA n'est nécessaire.

MCP est un protocole, pas un paquet COROS à installer. Aucun SDK COROS spécifique
n'est présupposé. L'implémentation doit néanmoins assurer transport, authentification
et accès au trousseau ; une bibliothèque MCP/OAuth générique ou un adaptateur
interne peut être nécessaire. Les dépendances exactes et leur caractère optionnel
seront choisis après le prototype, en privilégiant une solution minimale.

## 11. Quotas, erreurs et compte rendu

### 11.1 Quotas et indisponibilités

COROS documente un plafond de **50 fichiers**, commun à la récupération directe
et aux demandes d'URL FIT. La [FAQ technique](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/faq.md#9-can-coros-mcp-retrieve-fit-files)
parle d'une fenêtre fixe de 24 heures par utilisateur de la plateforme,
sans prolongation lors des appels suivants. Le [centre d'aide](https://support.coros.com/hc/en-us/articles/50841795180948-Connect-Your-COROS-to-AI)
parle de jour calendaire. Le point de départ de la fenêtre, son périmètre précis
entre clients et la facturation des tentatives échouées ne sont pas établis.

Décision maintenue : conserver les compteurs d'appels de FitToMd par jour et
distinguer les appels de catalogue des demandes FIT. Avant toute demande FIT,
exclure les fichiers déjà acquis et réserver transactionnellement le coût
de la demande (un fichier par activité en V1). En cas de
réponse perdue, conserver la réservation par prudence.

Garde-fou retenu tant que le calendrier serveur n'est pas confirmé :
horodater en UTC les réservations et limiter le budget local à 50 unités FIT
sur les 24 heures précédentes. Cette règle locale peut libérer le budget plus
tard que COROS ; elle ne décrit pas la fenêtre du serveur. Le compteur par jour
reste disponible pour le bilan. Le changement de date sur la machine ne remet
pas le budget à zéro. Le bilan distingue plafond annoncé et demandes locales.

Le compteur se trouve dans SQLite. Pour un même compte sur la même machine,
un unique registre de quota fait autorité : le trousseau conserve son chemin,
pas le compteur lui-même. Un second dossier réutilise ce compteur au lieu de
créer un nouveau budget. `fit-to-md coros init-quota` initialise explicitement
le registre pour un compte qui utilise cette installation pour la première fois.
Si ce registre est perdu ou indisponible, ne pas
réinitialiser le budget à zéro : toute acquisition est refusée jusqu’à
restauration d’un registre fiable. Login, logout et changement d’output ne
recréent pas implicitement un budget. La liaison compte/quota est conservée
dans le trousseau indépendamment des jetons de session.

Ce suivi évite de dépasser le budget par les appels connus de cette installation.
Il ne peut pas connaître les appels effectués par d'autres clients ou machines :
une réponse de quota épuisé du serveur reste prioritaire.

En cas de quota épuisé, conserver les candidats non traités, arrêter les nouvelles
acquisitions et convertir les FIT déjà disponibles. Ne pas attendre jusqu’au
lendemain dans le même processus. Si COROS fournit une échéance de reprise, l’afficher.

Décision V1 : aucune nouvelle tentative automatique de demande FIT après un
échec ; la commande suivante reprend le travail et consomme le budget correspondant.
Les appels de catalogue peuvent avoir des tentatives bornées avec temporisation
et respect des indications du serveur. Une erreur permanente ne provoque pas de
boucle. Les délais réseau, l'expiration des URL et le comptage des tentatives
doivent être vérifiés avec COROS.

### 11.2 Comportements attendus

| Situation | Résultat attendu |
| --- | --- |
| Aucune activité sélectionnée ou nouvelle | Succès avec bilan à zéro. |
| Un FIT échoue | Conserver l’erreur et poursuivre les autres activités si possible. |
| Une conversion échoue ou le FIT est non pris en charge | Conserver le FIT ; signaler l’étape en échec. |
| Session expirée, renouvellement possible | Renouveler puis poursuivre. |
| Autorisation révoquée | Arrêter les appels COROS et demander une reconnexion explicite. |
| Catalogue incomplet | Signaler la couverture incomplète ; conserver les travaux enregistrés. |
| Disque plein ou registre non inscriptible | Arrêter les mutations et préserver les résultats déjà finalisés. |
| Synchronisation concurrente | Échouer clairement sans corrompre le suivi. |
| Activité disparue chez COROS | Signaler l’indisponibilité ; conserver ses fichiers locaux. |
| Échec d’un enrichissement facultatif | Appliquer le comportement dégradé actuel ; le rapport reste réussi s’il est généré. |

Une activité multisession ou d’un sport non pris en charge par la conversion
peut rester téléchargée ; l’échec de conversion n’est pas un échec d’acquisition.

### 11.3 Sorties et codes

Les diagnostics et la progression sont écrits sur stderr. Décision : stdout
contient un bilan de synchronisation, sans concaténation automatique des rapports.
Le comportement stdout des conversions locales existantes reste inchangé.

Le bilan indique la période, le fuseau, la complétude de la découverte, les
activités sélectionnées, déjà acquises, les nouveaux FIT, les rapports créés,
les éléments en erreur et ceux reportés pour quota. Les compteurs d'acquisition
et de conversion sont séparés et peuvent concerner une même activité.

Par activité, la progression reste simple : « Récupération de l'activité … »,
puis « réussie » ou « échouée », avec un motif bref en cas d'échec. Ni secret,
ni URL temporaire ne sont affichés.

En mode normal, le succès signifie que les FIT et rapports demandés sont
disponibles. En mode téléchargement seul, les rapports ne conditionnent pas le succès.

| Code proposé | Signification |
| --- | --- |
| `0` | Travail demandé terminé, y compris aucune nouveauté ou prévisualisation complète. |
| `1` | Échec opérationnel ou résultat partiel : quota, réseau, stockage, authentification ou conversion. |
| `2` | Arguments ou configuration invalides, y compris référence initiale manquante. |
| `130` | Interruption volontaire par l’utilisateur, avec travail déjà finalisé conservé. |

`--dry-run` peut consulter COROS et renouveler une session ; il n’obtient aucune
URL FIT, ne télécharge aucun FIT et ne change ni référence ni état des activités.
Il annonce une estimation des acquisitions, sans garantir le quota restant ou
la réussite ultérieure des conversions.

## 12. Intégration à l’architecture DDD

Créer un contexte d’acquisition/synchronisation responsable des références
distantes, périodes, identités, règles de reprise et états de synchronisation.
Ses règles métier ne dépendent pas de MCP, OAuth, HTTP ou d’un moteur de stockage.

L’application orchestre des ports de catalogue distant, acquisition de fichiers,
registre, stockage de secrets et horloge. L’adaptateur COROS traduit les réponses
externes vers les contrats internes. Les réponses MCP et identifiants de connexion
ne pénètrent pas les modèles Activity et Reporting.

L’orchestration appelle le cas d’usage existant de génération à partir d’un FIT
local. Activity reste responsable de l’activité normalisée, Reporting des calculs
et rapports. La CLI assemble les composants et présente les résultats.

Les nouveaux contrats publics devront être typés. Les règles d’import et les
tests d’architecture devront couvrir les nouvelles frontières. Le choix des
classes et bibliothèques sera détaillé dans une conception technique séparée.

## 13. Critères d’acceptation et stratégie de tests

Chaque comportement ci-dessous doit être couvert dans `tests/`. Les interactions
COROS sont simulées ; aucun test obligatoire ne dépend d’un compte ou du réseau.
L’horloge, le fuseau et les réponses de découverte doivent être contrôlables.

| ID | Scénario | Résultat attendu |
| --- | --- | --- |
| AC-01 | Premier `sync` sans période | Erreur avec suggestion de sept jours ; aucun téléchargement ni référence créée. |
| AC-02 | `--since 2026-11-01` | Inclut le début exact de journée ; exclut l’activité commencée avant. |
| AC-03 | Le 16 novembre à 15:30, `--last-days 7` | Début le 10 novembre à minuit dans le fuseau choisi. |
| AC-04 | `--last-days 1`, changement de mois, année et heure | Bornes calendaires correctes, aucune approximation par 24 heures. |
| AC-05 | Deux sélecteurs, N nul/négatif/non entier, date invalide ou future | Code 2 avant accès distant ou mutation. |
| AC-06 | Même synchronisation exécutée deux fois | Aucun nouveau téléchargement ni doublon de rapport au second passage. |
| AC-07 | Nouvelle activité dans la période suivie | Seul son FIT est acquis ; son rapport est créé. |
| AC-08 | Activité tardive antérieure à la fenêtre incrémentale | Pas de balayage historique ; récupération possible par période explicite. |
| AC-09 | Activité antérieure à la fenêtre incrémentale courante | Absente du mode incrémental ; récupérable avec une période explicite. |
| AC-10 | Période explicite après initialisation | Aucun changement implicite de référence ; filtres correctement appliqués. |
| AC-11 | Catalogue saturé et résultats recouvrants | Découverte complète si le mécanisme validé le permet, chaque identité traitée une fois ; sinon résultat incomplet et référence inchangée. |
| AC-12 | Deux identités aboutissent au même nom à la minute | Conflit signalé sans écrasement ni suffixe automatique. |
| AC-13 | Téléchargement interrompu puis relance | Aucun fichier partiel accepté ; reprise sans perte des succès. |
| AC-14 | Arrêt entre écriture du FIT et mise à jour du registre | Réconciliation avant nouveau téléchargement. |
| AC-15 | FIT valide disponible, erreur temporaire de conversion | Reprise sans téléchargement ; une conversion non prise en charge reste exclue des reprises. |
| AC-16 | `--download-only` puis synchronisation normale | FIT réutilisé pour créer le rapport. |
| AC-17 | FIT supprimé/corrompu ou rapport supprimé | Reprise à la bonne étape ; seuls les fichiers nécessaires sont acquis. |
| AC-18 | Quota atteint au milieu d’un lot | Succès conservés, autres acquisitions reportées, code 1 et reprise possible. |
| AC-19 | Un fichier échoue dans un lot | Les activités suivantes sont traitées ; bilan partiel exact. |
| AC-20 | Jeton renouvelable puis autorisation révoquée | Renouvellement testé ; révocation expliquée sans boucle ni secret journalisé. |
| AC-21 | Aucun résultat, ou toutes les activités sont connues | Code 0, aucun FIT demandé. |
| AC-22 | `--dry-run` avec et sans registre | Métadonnées consultées ; aucun FIT/URL demandé ni suivi modifié. |
| AC-23 | Fuseau machine modifié ou date d’activité manquante | Nouvelle période locale correctement résolue, repère UTC conservé ; date absente signalée. |
| AC-24 | Registre lu après écriture, version inconnue, état corrompu | Aller-retour fidèle ; erreur sûre ou migration testée, sans remise à zéro. |
| AC-25 | Deux processus sur le même dossier | Deuxième commande refusée immédiatement, première intacte ; libération après crash. |
| AC-26 | Sortie occupée ou options de conversion modifiées | Aucun écrasement silencieux ; action attendue expliquée. |
| AC-27 | Catalogue interrompu ou FIT temporairement indisponible | Couverture incomplète signalée et éléments reprenables conservés. |
| AC-28 | Synchronisation sans connexion ou trousseau indisponible | Sortie sans navigateur, question ni stockage en clair ; login explicite nécessaire. |
| AC-29 | Conversion locale existante et configuration | Compatibilité préservée, y compris priorité CLI et dossier `./coros/`. |
| AC-30 | FIT non pris en charge puis nouvelle synchronisation | FIT conservé, conversion marquée terminale, aucune tentative automatique. |
| AC-31 | `--init-coros-reference DATE` | Repère modifié sans téléchargement, suppression de suivi ni remise à zéro du quota. |
| AC-32 | Compteur quotidien au plafond puis relance | Aucune demande FIT supplémentaire ; compteur conservé après redémarrage. |
| AC-33 | Quota partagé, minuit local et refus serveur | Compteur par jour et fenêtre de protection correctement distingués ; budget local par compte respecté ; refus serveur prioritaire. |
| AC-34 | Succès, résultat partiel et arguments invalides | Bilan texte sur stdout ; progression et diagnostics sur stderr ; codes de sortie conformes. |
| AC-35 | Output absent ou inexistant, même avec téléchargement seul | Code 2 avant réseau et mutation ; aucun dossier racine créé. |
| AC-36 | Période explicite après initialisation et incrémental réussi | Première sans changement du repère ; second avance après découverte complète. |
| AC-37 | Modification distante ou édition manuelle d'un rapport | Aucun téléchargement de révision, comparaison d'empreinte ou régénération. |
| AC-38 | Sauvegarde restaurée, migration interrompue, registre perdu | Restauration cohérente ; rollback ou erreur explicite ; aucune remise à zéro automatique. |
| AC-39 | Linux et Windows | Dates, trousseau, chemins et exclusion concurrente validés sur les deux systèmes. |

Les nouveaux modules doivent avoir des tests de réussite et d’échec. Les tests
de persistance couvrent aussi les écritures interrompues, les chemins et la
séparation des comptes. La qualité de livraison exige `pytest` réussi, ainsi que
les contrôles de typage, lint et couverture applicables au dépôt. Le [changelog](../changelog.md)
et le [guide d’exploitation](coros-sync-guide.md) documentent les commandes livrées.

## 14. Questions à traiter avant implémentation

**Décisions L0 du 17 novembre 2026 :** voir la
[note technique](coros-sync-technical-decisions.md) pour les contrats candidats,
diagnostics, preuves et blocages. L’utilisateur a confirmé sur Linux/Europe
la stabilité de l’identité après renouvellement/reconnexion, la concordance des
horaires UTC/locaux, le jour attendu et l’invariance des instants entre fuseaux.
La frontière de minuit et la comparaison avec un autre compte restent non qualifiées.

Les recommandations ci-dessus permettent de discuter d’un comportement concret.
Elles ne dispensent pas de répondre aux questions suivantes. « Bloquant » signifie
qu’une réponse ou une preuve technique est requise avant de coder le parcours concerné.

### 14.1 Contrat et accès COROS — revue documentaire et validations bloquantes

**Étape 1 réalisée le 16 novembre 2026.** Les constats ci-dessous proviennent
des sources officielles et du code d'exemple publié par COROS. Aucun compte
n'a été connecté et aucun téléchargement réel n'a été effectué. Un exemple de
client démontre une approche de référence, pas une garantie de compatibilité
avec le serveur ou avec FitToMd.

- **[DEV]** [Accès développeur MCP et limites](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP)
- **[CLIENT]** [Exemple Python officiel : connexion, renouvellement et appels](https://github.com/coroslab/COROS-MCP/blob/main/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py)
- **[OUTILS]** [Guide officiel des outils](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/tools.md)
- **[FAQ]** [FAQ technique COROS MCP](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/faq.md)
- **[AIDE]** [Centre d'aide COROS : connexion et limites FIT](https://support.coros.com/hc/en-us/articles/50841795180948-Connect-Your-COROS-to-AI)

| N° | État et preuve documentaire | Validation restante et conséquence pour FitToMd |
| --- | --- | --- |
| 1. Client autonome | **Étayé par le code.** [DEV] annonce un accès sans candidature. [CLIENT] enregistre dynamiquement un client public sans authentification au point d'échange des jetons. | Vérifier l'enregistrement avec un identifiant de client propre à FitToMd sur la région du compte. Aucun secret embarqué. |
| 2. OAuth et renouvellement | **Partiellement établi.** [CLIENT] emploie un code d'autorisation avec PKCE S256, contrôle de `state`, URI de retour loopback enregistrée et renouvellement. Les portées d'exemple sont `openid offline_access mcp.tools`. [FAQ] annonce un jeton d'accès de 30 jours et un jeton de renouvellement de 180 jours, remplacé au renouvellement. | Vérifier le flux réellement accepté, les URI et portées minimales (dont une éventuelle portée de lecture seule), et la rotation. Utiliser les durées retournées par le serveur. Le cache fichier de l'exemple ne satisfait pas l'exigence de trousseau : prouver la persistance et la reprise avec le trousseau Linux/Windows. |
| 3. Navigateur distant | **Étayé par le code.** [CLIENT] sépare le lancement de la connexion et sa finalisation dans le terminal d'origine après autorisation dans un navigateur. | Aucun mode de première autorisation sans navigateur vérifié. Le navigateur distant n'est pas un prérequis V1. Vérifier surtout qu'après `login`, une seconde exécution n'a besoin ni du navigateur ni d'une question interactive. |
| 4. Transport et accès direct | **Étayé par le code.** [CLIENT] utilise Streamable HTTP, propose MCP `2025-06-18` à l'initialisation puis appelle `tools/list` et `tools/call`. [FAQ] décrit un flux sans session et un routage régional, dont `https://mcpeu.coros.com/mcp` pour les comptes européens. | Vérifier la version acceptée et les schémas exposés par le serveur régional. Garder l'émetteur d'autorisation et les appels dans la région appropriée. Aucun assistant ni modèle requis par cette approche. |
| 5. Identité | **Partiel.** [OUTILS] demande `labelId` et `sportType` tirés de la même activité ; `queryUserInfo` ne documente pas d'identifiant de compte stable. | Conserver le couple comme candidat à la clé dans un espace compte. Vérifier la stabilité du couple et la source d'une identité de compte opaque et stable après reconnexion ; ne pas déduire le compte du nom, du profil de santé ou du jeton d'accès. |
| 6. Dates et historique | **Partiel.** [OUTILS] documente les bornes `yyyyMMdd`, la date de fin par défaut dans le fuseau COROS et la possibilité de demander des dates plus anciennes. | Vérifier l'inclusion des jours, le fuseau des bornes explicites, le champ de début machine-exploitable et sa précision, et la profondeur d'historique. Comparer deux fuseaux, une frontière de minuit et une période ancienne avec Training Hub ; le filtre final reste celui de la machine. |
| 7. Exhaustivité | **Découpage validé sur une plage témoin (section 14.1.3).** [OUTILS] documente `limit` (20 par défaut), sans mécanisme de pagination des activités, maximum garanti ou signal de troncature précisé. Le curseur de `tools/list` dans [CLIENT] pagine la liste des outils, pas le résultat de `querySportRecords`. | Le découpage en jours disjoints a retrouvé les 22 activités attendues en nombre. Une pagination native, l’absence de troncature sous la limite et l’égalité des ensembles d’identités ne sont pas établies. Si une journée reste saturée sans voie de continuation, signaler l’incomplétude et garder la référence inchangée. |
| 8. Créations tardives | **Non documenté.** [OUTILS] n'indique ni filtre par date de création/modification distante ni curseur de changements dans le catalogue. | Vérifier le schéma publié par le serveur. Sans mécanisme confirmé, conserver l'incrémental borné de la section 7 et la récupération explicite des activités tardives hors fenêtre. |
| 9. Récupération FIT | **Documenté.** [OUTILS] décrit la récupération directe (`downloadActivityFitFiles`) et le repli par URL (`queryActivityFitFileDownloadUrls`), par période ou par couple activité/sport. | Tester la représentation du binaire et l'association avec l'identifiant côté client. Préférer la récupération directe par activité absente. Si le repli est nécessaire, vérifier domaines, expiration, redirections et éventuelle authentification de l'URL, sans journaliser le lien ni transférer le jeton MCP à un hôte de fichiers. |
| 10. Quota | **Partiel, sources divergentes.** [OUTILS] et [FAQ] décrivent 50 fichiers partagés entre les deux modes ; [FAQ] précise une fenêtre fixe de 24 heures, [AIDE] parle de jour calendaire. | Demander à COROS le déclencheur de fenêtre, le partage entre clients, le débit des demandes échouées et le signal de reprise. En attendant, appliquer le garde-fou retenu à la section 11.1. Ne pas épuiser le quota réel pour le mesurer. |
| 11. Autres limites et erreurs | **Partiel.** [OUTILS] indique 5 FIT par défaut et 10 maximum par appel. [CLIENT] illustre plusieurs niveaux d'erreur, sans établir de contrat complet sur les tailles, délais ou fréquences. | Vérifier les limites publiées par le serveur et le comportement observable sur les erreurs non destructives de lecture. Prévoir délais bornés et distinguer erreurs HTTP, MCP et métier ; simuler quotas, interruptions et réponses invalides dans les tests. |
| 12. Équivalence Training Hub | **Non garantie.** [OUTILS] indique que le contenu dépend du sport, de l'appareil et des données enregistrées. | Comparer un ou deux FIT réels obtenus par MCP aux exports manuels des mêmes activités : empreinte si identique, sinon comparaison des sessions, enregistrements, tours, pauses et du Markdown produit. Documenter la portée de l'échantillon ; les activités multisessions restent soumises aux limites du convertisseur. |
| 13. Usage local | **Lecture documentée, archivage à clarifier si nécessaire.** [DEV] ouvre l'accès aux données autorisées et réserve les usages de synchronisation bidirectionnelle à l'API partenaire ; [OUTILS] expose la lecture des FIT. | Le périmètre FitToMd est une acquisition locale déclenchée par l'utilisateur, sans écriture COROS ni service de synchronisation tiers. Cette lecture locale est l'interprétation retenue, pas une autorisation explicite d'archivage récurrent. Vérifier les conditions d'usage applicables et demander une clarification ciblée si elles restent ambiguës. |

**Critères de passage après prototype :** démontrer une connexion autonome
réutilisable et renouvelable, la découverte complète d'une période témoin malgré
la limite du catalogue, et la récupération d'un FIT lisible par FitToMd.
Le compte rendu devra séparer ce qui a été observé des limites encore ouvertes,
sans conserver de jetons, d'URL privées ou de données personnelles dans le dépôt.
Les tests automatisés restent hors ligne.

#### 14.1.1 Étape 2 — prototype de connexion

Le prototype `fit-coros-probe` constitue une commande expérimentale séparée de
`fit-to-md`. La commande de connexion ne télécharge aucun FIT. `probe` vérifie l'initialisation
MCP et la présence du catalogue d'activités et d'au moins un des deux outils FIT
dans la liste des outils. Cette liste ne prouve ni l'accès aux données ni
l'exhaustivité du catalogue d'activités.

| Niveau de preuve | Résultat | Conséquence |
| --- | --- | --- |
| Serveur public, sans authentification | Le [document de découverte de COROS Europe](https://mcpeu.coros.com/.well-known/openid-configuration) a été lu avec succès. Il annonce l'enregistrement dynamique, l'échange de code, PKCE S256, l'authentification de client `none`, le renouvellement et les portées `openid`, `offline_access`, `mcp.tools`. Il annonce aussi un point `userinfo` et un flux par code d'appareil. | Les points 1 et 2 sont corroborés au niveau des capacités annoncées. Aucune portée distincte de lecture seule n'est annoncée dans ce document. `userinfo` est une piste pour le point 5 ; aucun identifiant stable n'a été observé. Le flux par code d'appareil annoncé n'a pas été testé et n'est pas celui implémenté. |
| Implémenté et testé hors ligne | Le Python du prototype reproduit le parcours de [CLIENT] avec un client public, une session CLI autorisée dans le navigateur, puis réclamée par le terminal, PKCE et contrôle de la destination et de `state`. Le rafraîchissement utilise l'expiration retournée par le serveur ; le jeton de renouvellement remplacé est sauvegardé avant les appels MCP. `probe --refresh` permet de provoquer un renouvellement sans attendre l'expiration. | Les tests couvrent la conservation et la reprise via un faux trousseau, le refus d'une destination ou d'un état OAuth incorrect, la rotation, l'absence de fuite dans les messages d'erreur et la découverte MCP simulée. Le flux interactif ne récupère pas le mot de passe. L'émetteur régional est choisi explicitement (Europe par défaut), indépendant du fuseau de la machine. |
| Stockage et limites | L'adaptateur Linux utilise Secret Service et refuse une collection ou une entrée verrouillée sans demander son déverrouillage. L'adaptateur Windows cible Credential Manager. Il n'y a ni cache en clair ni repli fichier. Dans l'environnement de travail, le backend Linux n'est pas installé et le bac à sable empêche l'accès au bus de session. | Confirmer l'écriture, la lecture après redémarrage et la rotation dans un vrai trousseau sur les deux OS, sans invite lors de `probe`. Sur Windows, vérifier que les jetons réels tiennent dans une entrée : [Microsoft limite le contenu à 2 560 octets](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw). Un stockage groupé dans une entrée est un choix du prototype. |
| Essai local avec un compte réel (Europe, Linux), rapporté par l’utilisateur | `login` a ouvert le navigateur et enregistré la session. Trois processus distincts ont ensuite affiché `session reused`, `session renewed`, puis `session reused`. Les trois outils de catalogue et de FIT étaient visibles dans chaque réponse MCP 2025-06-18. | Connexion autonome, trousseau, renouvellement demandé et découverte d’outils validés sur cet environnement. Les appels de catalogue et FIT ont ensuite réussi (section 14.1.2). L’expiration naturelle et Windows restent à vérifier. |
| Portée historique partiellement vérifiée | Le texte de la mire d’autorisation a semblé indiquer une limite de 24 heures. Le catalogue a renvoyé des activités antérieures, dont une du 11 novembre pour l’essai du 16. Le [guide du catalogue](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/tools.md#querysportrecords) documente des requêtes à dates explicites pour des activités plus anciennes. | L’accès historique au catalogue est confirmé pour les jours testés, et au FIT pour l’activité du 11 novembre. La profondeur maximale et le quota restent à établir séparément. |

Depuis la racine du dépôt, avec l'environnement Python du projet et un
trousseau déverrouillé :

```bash
python -m pip install -e ".[coros-probe]"
fit-coros-probe --region eu login
fit-coros-probe --region eu probe
fit-coros-probe --region eu probe --refresh
fit-coros-probe --region eu probe
```

`login` ouvre le navigateur COROS. Les trois commandes suivantes sont exécutées
dans des processus distincts : reprise, renouvellement, puis reprise avec la
session renouvelée. Elles affichent un bilan texte, sans jetons. Pour nettoyer,
`fit-coros-probe --region eu logout` retire uniquement la session locale du
prototype. Une seule session expérimentale est conservée par région ; ce
rangement ne constitue pas la future identité de compte du registre.

#### 14.1.2 Étape 3 — premier contrôle de l’accès aux activités anciennes

Choisir dans Training Hub un jour connu où figurent des activités, datant de
plus de 24 heures. Exécuter la commande avec la session du prototype déjà
autorisée (remplacer la date d’exemple) :

```bash
fit-coros-probe --region eu activities --on 2026-11-08
```

La commande appelle uniquement `querySportRecords` pour ce jour avec une limite
de 20. Elle affiche le nombre de résultats, sans titres ni identifiants, et
la période rapportée par COROS lorsqu’elle figure dans le résumé reconnu.
Comparer avec Training Hub. Une réponse non reconnue est signalée séparément
d’un résultat vide. Un résultat non vide pour le jour ancien constitue un
indice d’accès historique au catalogue ; confirmer la correspondance du jour.
Un résultat vide isolé ne prouve pas une restriction de 24 heures : répéter avec
un autre jour connu et un jour récent servant de contrôle. Cette commande ne
vérifie ni l’exhaustivité d'une période ni l’accès au fichier FIT.

**Résultat réel (Europe, Linux) :** le catalogue a renvoyé une activité pour
chacun des 11 et 15 novembre 2026. L'activité du 11 est antérieure de plus de
24 heures à l'essai. La réponse contient un bloc MCP `text` dont la valeur
est elle-même une chaîne encodée, avec un résumé et des champs
`startTimestamp`, `endTimestamp`, `LabelId` et `SportType`. Cela explique
l'erreur du premier essai : le prototype vérifiait la forme du résumé avant de
décoder la chaîne. Le correctif accepte ce format ainsi que le texte direct.
Les tests utilisent des données synthétiques de même forme. Aucun identifiant
ou détail personnel de l'essai ne figure dans le dépôt.

**Contrôle FIT réel réussi :** l’utilisateur a confirmé que le FIT du
11 novembre 2026 a été téléchargé avec la même session et converti en Markdown
par FitToMd. Commandes de reproduction :

```bash
fit-coros-probe --region eu fit --on 2026-11-11 --output /tmp/coros-2026-11-11.fit
fit-to-md /tmp/coros-2026-11-11.fit
```

La commande n'émet une demande FIT que si le catalogue renvoie exactement
une activité identifiable sur la journée. Elle transmet son identifiant et
son sport à `downloadActivityFitFiles`, accepte un seul fichier binaire
intégré à la réponse MCP et vérifie sa signature et sa longueur avant de
l'écrire. Le fichier de destination doit être nouveau. Aucun lien temporaire
n'est consulté par ce prototype. La conversion locale vérifie ensuite que le
FIT est exploitable. L’équivalence avec un export manuel Training Hub n’a pas
été comparée. Cet essai confirme l’accès et la conversion d’un FIT de plus de
24 heures pour ce compte, sans établir la profondeur maximale de l’historique.
Le traitement d’une plage saturée est vérifié séparément en section 14.1.3.

**Qualification L0 complémentaire (17 novembre 2026, Linux/Europe), rapportée
par l’utilisateur :** empreinte du compte identique après renouvellement et
reconnexion ; horaires de début/fin d’une activité parfaitement concordants avec
Training Hub ; activité présente le jour attendu ; timestamps et heures UTC
inchangés lors du changement de fuseau, avec représentation locale adaptée.
Aucune activité proche de minuit disponible : cette frontière reste à qualifier.
Aucune comparaison d’identité avec un autre compte n’a été rapportée. Ces
résultats complètent les preuves précédentes sans étendre leur portée à Windows.

#### 14.1.3 Étape 3 — détection d’une réponse tronquée

Dans Training Hub, relever le nombre d’activités d’une plage fixe contenant
au moins deux activités. Pour un premier contrôle, utiliser la plage du 11 au
15 novembre si son total est inférieur à 20. Exécuter :

```bash
fit-coros-probe --region eu activities --on 2026-11-11 --through 2026-11-15 --limit 1
fit-coros-probe --region eu activities --on 2026-11-11 --through 2026-11-15 --limit 20
```

Comparer les bornes et nombres rapportés à Training Hub. Une limite de 1
force la saturation si COROS l'applique. Noter si le premier résultat indique
aussi un total, une troncature ou un curseur ; en cas de doute, ajouter
`--inspect-response` et ne partager que le constat, sans contenu d’activité.

**Résultat observé (Europe, Linux) :** du 11 au 15 novembre 2026 inclus,
Training Hub affiche 4 activités. Le prototype en a rapporté 1 avec
`--limit 1` et 4 avec `--limit 20`. La réponse est donc limitée même
quand d'autres activités correspondent à la plage. Ce test ne détermine
pas si la réponse expose un indicateur de troncature ou un curseur.

Pour éprouver la stratégie de découpage réelle, reprendre avec une plage
contenant plus de 20 activités selon Training Hub, puis demander deux
sous-plages de jours disjointes. Recouper les sous-plages qui atteignent la
limite jusqu’à obtenir des réponses non saturées. Comparer le total et,
localement si nécessaire, les activités présentes avec Training Hub.
Une journée saturée sans reprise documentée empêche de conclure à
l’exhaustivité. Le succès sur une plage ne garantit pas que COROS ne tronque
jamais silencieusement. Ces diagnostics ne demandent aucun fichier FIT.
`--through` et `--limit` sont des paramètres du prototype, pas des ajouts
au périmètre de la CLI de synchronisation V1.

**Essai sur une plage réellement saturée (Europe, Linux) :** du 4 octobre
au 15 novembre 2026 inclus, Training Hub affiche 22 activités. COROS en
rapporte 20 sur la plage entière avec une limite de 20. Après découpage :

| Sous-plage inclusive | Nombre rapporté |
| --- | ---: |
| 4 au 8 octobre | 2 |
| 9 au 27 octobre | 9 |
| 28 octobre au 15 novembre | 11 |
| **Total** | **22** |

Le total concorde avec Training Hub et aucune sous-plage n’atteint le plafond.
Il s’agit d’une validation sur le nombre, pas d’une comparaison systématique
des identifiants ni d’une garantie de comportement sur toutes les plages.

**Règle retenue pour la V1 :** tant qu’aucune pagination native n’est
confirmée, une réponse qui atteint le plafond doit provoquer un découpage
en jours calendaires disjoints dans le référentiel de requête COROS. Remplacer
le résultat parent par les résultats enfants ; vérifier leurs bornes,
dédupliquer les identités et appliquer le filtre exact de la machine.
Si une journée seule atteint encore la limite, si les bornes sont incohérentes
ou si la réponse est inexploitable, signaler la découverte comme incomplète
et garder la référence incrémentale inchangée.

### 14.2 Décisions sur les périodes et la nouveauté

La numérotation d'origine est conservée pour le suivi des réponses.

| N° | Décision utilisateur / suite à donner |
| --- | --- |
| 14 | Jours calendaires, aujourd'hui inclus : confirmé. |
| 15 | Fuseau de la machine exécutante ; le fuseau du compte n'est pas retenu. |
| 16 | Borne basse inclusive confirmée après clarification ; sélection par début conservée. |
| 17 | Suggérer sept jours au premier lancement ; attendre une période explicite avant de télécharger. |
| 18 | Repère de dernière découverte complète et relecture de sa journée ; les imports tardifs hors fenêtre sont récupérables par période explicite. |
| 19 | `--init-coros-reference DATE` repositionne le repère sans télécharger. |
| 20 | Périodes explicites indépendantes de la référence : confirmé. |
| 21 | Aucun `--until`, filtre sport, historique complet ou durée heures/semaines en V1. |

### 14.3 Décisions sur les fichiers et la reprise

| N° | Décision utilisateur / suite à donner |
| --- | --- |
| 22 | Rapports générés par défaut ; exception `--download-only`. |
| 23 | FIT, rapports et registre dans le dossier output ; secrets dans le trousseau conformément au point 39. |
| 24 | Destination existante obligatoire. |
| 25 | Nommage par date, heure et minute, sans suffixe ; conflit inattendu refusé sans écrasement. |
| 26 | Aucun import/reconnaissance des FIT manuels. |
| 27 | Modifications distantes ignorées. |
| 28 | Aucune interface de forçage ou régénération COROS. |
| 29 | Aucune détection par empreinte des rapports édités. |
| 30 | FIT non pris en charge : reprise uniquement sur action explicite, pas automatique. Conversion locale existante utilisable ; `conversion_unsupported` demeure terminal, sans reconnaissance automatique du rapport manuel en V1 (L0). |
| 31 | Validé : SQLite, transactions, finalisation atomique des fichiers, verrou système, sauvegarde du dossier à l'arrêt. Pas de reconstruction automatique du registre. |
| 32 | Aucun suivi de changement de destination/déplacement ni destinations multiples. |

### 14.4 Décisions d'exploitation et questions restantes

| N° | Décision utilisateur / suite à donner |
| --- | --- |
| 33 | stdout réservé au bilan ; codes de sortie proposés confirmés. |
| 34 | Bilan texte uniquement ; sortie structurée retirée du périmètre à la demande de l’utilisateur. |
| 35 | Progression simple par activité et indication de réussite/échec. |
| 36 | Aucune tâche planifiée. Catalogue : trois tentatives au total, 10 s par tentative, pauses 1 puis 2 s, uniquement réseau/HTTP 408/429/5xx ; Retry-After plus long = report. Aucune relance OAuth/MCP/métier/décodage ni FIT (L0). |
| 37 | Compteur quotidien persistant et plafond local de 50 demandes FIT individuelles sur 24 heures glissantes ; une réponse perdue reste comptée. Registre perdu/inaccessible : acquisition refusée jusqu’à restauration fiable, sans budget neuf implicite (L0). Fenêtre serveur et appels externes restent à clarifier. |
| 38 | Clarifié et décidé : si deux commandes utilisent le même dossier, la seconde s'arrête immédiatement avec un message. |
| 39 | Trousseau utilisable sans interaction ; fonctionnalité indisponible s'il est inaccessible. Aucun secours en clair. |
| 40 | Déconnexion locale uniquement, sans révocation COROS. |
| 41 | Bibliothèque standard pour le sous-ensemble MCP/OAuth testé, SecretStorage sous Linux et backend explicite keyring sous Windows. `portalocker` est utilisé dès L2 pour les mutations de session et prévu en L3 pour les outputs. Résolution de fuseau via tzlocal/ZoneInfo et tzdata sous Windows. Limites et qualification dans la note L0. |
| 42 | Linux et Windows supportés. Aide, erreurs et bilans COROS en anglais ; documentation de conception en français (L0). |

L’utilisateur a validé le repère incrémental, le découpage du catalogue,
SQLite et la reprise manuelle des FIT non pris en charge, ainsi que le
garde-fou de quota. L0 fixe la conversion manuelle sans réconciliation,
la récupération du quota sans recréation implicite, les dépendances et la langue.
Le registre commun est placé initialement dans le répertoire d’état utilisateur
Linux ou LocalAppData Windows, par compte ; son chemin et son identité sont liés
au compte dans une entrée de trousseau distincte des jetons, conservée au logout.
La note L0 précise les chemins et les limites de première initialisation.

### 14.5 Critères de passage à l’implémentation

Les essais sur Linux et les décisions fonctionnelles validées permettent
d’engager l’implémentation V1. Avant livraison :

- compléter la comparaison d’identité avec un autre compte et la sélection aux
  frontières de jours ; stabilité du même compte et conversion entre fuseaux
  observées sur l’échantillon L0 Linux/Europe ;
- vérifier le trousseau et les opérations sur les fichiers sous Windows ;
- comparer le FIT témoin avec l’export manuel Training Hub et tester le découpage
  sur d’autres cas, notamment une journée saturée ;
- tester hors ligne la persistance, les interruptions, le quota et la reprise ;
- conserver le garde-fou local tant que le fonctionnement exact du quota COROS
  n’est pas clarifié.

## 15. Découpage de livraison proposé

1. **Validation de faisabilité :** revue documentaire terminée (section 14.1).
   Prototype OAuth/MCP réalisé et testé hors ligne (section 14.1.1). La connexion interactive, la reprise et le renouvellement sont validés sur
   Linux pour un compte européen. Le catalogue historique et la conversion d’un
   FIT témoin téléchargé sont validés (section 14.1.2). Restent notamment
   le contrat de quota, la validation Windows et les limites du découpage du
   catalogue sur d’autres cas (section 14.1.3).
2. **Acquisition bornée :** connexion, périodes absolues/relatives, téléchargement
   seul, stockage et tests de dates et de persistance.
3. **Synchronisation fiable :** registre, incrémental borné, compteur de quota,
   verrouillage et reprise après interruption.
4. **Parcours complet :** conversion, collisions, options existantes, bilans,
   documentation, bilan texte et validation sur Linux/Windows.

La fonctionnalité est considérée livrée lorsque les trois modes de récupération
sont couverts, que les échecs se reprennent sans doublons, que les tests et les
contrôles du dépôt passent et que les décisions encore ouvertes ne conditionnent
plus le fonctionnement documenté.
