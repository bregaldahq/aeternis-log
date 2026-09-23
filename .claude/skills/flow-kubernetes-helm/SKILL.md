---
name: flow-kubernetes-helm
description: >-
  Deep reference for deploying AeternisLog on Kubernetes with the Helm chart
  (deploy/helm/aeternislog): values, the rendered ConfigMap vs Secrets split,
  the Fabric identity Secret, WAL PVC and the single-replica guard, the
  pre-install/pre-upgrade migration Job, demo MongoDB/Redis, probes and
  Prometheus annotations. Use when changing the chart, adding a config key
  that must reach Kubernetes, or planning a production deployment.
---

# Flow: Kubernetes / Helm

## Chart layout (`deploy/helm/aeternislog/`)

| Template | Renders |
|---|---|
| `configmap.yaml` | `config.yaml` for the API **and** the migrate Job (non-secret settings) |
| `api-secret.yaml` | app Secret (only if keys/tenants/webhook secret set): `AUTH_API_KEYS`, `AUTH_TENANTS`, `WEBHOOK_SECRET` as env |
| `secret.yaml` | Fabric identity Secret (`tls-ca.pem`, `sign-cert.pem`, `sign-key.pem`) unless `fabric.identity.existingSecret` |
| `api-deployment.yaml` | API Deployment. **Fails to render if `replicaCount > 1` without `allowMultiReplica`**. `checksum/config` + `checksum/secret` roll pods on change. Mounts config, identity (`/fabric-identity/{tls/ca.crt, signcert/cert.pem, keystore/key.pem}`), WAL PVC. Probes on `/health`. Prometheus annotations |
| `api-wal-pvc.yaml` | WAL PVC (`persistence.*`) |
| `api-service.yaml` | Service (API + metrics ports) |
| `migrate-job.yaml` | `aeternislog-migrate` Job, hook `pre-install,pre-upgrade`, weight -5, same config + app Secret |
| `mongodb.yaml`, `redis.yaml` | **demo single-pod** datastores (`mongodb.enabled`, `redis.enabled`) |
| `_helpers.tpl` | names, labels, `mongoHost`, `redisHost`, `tenantsEnv` |

## Values that matter

`image.*`, `replicaCount` / `allowMultiReplica`, `persistence.*` (WAL),
`api.securityContext` (uid 1000, non-root), `auth.{enabled, headerName, apiKeys, tenants}`,
`rateLimit.*`, `fabric.{syncEnabled, channel, chaincode, mspId, peerEndpoint, serverNameOverride, timeouts, tenantChannels, identity.*}`,
`webhook.*`, `migrations.{enabled, backoffLimit}`, `mongodb.*`, `redis.*`,
`ingress.*`, `metrics.*`.

Rule: **secrets never go into the ConfigMap.** Keys and the webhook secret
go through the app Secret as env (env overrides YAML), and the signing key
through the identity Secret. Prefer `existingSecret` plus an external secret
store in production.

## Known gaps / drift (check before a real deploy)

- **Migration hook on first install**: `pre-install` runs **before** the
  demo MongoDB exists, so the Job can exhaust its retries on a fresh
  install. Verify on a clean cluster. Options: `post-install` for the
  install case, an init container that waits for Mongo, or an external Mongo.
- **Batching is hardcoded `enabled: true`** in the configmap (no value), as
  are the `logging.format`, Mongo pool and Redis settings.
- The ConfigMap renders a block of **dead WAL keys** (`backend`,
  `stream_key`, `consumer_group`, rotation, …) and `fabric.tls_enabled`,
  none of which the Go config reads. `values.yaml` advertises `wal.backend`.
- No support for `fabric.tenant_identities` (per-tenant signing identities),
  so ledger isolation by identity cannot be configured through the chart.
- `values.yaml` says rate limiting is "in-memory, per pod", but with the
  in-chart Redis enabled the API uses the **shared Redis store**.
- `secret.yaml` comments reference `docs/runbook-operacao-prod.md`, which
  does not exist.
- Liveness and readiness both use `/health`, which returns 200 even when
  degraded.

## Invariants

- Single replica unless per-pod durable WAL is solved (see `flow-wal`).
- The migration Job and the API use the **same** config and secrets, so they
  reach the same database.
- Identity key mounted read-only with mode 0400 for uid 1000.

## Validate changes

```bash
helm lint deploy/helm/aeternislog
helm template t deploy/helm/aeternislog --set auth.enabled=true \
  --set auth.tenants[0].id=acme --set auth.tenants[0].keys[0]=k | less
helm template t deploy/helm/aeternislog --set replicaCount=2   # must fail
```

When you add a Go config key operators need in Kubernetes, wire it through
`values.yaml` → `configmap.yaml` (or the Secret if sensitive) and update the
chart `README.md` and the website page `operations/kubernetes.md`.

## Related

`flow-config-lifecycle` · `flow-mongo-migrations` · `flow-wal` ·
`flow-observability` · `flow-fabric-transport`
