# Architecture

## La thèse

Le produit n'est ni un générateur d'emails, ni un voicebot : c'est un **moteur de décision
commerciale**. Email, WhatsApp et téléphone ne sont que des façons d'exécuter une décision. Le
code est organisé pour que cette phrase soit vraie techniquement, pas seulement dans le pitch :
la logique commerciale ne connaît aucun fournisseur.

## Hexagonale, et pourquoi ça compte ici

```
        entrypoints (FastAPI, CLI)          ← adapters primaires
                    │
              application                    ← use cases
                    │
                 domain                      ← modèle, triage, enjeu, politique (zéro dépendance)
                    │
                  ports                      ← interfaces
                    │
   HubSpot · Resend · Meta · Retell · Exa · OpenRouter · JSON local
                                             ← adapters secondaires
```

Trois bénéfices concrets, pas théoriques :

**La dégradation devient une décision de câblage.** Avant, « ça doit marcher sans clé API » se
traduisait par des `if not api_key: print(...)` dispersés dans chaque fonction. Désormais le
composition root (`container.py`) choisit `HubSpotCrmAdapter` ou `JsonFileCrmAdapter`,
`ResendEmailAdapter` ou `ConsoleEmailAdapter`. Le domaine ignore l'arbitrage, et le chemin de
code exercé en démo est le même qu'en production.

**Les tests tournent sans réseau.** 75 tests couvrent le triage, le routage par enjeu, la
résolution de canal, la boucle de scan, la politique d'autonomie et la relecture des messages —
en moins d'une seconde, sans HubSpot, sans LLM. C'est la mesure objective que la logique métier
est bien découplée.

**Changer de fournisseur est un fichier.** Passer de HubSpot à Salesforce, de Retell à Vapi,
d'OpenRouter à l'API OpenAI directe : un adapter, aucun impact sur le domaine.

## La boucle autonome

```
Trigger.dev (cron 5 min) ──> POST /scan
   1. HubSpot search : deals modifiés depuis (pointeur − 5 min de recouvrement)
   2. déduplication par id        ← l'index de recherche HubSpot est eventually consistent
   3. triage déterministe, zéro LLM :
        nouveau lead · changement de stade · relance échue · inactivité prolongée
   4. plafond par scan            ← un pic d'activité CRM ne doit pas faire exploser la facture
   5. cycle de décision sur les opportunités retenues
```

Quatre décisions de conception méritent d'être explicitées :

