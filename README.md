# Autonomous Revenue Agent

**Un commercial numérique qui vit dans votre CRM, pas dans une fenêtre de chat.**

Il ne génère pas des emails. Il scanne le portefeuille, repère les opportunités qui meurent en
silence, **décide** de la meilleure action commerciale — email, WhatsApp, appel téléphonique,
attente délibérée, escalade humaine — l'exécute, puis écrit tout ce qu'il apprend dans le CRM.

> Le canal n'est pas le produit. **La décision commerciale est le produit.**

---

## Le problème

Aucun commercial ne perd un deal parce qu'il écrit mal ses emails. Il le perd parce que
personne ne rouvre une opportunité endormie depuis deux mois.

Un CRM est un système passif : il **enregistre** ce qui s'est passé, il ne fait rien. Les deals
dormants n'envoient aucune notification — c'est précisément leur problème. Il n'existe aucun
événement « ce prospect vous oublie depuis 62 jours ». Et le jour où un commercial reprend
enfin le dossier, le contexte a disparu : pourquoi ce silence ? Était-ce le prix, ou la
contrainte contractuelle mentionnée lors d'un appel en juin ?

Un assistant IA classique ne résout rien à ce problème, pour une raison structurelle : **il
faut aller le chercher.** Il attend qu'on lui parle. Or ici, personne ne parle. C'est justement
ça, le problème.

## Ce que fait l'agent, concrètement

Voici un cycle réel, celui qui se déclenche au premier `scan` de la démo :

```
Trigger.dev (cron, 5 min) ──> POST /scan
  │
  ├─ 1. HubSpot : deals modifiés depuis (dernier pointeur − 5 min de recouvrement)
  ├─ 2. Triage déterministe, zéro appel LLM :
  │      nouveau lead · changement de stade · relance échue · inactivité prolongée
  │      → « Acme Co : aucune action depuis 62 jours »
  ├─ 3. Évaluation de l'enjeu : 68 000 € → raisonnement stratégique (modèle capable)
  ├─ 4. Le moteur de décision lit le dossier complet, et comprend que le silence
  │      n'est pas un désintérêt : une objection de juin disait « on ne peut pas
  │      changer avant la fin du contrat en cours »
  ├─ 5. Il constate que le décideur budgétaire n'a jamais été engagé, cherche
  │      l'actualité récente d'Acme (Exa), et rédige
  ├─ 6. Politique + relecture avant envoi → file de validation ou envoi direct
  └─ 7. Trace écrite dans HubSpot : ce qui n'y est pas écrit n'a pas eu lieu
```

Et quand le prospect répond — par WhatsApp, ou en décrochant le téléphone — **le même cycle
repart au même endroit**, avec la même mémoire. Un prospect ne recommence jamais son histoire
parce qu'il a changé de canal.

## Pourquoi ceci ne peut pas être un chatbot

Trois propriétés du produit disparaissent si on le sort de son environnement.

**1. Le déclencheur est une absence, pas un message.** Les quatre déclencheurs de l'agent
(nouveau lead, changement de stade, relance échue, inactivité) sont des événements que
*personne n'émet*. Deux d'entre eux sont littéralement des non-événements : ils naissent du fait
que rien ne s'est passé. Aucune fenêtre de chat ne peut produire ça — il n'y a personne pour
taper le message « ce deal s'éteint ».

**2. Le CRM est la mémoire longue, et elle est partagée.** Tout ce que l'agent apprend est écrit
dans HubSpot sous forme de notes préfixées : lisibles par un humain dans l'interface,
re-parsables par l'agent au scan suivant. L'agent et le commercial travaillent donc sur **le
même cerveau**, pas sur deux historiques divergents. Il n'y a pas de base cachée à côté du CRM
que les commerciaux ne verraient jamais.

**3. L'agent texte et l'agent vocal sont le même agent.** Pendant un appel téléphonique, quand
l'agent vocal consulte une fiche produit ou consigne une objection, il appelle par webhook
**exactement le même registre d'actions** que le moteur écrit. Pas deux implémentations vouées à
diverger : un seul code, deux modalités. L'appel se termine, `call_analyzed` renvoie le
transcript, et un nouveau cycle de décision repart de ce qui s'est réellement dit.

## L'environnement façonne le code

