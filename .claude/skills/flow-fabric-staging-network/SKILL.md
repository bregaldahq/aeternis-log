---
name: flow-fabric-staging-network
description: >-
  Deep reference for the production-staging Fabric network in
  hybrid-architecture/fabric-network/prod (a LOCAL staging network on WSL, not
  remote prod): 3 peer orgs + Raft-3 orderers with channel participation,
  Fabric-CA-issued identities, MAJORITY (2/3) endorsement, per-tenant channels
  and identities, chaincode deploy/upgrade, fault-tolerance drills, ledger
  backup, and the parallel API stack (api/docker-compose.prod.yml). Use when
  operating or changing anything under prod/, rehearsing upgrades, onboarding
  a tenant with its own channel/identity, or running DR/fault drills.
---

# Flow: production-staging Fabric network (`fabric-network/prod`)

> The `.prod` containers are a **local staging network** on this WSL machine.
> It is safe and authorized to `docker exec`, stop and start them. It is
> fully isolated from dev: Docker network `aeternislog_network_prod`, shifted
> ports, and its own CA-issued crypto (gitignored:
> `organizations/`, `channel-artifacts/`, `api-identity/`, `backups/`, …).

## Topology

- **Orderers**: `orderer0/1/2.example.com`, Raft, channel participation (no
  system channel; `osnadmin` joins).
- **Peer orgs**: `peer0.org1/org2/org3.example.com`, one peer each, with
  `couchdb-org1/2/3`.
- **CAs** (`docker-compose-ca.yml`): `ca.org1/2/3`, `ca.orderer`.
- **CLI**: `cli.prod`, where most scripts run (`/opt/scripts/...`).
- **Channels**: `logchannel` (default), plus per-tenant channels such as
  `acme-channel` (profile `ThreeOrgsChannel`, endorsement policy MAJORITY 2/3).
- **API stack**: `api/docker-compose.prod.yml` → `aeternislog-api-prod`
  (host 5002, metrics 9091, non-root uid 1000, identity bundle mounted at
  `/fabric-identity`), `aeternislog-mongodb-prod` (27018),
  `aeternislog-redis-prod` (6380). Config: `api/config.prod.yaml` (auth on,
  staging keys, `tenant_channels.acme`, **auto-batch off**).

## Scripts (`prod/scripts/`)

| Script | Runs on | Purpose |
|---|---|---|
| `registerEnroll.sh` | host | Register and enroll every org's MSP and TLS through Fabric CA (no cryptogen). Run after the CAs are up |
| `join-peers.sh` | cli.prod | Join each org's peer to `logchannel` + set anchor peers (idempotent) |
| `deploy-chaincode.sh` | cli.prod | Lifecycle: package `logchaincode_1` v1.0 seq 1, install on 3 peers, approve 3 orgs, commit (MAJORITY) |
| `upgrade-chaincode.sh <ver> [seq]` | cli.prod | Same lifecycle at a new version and sequence (default committed+1). State preserved |
| `create-tenant-channel.sh <ch>` | host | `configtxgen` genesis for `<ch>` → `tenant-channel-steps.sh` in cli.prod (osnadmin join on 3 orderers, join 3 peers, approve+commit, reusing the installed package) |
| `register-tenant-identity.sh <tenant> [org]` | host | Enroll an identity with ecert attr `tenant=<tenant>` → least-privilege bundle for `fabric.tenant_identities` |
| `build-api-identity.sh [org]` | host (root) | Extract the API's bundle (admin signcert, key 0400, peer TLS CA) owned by uid 1000 → `prod/api-identity/org<N>` |
| `smoke-test.sh` | cli.prod | Anchor with Org1+Org2 endorsement, then query it back |
| `anchor.sh <batch> "<orgs>" [orderer]` | cli.prod | Parametric `StoreMerkleRoot` used by the drills |
| `fault-tolerance.sh` | host | 1 orderer down → ok; 1 org down → ok; 2 orgs down → **must fail**. A trap restarts what it stopped |
| `query-batch.sh <batch> [org] [ch]` | cli.prod | Read a batch from another org's peer (shared-ledger proof) |
| `discover-peers.sh`, `status.sh`, `orderer-status.sh` | cli.prod | Read-only probes (discovery plan, membership, committed cc, osnadmin list) |
| `backup-ledger.sh` | host | Crash-consistent tar of every orderer and peer block store → `prod/backups/<ts>/`. CouchDB is derived and not backed up |

## Bring-up order (reconstructed; there is no single bootstrap script)

1. `docker network create aeternislog_network_prod` (if missing).
2. `docker compose -f prod/docker-compose-ca.yml up -d` → `prod/scripts/registerEnroll.sh`.
3. Create the `logchannel` genesis block (`configtxgen -profile
   ThreeOrgsChannel`, `prod/configtx.yaml`) and join the orderers
   (`osnadmin`). `tenant-channel-steps.sh` shows the exact commands for any
   channel id.
4. `docker compose -f prod/docker-compose-prod.yml up -d`.
5. In `cli.prod`: `join-peers.sh` → `deploy-chaincode.sh` → `smoke-test.sh`.
6. `build-api-identity.sh 1` → from `api/`:
   `docker compose -f docker-compose.prod.yml -p aeternislog-prod up -d --build`.
7. Tenants: `create-tenant-channel.sh acme-channel`, plus optionally
   `register-tenant-identity.sh acme` and `fabric.tenant_identities`.

Documenting or scripting steps 1-6 as one idempotent `bootstrap.sh` is an
open improvement.

## Gotchas

- **Migrations**: `docker-compose.prod.yml` does **not** set
  `RUN_MIGRATIONS=true`, so a fresh `aeternislog-mongodb-prod` makes the API
  refuse to boot (schema mismatch). Run
  `docker exec aeternislog-api-prod /app/aeternislog-migrate` once, or set
  the env (`flow-mongo-migrations`).
- **Auto-batch off** in `config.prod.yaml`: batches only happen through
  `POST .../records/batch`, and the **reconciler never runs** (it lives in the
  ticker).
- Deploying the F14 tenant-scoped chaincode over pre-F14 data hides old
  batches. Use a clean ledger or re-key first.
- `tenant-channel-steps.sh` and `deploy-chaincode.sh` hardcode
  `logchaincode_1` v1.0 seq 1. After an `upgrade-chaincode.sh`, a **new**
  tenant channel must be approved at the current definition, not v1.0. Check
  before creating channels post-upgrade.
- Chaincode containers run on `aeternislog_network_prod`
  (`CORE_VM_DOCKER_HOSTCONFIG_NETWORKMODE`). The peers mount the Docker
  socket to build them.

## Invariants

- Endorsement is **real**: with two of three orgs down, anchoring must fail
  (fault-tolerance test 3). Never relax the channel policy to "make it work".
- The API identity bundle is least-privilege (no whole `organizations/`
  mount) and the key is 0400.
- Staging keys in `config.prod.yaml` are throwaway. Real secrets never go in
  the repo.

## Debug recipes

```bash
docker exec cli.prod bash /opt/scripts/status.sh
docker exec cli.prod bash /opt/scripts/orderer-status.sh
docker exec cli.prod bash /opt/scripts/query-batch.sh <batchId> 3 logchannel
bash hybrid-architecture/fabric-network/prod/scripts/fault-tolerance.sh
curl -s -H 'X-API-Key: staging-acme-key' localhost:5002/health | jq
```

## Related

`flow-chaincode` · `flow-tenancy` · `flow-fabric-transport` ·
`flow-fabric-dev-network` · `flow-mongo-migrations` · `flow-kubernetes-helm`
