# COROS — décisions techniques L0

**Date :** 17 novembre 2026. **Statut :** décisions et diagnostics livrés ;
identité du même compte et horaires validés sur un échantillon Linux/Europe ;
frontière de minuit non qualifiée, J0 non atteint.
Références : [spécification](coros-sync-functional-specification.md),
[suivi des lots](coros-sync-implementation-plan.md).

## 1. Preuves et limites

Les essais rapportés dans la spécification §§14.1.1–14.1.3 établissent, sur
Linux/Europe pour un compte, la connexion navigateur, la reprise interprocessus,
le renouvellement demandé, l’accès à un catalogue ancien et la conversion d’un
FIT. Le découpage d’une plage a retrouvé 22 activités en nombre. Ils ne prouvent
ni l’égalité des ensembles d’identités, ni l’absence de troncature silencieuse,
ni la stabilité d’une identité de compte, ni les frontières de dates.

Les tests L0 utilisent exclusivement des réponses synthétiques. Ils prouvent
le comportement client pour ces réponses, pas celui du serveur. Les essais authentifiés complémentaires rapportés par l’utilisateur le
17 novembre 2026 sont consignés ci-dessous ; aucun nouveau FIT n’a été demandé
pour ces diagnostics.
Windows, l’expiration naturelle, la taille des secrets Windows et l’équivalence
avec les exports Training Hub restent à qualifier dans les lots concernés.

### Résultats réels complémentaires — 17 novembre 2026

Essais exécutés par l’utilisateur sous Linux, région Europe :

| Vérification | Résultat rapporté | Portée |
| --- | --- | --- |
| Identité du même compte | Empreinte identique après renouvellement et reconnexion | Stabilité observée pour ce compte, y compris après une nouvelle autorisation. |
| Horaires d’une activité connue | Concordance parfaite avec Training Hub des débuts et fins UTC/locales | Timestamps en secondes Unix et conversion UTC/local corroborés sur cet échantillon. |
| Jours interrogés | Activité présente le jour attendu dans les requêtes de jours limites | Cas observé en journée ; pas une preuve des frontières à minuit. |
| Changement de fuseau | Fuseau et représentation locale changent ; heures UTC et timestamps inchangés | Invariance des instants observée entre fuseaux. |

Aucune activité proche de minuit disponible : inclusion exacte aux frontières,
activité traversant minuit et référentiel des jours serveur non établis par cet
essai. Aucune comparaison avec un autre compte n’a été rapportée. Les empreintes
et timestamps réels ne sont pas recopiés dans le dépôt.

## 2. Identités et diagnostics

Contrat candidat de compte : `(fournisseur=coros, émetteur régional, sub)`.
L’émetteur est celui explicitement choisi pour la région, pas une valeur libre.
Le point `userinfo_endpoint` est lu dans la découverte OpenID de cet émetteur ;
son HTTPS, son origine identique, l’absence de query/fragment et l’égalité de
`issuer` sont imposés avant l’envoi du bearer. Les redirections ne sont pas suivies.
`sub` doit être une chaîne non vide, conservée sans normalisation. Aucun repli
sur nom, email, profil de santé, client OAuth ou contenu d’un jeton.

Clé activité candidate : `(coros, émetteur, sub, labelId, sportType)` ; les deux
derniers champs proviennent de la même activité. Un output lié à un autre compte
sera refusé avant mutation. Cette règle sera implémentée en L2/L3, pas dans le
prototype. La stabilité de `sub`, notamment après un nouvel enregistrement de
client OAuth, doit être observée ; un sujet pairwise lié au client serait un
blocage et imposerait un autre contrat établi, sans migration implicite.

```bash
fit-coros-probe --region eu identity
fit-coros-probe --region eu probe --refresh
fit-coros-probe --region eu identity
# Refaire login avec le même compte, puis identity dans un nouveau processus.
# Répéter ensuite avec un autre compte de test autorisé.
```

