# Cadence autonome — Trigger.dev

C'est cette brique qui transforme une CLI en agent autonome. Sans elle, quelqu'un doit lancer
le scan à la main, et « agent commercial autonome » devient un abus de langage.

Le dossier est **déployable en l'état** : configuration, dépendances verrouillées et tâches
sont livrées. `npx trigger.dev init` n'est pas nécessaire — il ne ferait que régénérer ce qui
est déjà ici.

## Pourquoi du TypeScript à côté d'un projet Python

Trigger.dev ne fournit pas de SDK Python (leur paquet `@trigger.dev/python` est une extension
de build qui exécute des scripts dans un conteneur, pas un SDK). Le pattern retenu est donc
celui qu'ils recommandent : **des tâches TypeScript minces qui appellent notre API HTTP**. Tout
le raisonnement commercial reste en Python ; seule la cadence vit ici.

## Mise en route

```bash
cd trigger
npm install

# 1. Créer un projet sur cloud.trigger.dev, récupérer sa référence (proj_xxx)
export TRIGGER_PROJECT_REF=proj_xxxxxxxx

# 2. Pointer vers l'API du moteur de décision
export REVENUE_AGENT_API_URL=https://votre-tunnel.ngrok.app
export SCAN_SHARED_SECRET=...          # doit correspondre à celui de l'API Python

npm run dev        # exécution locale, tâches visibles dans leur dashboard
npm run typecheck  # vérifie les tâches contre les types du SDK
```

Déploiement :

```bash
export TRIGGER_ACCESS_TOKEN=tr_pat_...   # ou `npx trigger.dev login` en interactif
npm run deploy
```

En cloud, déclarez `REVENUE_AGENT_API_URL` et `SCAN_SHARED_SECRET` dans la page *Environment
Variables* du dashboard : les tâches les lisent via `process.env`. En local, `trigger.dev dev`
charge automatiquement un `.env` présent dans ce dossier.

⚠️ L'URL ngrok change à chaque redémarrage. C'est une variable d'environnement, jamais une
valeur en dur.

## Les deux tâches

| Tâche | Déclenchement | Rôle |
|---|---|---|
| `scan-crm` | cron `*/5 * * * *` | Le battement de cœur. Appelle `POST /scan`, journalise chaque décision prise. |
| `schedule-follow-up` | à la demande | Attente durable (`wait.for`) puis relance. Le run est suspendu et ses ressources libérées — il reprend exactement où il s'était arrêté, même des semaines plus tard. |

Le mécanisme principal reste le cron : les relances programmées sont stockées côté Python et
détectées par le triage, ce qui garantit une source de vérité unique pour « quand réveiller
cette opportunité ». `schedule-follow-up` sert aux échéances ponctuelles et à la démonstration
(programmer une relance à deux minutes et la voir partir seule).

La cadence de 5 minutes n'est pas arbitraire : la Search API HubSpot est plafonnée à
**4 requêtes/seconde partagées** entre tous les objets. Scanner plus souvent ne remonterait pas
plus de leads, cela consommerait seulement le quota.

## Versions

`@trigger.dev/sdk`, `@trigger.dev/build` et le CLI `trigger.dev` sont alignés en 4.5.16, avec
un `package-lock.json` versionné. Les tâches compilent sans erreur contre les types du SDK
(`npm run typecheck`).