Ce ne sont pas des détails d'intégration : chacune de ces contraintes a changé une décision de
conception. C'est ce qui distingue un agent *dans* un environnement d'un agent *à côté*.

| Contrainte réelle de l'environnement | Ce que le code fait en conséquence |
|---|---|
| L'index de recherche HubSpot est *eventually consistent* | Fenêtre de recouvrement de 5 min à chaque scan + déduplication par identifiant, sinon des leads passent entre les mailles |
| La Search API HubSpot plafonne à 4 req/s partagées | Cadence de scan à 5 min — scanner plus souvent ne remonterait rien de plus |
| Les webhooks HubSpot exigent une app publique | Le polling n'est pas un pis-aller, c'est le seul mécanisme disponible : il est donc conçu pour, pointeur inclus |
| Une panne CRM ne doit pas faire perdre une fenêtre | Le pointeur **n'avance que si le CRM a répondu**, sinon la fenêtre est rejouée |
| Le jour 1, le CRM contient déjà 500 deals | Le premier scan est un *inventaire*, pas un delta : on adopte le portefeuille sans relancer les deals actifs, mais les dormants sont éligibles immédiatement |
| `retell_llm_dynamic_variables` n'accepte que des chaînes | Le contexte de l'opportunité est aplati explicitement avant l'appel |
| C'est `call_analyzed`, pas `call_ended`, qui porte le résumé | C'est cet événement-là qui alimente la décision post-appel |
| Meta renvoie le numéro sans `+` | La résolution du prospect entrant se fait sur les chiffres seuls |
| Un pic d'activité CRM ne doit pas exploser la facture | Plafond de décisions par scan, surplus reporté au tick suivant — jamais perdu |

## Le contrôle de l'opérateur

Un agent qui écrit à de vrais prospects et passe de vrais appels ne peut pas reposer sur la
bonne volonté d'un modèle. **Le prompt est une consigne ; un contrôle d'accès est une
garantie.** Les limites d'autonomie sont donc appliquées en code (`domain/policy.py`), évaluées
**avant** toute action sortante — et `escalate_to_human` ne peut pas être la seule protection,
puisque c'est une action que le modèle *choisit* d'appeler.

| Règle | Verdict | Ce qu'elle empêche |
|---|---|---|
| `mode_dry_run` | bloqué | Toute émission, en répétition ou en test |
| `destinataire_hors_liste` | bloqué | Écrire à un vrai prospect pendant une démo |
| `prix_hors_catalogue` | bloqué | Qu'un prix halluciné engage l'entreprise |
| `cadence_maximale` | bloqué | Le harcèlement d'un prospect |
| `mode_supervise` | validation | Toute action, tant que l'autonomie n'est pas accordée |
| `relecture_*` | validation | Qu'un engagement de prix ou une promesse intenable parte seul |
| `montant_eleve` | validation | Qu'un gros deal se joue sans supervision |

Trois partis pris :

**Le défaut est `supervised`.** Le système est verrouillé à l'installation et l'autonomie
s'accorde explicitement. L'inverse ferait porter le coût de l'erreur au prospect.

**Un refus est rendu au modèle en texte, pas en exception.** L'agent peut alors s'adapter :
réécrire, changer de canal, escalader. Le prompt lui interdit en revanche de contourner le
filtre par reformulation — retirer le mot « remise » en proposant la même concession serait une
faute grave.

**Une validation humaine lève les demandes de validation, jamais les blocages.** Un opérateur
arbitre le commercial ; il ne désactive pas le mode simulation ni la liste blanche.

### La relecture avant envoi, en deux couches

Même en mode autonome, chaque message est relu avant de partir.

Le **socle lexical** est déterministe, gratuit, instantané, toujours actif : il attrape ce qui
se nomme (« remise », « -20 % », « offert »). Il ne voit pas ce qui se formule.

La **relecture sémantique** lit le message comme le ferait un directeur commercial, avec le
stade du deal, le montant et les objections ouvertes — parce que « on peut s'arranger sur le
prix » est anodin au premier contact et grave en fin de négociation. Elle attrape ce qu'aucun
dictionnaire ne couvrira : *« je m'aligne sur leur tarif »*, *« je vous garantis un ROI en six
mois »*, *« l'offre expire ce soir »*.