`identity` n’ouvre pas de navigateur. Il reprend/renouvelle la session comme les
autres diagnostics ; une rotation est sauvegardée avant tout appel suivant.
Il affiche seulement SHA-256 du tableau JSON `[issuer, sub]` encodé en UTF-8
avec l’échappement ASCII et les séparateurs JSON par défaut de Python. Cette
empreinte sert à la comparaison locale ; elle reste une donnée pseudonyme.
Ne pas publier d’empreintes réelles dans le dépôt. Les autres champs `userinfo`
ne sont ni affichés ni persistés. Une erreur opérationnelle retourne 1 ; une
interruption retourne 130. Aucun FIT ni registre de production n’est créé.

**Preuve du même compte acquise sur l’échantillon Linux/Europe :** empreinte
identique après renouvellement et reconnexion. **Complément restant :** empreinte
différente pour un autre compte autorisé. Si `userinfo` manque, si le
sujet change ou si le serveur refuse l’accès, consigner le constat sans inventer
d’identité. Les essais du même compte permettent d’engager L2 avec ce contrat ; la séparation
entre comptes devra être testée avec des fakes puis qualifiée réellement avant livraison.

## 3. Dates, enveloppe et fuseau

Le diagnostic reconnaît le résumé texte déjà observé, direct ou encodé comme
chaîne JSON, et les blocs d’activité numérotés contenant `LabelId`/`SportType`
et `startTimestamp`/`endTimestamp`. Il associe les champs à l’intérieur du même
bloc : aucun rapprochement par position de listes indépendantes. Les variantes
non reconnues restent non prises en charge pour l’inspection des dates.

Contrat corroboré sur l’échantillon réel, sans garantie générale du serveur :
timestamps entiers en secondes Unix,
convertis en instants UTC conscients du fuseau. Ni division automatique des
millisecondes, ni date déduite du titre ou du résumé. Champ absent, doublonné,
invalide, hors plage datetime ou fin antérieure au début : diagnostic incomplet,
code 1. Une différence entre nombre annoncé et observations reconnues échoue
aussi. Les commandes sans `--inspect-dates` conservent leur comportement.

```bash
python -m pip install -e '.[coros-probe]'
fit-coros-probe --region eu activities --on 2026-11-08 --inspect-dates
fit-coros-probe --region eu activities --on 2026-11-08 --through 2026-11-09 --inspect-dates
```

La sortie présente bornes demandées/rapportées, unité candidate, fuseau figé,
empreinte d’activité, secondes reconnues et dates ISO avec décalages UTC/local.
L’empreinte d’activité utilise `[issuer, labelId, sportType]` (champs chaînes) ;
c’est une référence de diagnostic, pas une clé de compte de production.
Éviter `--inspect-response` pour les preuves partageables : cette ancienne option
affiche volontairement le contenu privé brut sur stderr.

Le fuseau est obtenu une fois par `tzlocal.get_localzone()`, sous forme ZoneInfo
portant les règles historiques/DST, jamais par un offset courant fixe.
Installation `tzdata` sous Windows. Une résolution impossible ou accompagnée
d’un avertissement de configuration est refusée avant accès réseau.

Pour L1/L5 : figer T et le fuseau ; sélectionner le début sur `[début, T)`.
Enveloppe candidate en dates COROS inclusives :
`date_UTC(début) - 1 jour` à `date_UTC(T) + 1 jour`. Elle couvre les décalages
civils usuels de -12 à +14 heures si le serveur classe par date de début.
Appliquer ensuite le filtre exact sur les instants ; découper les plages
saturées en jours disjoints et dédupliquer les identités. Cette marge n’est
pas une garantie si le serveur utilise une autre règle. Refuser les bornes
non représentables ; ne pas les rabattre silencieusement.

**Acquis réel :** concordance des horaires, jour attendu et invariance des
instants entre fuseaux. **Protocole réel restant :** lorsque disponibles, choisir
des activités connues autour de minuit,
dont une traversant minuit ; comparer les jours demandés séparément et ensemble
avec Training Hub. Répéter avec deux fuseaux machine, noter le fuseau du compte,
vérifier unités et inclusion des deux jours limites. Contrôler précisément les
instants au début et à T, et une transition DST lorsque disponible. Ne conserver
que les constats anonymisés. Le lot 5 active la prévisualisation en lecture
seule avec l’enveloppe candidate. La qualification réelle reste ouverte pour
la découverte utilisée par le parcours avec acquisition.