**Le premier scan est un inventaire, pas un delta.** Sans cela, une opportunité jamais vue et
non modifiée récemment resterait invisible pour toujours — or les deals dormants sont
précisément ceux qui valent de l'argent. Au premier passage, l'agent adopte le portefeuille
existant : un deal actif ne déclenche rien (on ne relance pas 500 prospects le jour de
l'installation), un deal dormant au-delà du seuil est immédiatement éligible.

**Le pointeur n'avance que si le CRM a répondu.** Sinon une panne HubSpot ferait passer une
fenêtre entière pour traitée, et les leads de cette fenêtre seraient perdus silencieusement.

**Le triage précède le raisonnement.** Un scan peut remonter des centaines de deals ; faire
raisonner un modèle sur chacun coûterait cher pour conclure, la plupart du temps, qu'il n'y a
rien à faire. Le triage est une fonction pure et gratuite.

**Le polling n'est pas un choix.** Les webhooks HubSpot exigent une application publique et ne
sont pas disponibles sur un compte développeur gratuit. La cadence de 5 minutes est dictée par
la Search API, plafonnée à 4 requêtes/seconde partagées.

## Le routage par enjeu

Si la décision commerciale est le produit, alors *combien de raisonnement cette décision
mérite* est elle-même une décision. `domain/stakes.py` tranche, en fonction pure et auditable :

| Situation | Modèle |
|---|---|
| Montant ≥ 50 k€, stade tardif, objection non résolue, changement de stade | `openai/gpt-5.6-sol` |
| Le reste | `openai/gpt-5.6-luna` |

Les trois identifiants ont été vérifiés contre le catalogue public d'OpenRouter, pas seulement
lus dans une documentation.

Chaque cycle journalise son motif de routage — le coût reste explicable après coup. Les
fallbacks entre modèles sont natifs côté OpenRouter (champ `models[]`), donc pas de retry à
écrire.

## Le contrôle de l'opérateur

Le prompt décrit au modèle ce qu'il a le droit de faire. Cela ne suffit pas : un prompt est une
consigne, pas un contrôle d'accès, et `escalate_to_human` est une action que le modèle *choisit*
d'appeler — la limite d'autonomie n'existerait donc que tant qu'il veut bien la respecter.

`domain/policy.py` impose ces limites en code. Chaque action sortante est évaluée **avant**
exécution, dans `ActionRegistry._guard` :

| Règle | Verdict | Ce qu'elle empêche |
|---|---|---|
| `mode_dry_run` | bloqué | Toute émission, en répétition ou en test |
| `destinataire_hors_liste` | bloqué | Écrire à un vrai prospect pendant une démo |
| `prix_hors_catalogue` | bloqué | Qu'un prix halluciné engage l'entreprise |
| `cadence_maximale` | bloqué | Le harcèlement d'un prospect |
| `mode_supervise` | validation | Toute action, tant que l'autonomie n'est pas accordée |
| `relecture_*` | validation | Ce qu'un directeur commercial ne laisserait pas partir |
| `montant_eleve` | validation | Qu'un gros deal se joue sans supervision |

Trois propriétés y sont délibérées :

**Le mode par défaut est `supervised`.** Le système est verrouillé à l'installation et
l'autonomie s'accorde explicitement. L'inverse ferait porter le coût de l'erreur au prospect.

**Un refus est rendu au modèle sous forme de message, pas d'exception.** L'agent peut alors
s'adapter — réécrire, changer de canal, escalader. Le prompt lui interdit en revanche
explicitement de reformuler pour contourner le filtre : retirer le mot « remise » en proposant
la même concession serait une faute grave.

**Une validation humaine lève les règles de validation, jamais les blocages.** Un opérateur
arbitre le commercial ; il ne contourne pas le mode simulation ni la liste de destinataires
autorisés, qui sont des garde-fous d'exploitation.

L'arbitrage se fait sans interface, par CLI ou API : `approvals list` montre le message exact
qui partira, `approve` l'envoie tel quel, `reject` le classe en laissant une trace dans le CRM.
Tant qu'une action figure dans cette file, **rien n'est parti chez le prospect**.

### La relecture, en deux couches

La détection par mots-clés attrape ce qui se nomme (« remise », « -20 % »). Elle ne voit pas ce
qui se formule : « je m'aligne sur leur tarif », « vous rentabiliserez en six mois, c'est
garanti », « l'offre expire ce soir ». Ces phrases engagent l'entreprise ou abîment la relation
sans contenir aucun mot du dictionnaire.

D'où une seconde couche : un modèle relit le message comme le ferait un directeur commercial
avant envoi, avec le contexte de l'opportunité — stade, montant, objections ouvertes, nombre
d'échanges. La même phrase ne se juge pas pareil au premier contact et en fin de négociation.
Il classe en huit motifs : engagement prix, engagement contractuel, promesse intenable,
concession prématurée, pression excessive, référence client, message inadapté au stade,
exposition juridique — et cite la phrase en cause, comme un manager pointe la ligne.

Quatre partis pris :

**Les couches se superposent, elles ne se remplacent pas.** Le socle lexical reste, toujours.
Échanger une garantie déterministe contre un jugement probabiliste serait une régression : un
classifieur se trompe, tombe en panne, et se contourne lui aussi.

**Le socle passe en premier, et court-circuite.** S'il a déjà tranché, inutile de payer un appel
de modèle pour confirmer une certitude — et le motif reste auditable.

**Échec fermé.** Modèle injoignable, JSON illisible, catégorie inventée : le message est retenu.
Un relecteur absent ne veut pas dire un message validé.

**Le sur-blocage est un risque traité, pas ignoré.** Un relecteur qui retient tout n'est pas
prudent : l'opérateur face à une file pleine de faux positifs finit par tout approuver sans
lire, et le contrôle disparaît. Le prompt de relecture liste donc explicitement ce qui doit
passer sans commentaire — prise de contact, relance courtoise, rappel du prix catalogue,
réponse à une objection qui argumente sur la valeur.

Coût : un appel du modèle économique par action sortante, soit ~0,0003 $. La relecture ne
s'exécute qu'en mode autonome — dans les autres modes le verdict est connu d'avance.

## La boucle vocale, fermée

```
place_phone_call ──> POST /v2/create-phone-call (contexte sérialisé en strings)
       │
   pendant l'appel ──> Retell appelle POST /retell/tool-call ──> ActionRegistry
       │
   fin d'appel ──> call_analyzed ──> transcript + résumé ──> cycle de décision ──> CRM
```

Trois contraintes vérifiées de l'API Retell sont câblées dans le code :
`retell_llm_dynamic_variables` n'accepte que des **chaînes** (d'où l'aplatissement explicite du
contexte) ; c'est `call_analyzed` et non `call_ended` qui porte le résumé et le sentiment ; le
payload des custom functions est bien `{name, args, call}`.