Les deux se superposent, elles ne se remplacent pas : une garantie probabiliste ne remplace pas
une garantie déterministe. Et **la relecture échoue fermé** — si le modèle est indisponible ou
répond mal, le message est retenu pour un humain. Un relecteur absent ne veut pas dire un
message validé.

```bash
revenue-agent approvals list             # ce qui attend un arbitrage, message exact inclus
revenue-agent approvals approve <id>     # part tel quel, mot pour mot
revenue-agent approvals reject <id> --note "trop tôt"
```

L'humain valide **ce qui partira réellement**, jamais un résumé. Tant qu'une action est dans
cette file, rien n'est parti chez le prospect — et un refus laisse sa trace dans le CRM, au
même titre qu'un envoi.

---

## Installation et lancement

**Prérequis :** Python 3.11 ou plus. Rien d'autre — ni base de données, ni Docker, ni compte
externe.

```bash
git clone <url-du-dépôt> && cd Agents-Everywhere-Hackathon-Commercial-
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # toutes les variables sont commentées et optionnelles
```

Le fichier `.env` est chargé automatiquement, sans jamais écraser une variable déjà exportée
dans le shell : une valeur oubliée en local ne peut pas contaminer un déploiement.

### Lancer sans aucune clé (recommandé pour découvrir)