## 4. Contrats entre contextes et dépendances

Le domaine Synchronisation sera indépendant de MCP, OAuth, HTTP, SQLite,
Activity, Reporting et des objets de session du contexte Connexion.
Les ports applicatifs suivants seront introduits avec les lots consommateurs :

| Port / frontière | Responsabilité et résultat attendu |
| --- | --- |
| Identité/session | Compte opaque confirmé, région et session utilisable ; rotation persistée avant usage ; erreurs sans secrets. |
| Catalogue | Fenêtre explicite, activités avec références et instants UTC ; réponse vide distincte d’une réponse inconnue ; complétude explicite. |
| Acquisition | Une identité par demande, résultat binaire ou échec typé ; aucune relance automatique FIT. |
| Registre output | Compte, référence, candidats, chemins, intégrité et états séparés ; transactions atomiques candidats/référence ; aucune réinitialisation implicite. |
| Quota compte | Réservation atomique avant chaque demande, budget glissant UTC, compteurs par jour/type ; réservation conservée en réponse perdue. |
| Fichiers/verrou | Réservation de chemin, temporaire sur même volume, validation puis finalisation sans écrasement ; verrou OS exclusif sans attente. |
| Secrets | Session et liaison compte/quota versionnées ; trousseau obligatoire, aucun secours en clair. |
| Horloge/fuseau | T UTC et ZoneInfo injectables, figés par exécution ; horloge monotone pour les délais. |

La couche application traduit les contrats entre Connexion et Synchronisation
et orchestre les ports. La CLI assemble les adaptateurs et présente les sorties.
La conversion utilisera `GenerateMarkdownReport` avec un FIT local, sans injecter
de dépendance COROS dans Activity ou Reporting. Les tests d’architecture couvrent
dès L0 la séparation du futur contexte Synchronisation.

Choix minimal : bibliothèque standard pour le sous-ensemble JSON-RPC MCP
2025-06-18, JSON/SSE et OAuth/PKCE déjà testé ; SecretStorage sous Linux et backend
Windows explicite keyring. Aucun SDK COROS ni SDK MCP général requis à ce stade.
Ce client n’est pas une implémentation générale de tous les transports MCP :
versions inconnues et formats non reconnus sont refusés. Revoir ce choix si le
serveur requiert des capacités non couvertes.

`tzlocal>=5` et `tzdata` conditionnel Windows figurent dans les extras
`[coros-probe]` et `[coros]` pour les opérations calendaires. L2 avance
`portalocker` dans l’extra de production `[coros]`
pour sérialiser les mutations et renouvellements de la session. Le verrou de
session est exclusif, sans attente, distinct du verrou d’output prévu en L3.
Il utilise `~/.local/state/fit-to-md/coros/connection.lock` sous Linux et
`%LOCALAPPDATA%/fit-to-md/coros/connection.lock` sous Windows. Le fichier vide
reste en place après libération. Le verrou d’output ne remplace pas les
transactions de quota communes aux outputs.

La session de production occupe l’entrée versionnée
`fit-to-md/coros/connection` (utilisateur `session`) du trousseau. Elle contient
la région, l’émetteur, le sujet, les jetons, l’expiration et l’état de
vérification. Une rotation est d’abord enregistrée avec l’identité à vérifier.
Si cette vérification est interrompue, la prochaine reprise peut la terminer
avec les jetons déjà enregistrés. Windows utilise une écriture native sur la
cible fixe afin d’éviter qu’une rotation laisse une copie de secours du jeton
précédent. La taille du contenu en UTF-16 est vérifiée avant l’écriture.

## 5. Quota, reprise et exploitation

- Emplacement initial Linux : `$XDG_STATE_HOME/fit-to-md/coros/<empreinte-compte>.sqlite3`,
  avec XDG absolu ; sinon `~/.local/state/fit-to-md/coros/`. Windows :
  `%LOCALAPPDATA%/fit-to-md/coros/<empreinte-compte>.sqlite3` ; absence de chemin
  système exploitable = erreur. Aucun fichier n’est créé par L0.
