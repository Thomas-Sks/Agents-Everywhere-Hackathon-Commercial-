# Orchestration Trigger.dev

La tâche `scan-crm` appelle `POST /scan` de l'API Python toutes les 5 minutes. C'est ce qui
transforme le moteur de décision en agent réellement autonome.

## Pourquoi une couche TypeScript

Trigger.dev ne fournit pas de SDK Python — leur package `@trigger.dev/python` est une extension
de build qui exécute des scripts Python dans un conteneur, pas un SDK. Le pattern retenu est
donc celui recommandé dans ce cas : **une tâche TS mince qui appelle notre API HTTP**. Le
raisonnement commercial reste intégralement en Python ; seule la cadence vit ici.

## Mise en route

Le scaffold (`trigger.config.ts`, `package.json`, versions du SDK) est généré par leur CLI —
mieux vaut le laisser produire ces fichiers que les écrire à la main :

```bash
cd trigger
npx trigger.dev@latest init      # crée le projet et trigger.config.ts
cp src/trigger/scan-crm.ts <emplacement indiqué par le CLI si différent>
npx trigger.dev@latest dev       # exécution locale, tâches visibles dans leur dashboard
```

Puis en déploiement :

```bash
npx trigger.dev@latest deploy
```

## Variables d'environnement

À définir dans le dashboard Trigger.dev (et en local dans `.env`) :

| Variable | Rôle |
|---|---|
| `REVENUE_AGENT_API_URL` | URL publique de l'API FastAPI (ngrok pendant le hackathon) |
| `SCAN_SHARED_SECRET` | Doit correspondre au `SCAN_SHARED_SECRET` de l'API — l'endpoint `/scan` consomme des tokens LLM, il n'est pas ouvert |

⚠️ L'URL ngrok change à chaque redémarrage : c'est une variable d'environnement, jamais une
valeur en dur.

## Les deux tâches

- **`scan-crm`** (cron `*/5 * * * *`) — le mécanisme principal. Les relances programmées sont
  stockées côté Python et détectées par le triage, ce qui garantit une source de vérité unique
  pour « quand réveiller cette opportunité ».
- **`schedule-follow-up`** (waitpoint durable) — programme une reprise ponctuelle sans attendre
  le prochain tick. Pratique en démo : on programme une relance dans deux minutes et elle part
  toute seule, sans process maintenu en vie.

La cadence de 5 minutes n'est pas arbitraire : la Search API HubSpot est plafonnée à
**4 requêtes/seconde partagées** entre tous les objets. Scanner plus souvent ne remonterait pas
plus de leads, cela consommerait juste le quota.