GPT-5.6 ne figure pas dans les modèles proposés nativement par Retell : l'agent vocal tourne sur
leur modèle, notre moteur garde la stratégie avant et après l'appel, et sert ses actions
pendant. Le registre d'actions est partagé entre les deux — l'agent texte et l'agent vocal
exécutent le même code, pas deux copies vouées à diverger.

## Le CRM comme source de vérité unique

Ce que l'agent apprend (objections, décisions, interactions) est écrit dans HubSpot sous forme
de notes préfixées — lisibles par un humain dans l'interface, re-parsables par l'agent au scan
suivant. Pas de base cachée à côté du CRM que les commerciaux ne verraient jamais.

Corollaire assumé : une action dont il ne reste aucune trace dans le CRM n'a pas eu lieu du
point de vue du commercial humain. Chaque action du registre écrit sa trace.

Seul l'état d'exécution du scanner (pointeur, relances programmées, journal de cadence) vit à
côté : c'est la mémoire de l'agent, pas la vérité commerciale, et les deux n'ont pas le même
cycle de vie.

## Sponsors intégrés

Trois, choisis parce qu'ils portent une fonction réelle du produit — pas pour cocher des cases.

| Sponsor | Fonction portée | Où vit le code |
|---|---|---|
| **OpenAI + OpenRouter** | Le raisonnement, son routage par enjeu, la relecture des messages | Entièrement chez nous (`adapters/intelligence/`) |
| **Trigger.dev** | L'autonomie : cron du scan, relances durables | Code chez nous, **exécution sur leur cloud** |
| **Exa** | Le « qu'est-ce que je ne sais pas ? » : actualité du prospect | Entièrement chez nous (`adapters/intelligence/exa_enrichment.py`) |

Écartés en connaissance de cause : **Auth0** (l'essentiel est de la configuration dashboard, et
son apport — la validation humaine — n'était démontrable qu'avec une UI), **Ambiguous AI**
(aucune documentation API publique trouvée, impossible de planifier dessus), **Mozilla**
(`any-llm` résout le même problème qu'OpenRouter ; faire les deux serait incohérent).

## Coûts

Tarifs relevés sur le catalogue OpenRouter, pas dans une documentation tierce :

| Modèle | Entrée / sortie par MTok | Coût d'un cycle |
|---|---|---|
| `openai/gpt-5.6-luna` | 0,20 $ / 1,20 $ | ~0,001–0,003 $ |
| `openai/gpt-5.6-terra` | 2,00 $ / 12,00 $ | — (fallback) |
| `openai/gpt-5.6-sol` | 2,00 $ / 10,00 $ | ~0,008–0,02 $ |

| Autre poste | Coût |
|---|---|
| Relecture d'un message sortant | ~0,0003 $ |
| Recherche Exa | ~0,007 $ par requête (20 $ de crédits offerts, sans carte) |
| Appel téléphonique Retell, 5 minutes | ~0,65 $ |

Deux constats. D'abord, Sol est **nettement moins cher qu'annoncé** dans les comparatifs
publics (2 $/10 $ et non 5 $/30 $), au point de coûter moins que Terra en sortie — le routage
vers le modèle capable est donc peu pénalisant. Ensuite, le LLM n'est jamais le poste dominant :
**la voix l'est, d'un facteur 30 à 600**. C'est ce qui justifie que l'agent réserve le téléphone
aux moments où il apporte plus que l'écrit.

## Ce qu'il reste à faire

- **Éprouver le discernement du relecteur** sur de vrais messages dès qu'une clé OpenRouter est
  disponible. Le comportement *autour* du modèle est testé ; son jugement ne l'est pas.
- **Vérifier le schéma exact de la signature Retell** (`X-Retell-Signature`) contre leur SDK :
  l'implémentation actuelle fait un HMAC-SHA256 standard.
- **Charger le dataset Kaggle** « CRM Sales Opportunities » dans HubSpot pour un portefeuille
  réaliste (`accounts.csv`, `products.csv`).
- **Migrer vers le versionnage par date de HubSpot** (`/crm/objects/2026-09/`) : les chemins
  v3/v4 utilisés restent supportés, mais ne sont plus la recommandation.
- **Remplacer les adapters JSON par Postgres** au-delà d'une instance — le port ne change pas.