Le projet tourne **immédiatement, sans une seule clé d'API**. Chaque intégration externe a un
adapter simulé, et le chemin de code exercé est exactement le même qu'en production : seule la
destination change (un log au lieu d'un envoi réel).

```bash
python -m revenue_agent.entrypoints.cli status   # ce qui est branché, ce qui est simulé
python -m revenue_agent.entrypoints.cli scan     # scan du CRM + cycle de décision
python -m revenue_agent.entrypoints.cli demo     # 3 scénarios commerciaux
python -m revenue_agent.entrypoints.cli approvals list   # la file de validation
```

Le premier `scan` amorce un CRM local de démonstration, adopte le portefeuille, détecte
l'opportunité dormante depuis 62 jours et déclenche un cycle de décision dessus.

Sans `OPENROUTER_API_KEY`, tout le mécanisme tourne — scan, triage, routage, traçage CRM, file
de validation — mais l'agent ne *décide* rien : il consigne l'événement et l'annonce
explicitement. C'est délibéré : un agent de repli qui simulerait des décisions plausibles serait
pire qu'un agent qui annonce son incapacité.

### La seule clé qui change tout

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
python -m revenue_agent.entrypoints.cli scan
```

**C'est la seule clé nécessaire pour voir l'agent raisonner et décider pour de vrai.** Elle
active le moteur de décision LangGraph et la relecture sémantique des messages. Tout le reste
(CRM, email, WhatsApp, voix, recherche web) continue de tourner en simulé, sans rien casser.

À récupérer sur [openrouter.ai/keys](https://openrouter.ai/keys) — une clé donne accès à tous
les modèles, sans compte séparé chez chaque fournisseur.

### Les clés, une par une

| Variable | Requise ? | Ce qu'elle active | Sans elle |
|---|---|---|---|
| `OPENROUTER_API_KEY` | **La seule qui compte** | Le raisonnement et la relecture sémantique | L'agent consigne les événements sans décider |
| `HUBSPOT_TOKEN` | optionnelle | CRM réel (deals, contacts, notes, appels) | CRM JSON local pré-rempli |
| `EXA_API_KEY` | optionnelle | Recherche web sur l'actualité du prospect | L'agent sait qu'il n'a pas cette capacité |
| `RESEND_API_KEY` | optionnelle | Envoi d'emails réels | Email écrit en console |
| `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` | optionnelles | WhatsApp réel via Meta Cloud API | Message écrit en console |
| `RETELL_API_KEY` + `RETELL_FROM_NUMBER` + `RETELL_AGENT_ID` | optionnelles | Appels téléphoniques réels | Appel écrit en console |
| `SCAN_SHARED_SECRET` | si serveur exposé | Protège `/scan`, qui consomme des tokens | Endpoint non protégé (averti au démarrage) |
| `RETELL_WEBHOOK_SECRET` | si serveur exposé | Vérifie la signature des webhooks Retell | Signature non vérifiée (averti au démarrage) |
| `WHATSAPP_VERIFY_TOKEN` | si WhatsApp réel | Handshake de vérification exigé par Meta | Le webhook Meta ne peut pas être enregistré |

Où les obtenir : [openrouter.ai](https://openrouter.ai/keys) ·
[HubSpot Private App](https://developers.hubspot.com) (scopes `crm.objects.deals.read/write`,
`crm.objects.contacts.read`, `crm.objects.notes.write`) · [exa.ai](https://exa.ai) ·
[resend.com](https://resend.com) · [Meta Cloud API](https://developers.facebook.com) (mode test
gratuit, 5 numéros) · [retellai.com](https://retellai.com).

`status` affiche à tout moment ce qui est réel et ce qui est simulé : une démo ne doit jamais se
croire branchée alors qu'elle ne l'est pas.

### Régler l'autonomie

L'agent démarre **verrouillé**. Ces variables décident de ce qu'il a le droit de faire seul.

```bash
export AGENT_MODE=supervised            # défaut — il rédige, rien ne part sans validation
export AGENT_MODE=autonomous            # agit seul, sauf remise / gros deal / prix inconnu
export AGENT_MODE=dry_run               # rien ne sort, même approuvé

export MAX_AUTONOMOUS_AMOUNT=50000      # au-delà, validation obligatoire
export MAX_OUTBOUND_PER_DAY=3           # cadence maximale par prospect
export ALLOWED_RECIPIENTS=vous@exemple.test,+33600000000   # liste blanche
```

> ⚠️ **Avant de brancher une vraie clé d'envoi** (`RESEND_API_KEY`, `WHATSAPP_TOKEN`,
> `RETELL_API_KEY`), renseignez `ALLOWED_RECIPIENTS` avec vos propres coordonnées. C'est le
> garde-fou qui empêche un message de partir chez un vrai prospect pendant une démonstration, et
> aucune validation humaine ne peut le contourner.

Autres réglages : `COMPANY_NAME`, `MODEL_ROUTINE`, `MODEL_STRATEGIC`, `SCAN_INACTIVITY_DAYS`
(défaut 14), `SCAN_MAX_DECISIONS` (défaut 5), `SCAN_OVERLAP_MINUTES` (défaut 5), `STATE_FILE`,
`LOG_LEVEL`, `LOG_FORMAT=json`.

## Serveur

```bash
uvicorn revenue_agent.entrypoints.api:app --port 8000
ngrok http 8000     # pour que Trigger.dev et Retell puissent nous atteindre
```

| Endpoint | Appelant | Rôle |
|---|---|---|
| `POST /scan` | Trigger.dev (cron) | Scanne le CRM, trie, décide. Protégé par `X-Scan-Token` |
| `POST /retell/tool-call` | Retell, pendant un appel | L'agent vocal exécute une action du registre |
| `POST /retell/webhook` | Retell, fin d'appel | `call_analyzed` → transcript et résumé réinjectés dans la décision |
| `POST /whatsapp/inbound` | Meta | Un message entrant relance un cycle de décision complet |
| `GET /approvals` | opérateur | Actions en attente, avec le message exact qui partirait |
| `POST /approvals/{id}/approve` · `/reject` | opérateur | Arbitrage |
| `GET /health` | supervision | Mode d'autonomie, actions en attente, composants simulés |

---

## Architecture

Hexagonale (ports & adapters), et pas par goût du motif : c'est ce qui rend la dégradation
gracieuse possible **sans un seul `if` dans le code métier**.

```
        entrypoints (FastAPI, CLI)          ← adapters primaires
                    │
              application                    ← use cases : scan, décision, appel, arbitrage
                    │
                 domain                      ← modèle, triage, enjeu, politique (zéro dépendance)
                    │
                  ports                      ← interfaces
                    │
   HubSpot · Resend · Meta · Retell · Exa · OpenRouter · JSON local
```

```
revenue_agent/
├── domain/        # modèle, triage, enjeu, politique d'autonomie, relecture — zéro dépendance
├── ports/         # interfaces (CRM, communication, intelligence, état, validations)
├── application/   # use cases : scan, cycle de décision, fin d'appel, arbitrage humain
├── adapters/      # HubSpot, Resend, Meta, Retell, Exa, OpenRouter, JSON local
├── entrypoints/   # API FastAPI, CLI
└── prompts/       # moteur de décision, relecture de message
trigger/           # tâches Trigger.dev (cron + waitpoints durables)
```

Trois bénéfices concrets :

- **La dégradation est un choix de câblage.** `container.py` choisit `HubSpotCrmAdapter` ou
  `JsonFileCrmAdapter`, `ResendEmailAdapter` ou `ConsoleEmailAdapter`. Le domaine ignore
  l'arbitrage.
- **Les tests tournent sans réseau.** C'est la mesure objective du découplage.
- **Changer de fournisseur est un fichier.** HubSpot → Salesforce, Retell → Vapi : un adapter,
  zéro impact sur le domaine.

## Stack

| Brique | Choix |
|---|---|
| Raisonnement | **GPT-5.6** via **OpenRouter** — `luna` en routine, `sol` quand l'enjeu le justifie |
| Orchestration agent | **LangGraph** (`langchain.agents.create_agent`), un thread par opportunité, 10 tools |
| Orchestration longue durée | **Trigger.dev** — cron du scan, waitpoints durables pour les relances |
| CRM | **HubSpot** (API v3/v4, notes comme mémoire partagée), repli JSON local |
| Recherche prospect | **Exa** (fenêtre temporelle bornée : un article de 2019 n'est pas un signal d'achat) |
| Voix | **Retell AI** — appels sortants, tool-calls en cours d'appel, webhook `call_analyzed` |
| Email / WhatsApp | **Resend** / **Meta Cloud API** |
| API / CLI | **FastAPI**, **httpx**, **argparse** |
| Qualité | **pytest**, **ruff** |

### Le raisonnement est routé par l'enjeu

Si la décision commerciale est le produit, alors *combien de raisonnement cette décision
mérite* est elle-même une décision. `domain/stakes.py` tranche, en fonction pure et auditable :

| Situation | Modèle |
|---|---|
| Montant ≥ 50 k€, stade tardif, objection non résolue, changement de stade | le modèle capable |
| Le reste | le tier économique |

Chaque cycle journalise son motif de routage — le coût reste explicable après coup. Et le triage
qui précède est **déterministe et gratuit** : un scan peut remonter des centaines de deals, faire
raisonner un modèle sur chacun coûterait cher pour conclure, la plupart du temps, qu'il n'y a
rien à faire.

## Fiabilité

Ce qui a été traité explicitement, parce qu'une boucle autonome tourne sans personne devant
l'écran :

- Le pointeur de scan n'avance pas si le CRM est indisponible
- Une opportunité en échec n'interrompt jamais le traitement des suivantes
- Les erreurs d'adapter sont rendues au modèle en texte — il change de canal ou escalade au lieu
  de planter
- Retry HTTP sur les seuls statuts transitoires, en respectant `Retry-After` ; un 4xx métier
  n'est jamais rejoué
- Écriture JSON atomique (`os.replace` + `fsync`) sous verrou : une interruption ne laisse pas
  d'état tronqué
- Un adapter **lève** quand il échoue ; la dégradation est une décision applicative, prise dans
  les use cases

## Tests

```bash
pip install -e ".[dev]"
pytest          # 75 tests
ruff check .
```

**75 tests, aucun réseau, aucun appel de modèle, 0,7 seconde.** Ils couvrent le triage, le
routage par enjeu, la résolution de canal, la boucle de scan, la politique d'autonomie et la
relecture des messages. Les tests de `test_policy.py` sont les plus importants du dépôt : ce sont
eux qui vérifient ce qui empêche un message de partir.

## Coûts

| Poste | Coût |
|---|---|
| Un cycle de décision, tier économique | ~0,001–0,003 $ |
| Un cycle de décision, modèle capable | ~0,008–0,02 $ |
| Une relecture de message avant envoi | ~0,0003 $ |
| Une recherche Exa | ~0,007 $ |
| Un appel téléphonique Retell de 5 min | ~0,65 $ |

Le LLM n'est jamais le poste dominant : **la voix l'est, d'un facteur 30 à 600.** C'est ce qui
justifie que l'agent réserve le téléphone aux moments où il apporte plus que l'écrit — et le
triage déterministe garantit qu'on ne paie du raisonnement que pour les opportunités qui le
méritent.