- Le trousseau de production conservera une liaison versionnée séparée des
  jetons : fournisseur, émetteur, sujet opaque, chemin absolu du quota et identité
  du registre. Tous les outputs du compte réutilisent cette liaison. Les sessions
  expérimentales `fit-to-md/coros-prototype/<région>` restent isolées.
- La création initiale n’est possible que pour un compte nouvellement initialisé,
  via `fit-to-md coros init-quota`, qui prépare la liaison dans le trousseau,
  publie la base SQLite puis active la liaison après vérification. Liaison/quota perdus,
  inaccessibles, incohérents ou restauration douteuse : acquisitions interdites,
  aucune recréation par login, logout, changement d’output ou attente de 24 h.
  Restaurer un état fiable à l’arrêt avant reprise. Les cas ambigus exigent une
  intervention explicite ; aucune commande de remise à zéro n’est prévue en V1.
- Logout supprime la session, conserve liaison de quota et données locales.
  Le protocole de première initialisation sera transactionnellement testé en L4 ;
  le trousseau seul ne permet pas de prouver qu’une machine n’a jamais été utilisée.
- Limite locale : 50 réservations sur 24 heures glissantes UTC. Le refus serveur
  prévaut ; demandes externes et fenêtre serveur restent inconnues.
  L4 reconnaît HTTP 429 sur l’appel FIT et expose l’échéance UTC si l’en-tête
  `Retry-After` contient un délai ou une date HTTP exploitable. Le format d’un
  éventuel refus de quota métier MCP reste à qualifier.
- `conversion_unsupported` demeure terminal même après une conversion locale
  manuelle. Le rapport manuel n’est pas réconcilié ; aucun téléchargement ou essai
  de conversion automatique ne résulte de sa présence.
- Messages COROS en anglais ; documentation de conception en français.

Pour le catalogue : trois tentatives au total par lecture, délai réseau
de 10 s par tentative, pauses 1 s puis 2 s. Seulement erreurs réseau et HTTP
408/429/5xx ; pas de relance des erreurs OAuth, MCP/métier ou de décodage. Un
Retry-After supérieur à la pause prévue provoque un report sans attente longue.
Ces limites sont par requête, pas un budget global de découverte. L0 conserve
les appels uniques et le délai réseau existant de 10 s du prototype.
Les demandes FIT ne sont jamais relancées automatiquement. Le transport direct
reste retenu ; aucune consultation d’URL temporaire ajoutée. Toute nécessité
future d’URL exige validation domaines/redirections/expiration et absence de
transmission du bearer MCP à un hébergeur de fichiers.

## 6. Sources et conditions d’usage à vérifier

- [Guide officiel des outils COROS](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/tools.md) :
  dates `yyyyMMdd`, plafond de résultats et couple activité/sport ; aucune preuve
  supplémentaire de stabilité du compte ou de frontières temporelles.
- [Client officiel](https://github.com/coroslab/COROS-MCP/blob/main/skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py),
  [FAQ](https://github.com/coroslab/COROS-MCP/blob/main/docs/en/faq.md) et
  [aide COROS](https://support.coros.com/hc/en-us/articles/50841795180948-Connect-Your-COROS-to-AI) :
  références de protocole et de quota, divergences conservées dans la spécification.
- [Build on COROS MCP](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP) :
  accès OAuth aux données du seul utilisateur autorisant. Cela ne suffit pas à
  établir une autorisation explicite d’archivage local récurrent.
- [tzlocal](https://pypi.org/project/tzlocal/) et
  [portalocker](https://portalocker.readthedocs.io/en/latest/) : résolution locale
  Linux/Windows et verrou OS multiplateforme.

Avant livraison : vérifier les conditions applicables à l’archivage répété,
les règles de conservation/export et les restrictions des usages de synchronisation ;
demander une clarification COROS si ambiguës. Clarifier aussi fenêtre de quota,
partage entre clients et demandes échouées, sans épuiser le quota pour le mesurer.
Ne pas contacter COROS automatiquement. Les vérifications non faites restent
ouvertes dans le suivi, même lorsque les tests client passent.
