---
name: flow-fabric-dev-network
description: >-
  Deep reference for the local development Hyperledger Fabric network
  (hybrid-architecture/fabric-network): single org (Org1, 3 peers + CouchDB),
  solo orderer, cryptogen artifacts, the self-initializing `cli` container
  (channel logchannel, chaincode logchaincode_1), start/stop/clean scripts and
  the make targets that drive them. Use when bringing the blockchain up or
  resetting it, when chaincode changes don't show up, when the API can't reach
  the peer, or when editing the network scripts/compose.
---

# Flow: development Fabric network

## Topology (`hybrid-architecture/fabric-network/docker-compose.yml`)

| Container | Role | Host ports |
|---|---|---|
| `orderer.example.com` | orderer (fabric-orderer 2.4) | 7050, 9443 |
| `peer0/1/2.org1.example.com` | peers (fabric-peer 2.4) | 7051, 9051, 11051 (ops 9444-9446) |
| `couchdb0/1/2` | world state per peer | 5984, 6984, 7984 |
| `ca.org1.example.com` | Fabric CA 1.5 (not used by cryptogen material) | 7054 |
| `cli` | fabric-tools 2.4; runs `scripts/2-init-network.sh` on start and stays alive | — |

Mounts: `../chaincode` → `/opt/gopath/src/github.com/chaincode` in `cli`;
`./scripts` → `/scripts`. All containers join the external Docker network
`aeternislog_network`, shared with the API stack.

## Lifecycle

`make blockchain` (from the repo root) → `make network` (create
`aeternislog_network` if missing) → `cd hybrid-architecture/fabric-network &&
yes n | ./start-network.sh`:

1. `--clean` only: `docker-compose down -v` and remove `crypto-config/`,
   `config/genesis.block`, `config/logchannel.tx`.
2. **Artifacts** (skipped with `--restart`, or if all present):
   `scripts/1-generate-artifacts.sh` → `cryptogen` (keys `chmod 0644` so the
   non-root API can read them in dev) + `configtxgen` genesis block + channel
   tx. It needs the Fabric binaries in `fabric-network/bin/` (gitignored).
3. `docker-compose up -d` (or `restart`), wait 15 s.
4. Wait up to about 60 s for `cli` logs to show
   `Fabric network configured successfully`.
   `2-init-network.sh` is **idempotent**: create channel if missing, join all
   3 peers, package and install `logchaincode_1` if missing, approve v1.0
   seq 1 for Org1MSP if not approved, commit if not committed.
5. Verification and summary. The `yes n` pipe answers "no" to the optional
   test prompt (`test-network.sh`).

Other targets: `make blockchain-logs` (follow `cli`), `make down` (stop,
keep volumes), `make clean-blockchain` (`start-network.sh --clean`, **wipes
the ledger**), `make clean` (everything, including API volumes).
`stop-network.sh [--clean]` exists for direct use.

## Gotchas

- **Chaincode changes are not picked up automatically.** The init script
  skips install when label `logchaincode_1` is already installed, and skips
  commit when the definition is committed. After editing
  `hybrid-architecture/chaincode`, run `make clean-blockchain` (dev data
  lost), or bump label, version and sequence in `2-init-network.sh` for an
  in-place upgrade.
- **Cold chaincode containers**: after a restart only peers that served
  traffic have chaincode containers. The gateway may pick a cold endorser →
  endorsement timeout. Warm them with
  `docker exec cli bash /scripts/4-warm-chaincode.sh`.
- `builders/ccaas/bin/*` (chaincode-as-a-service builder binaries) are
  committed but **not referenced** by any compose file. Chaincode is built
  and launched by the peers' default Go builder.
- `scripts/3-test-network.sh` exercises the **legacy** `CreateLog`/`QueryLog`
  functions, not the product path.
- The dev network is `docker-compose` (v1 syntax) while the API uses
  `docker compose` (v2). Compose v2 is intermittent in this WSL setup, so
  prefer `make`.
- Two Docker daemons exist on this machine. The tcc stacks run under Docker
  Desktop. An environment restart can drop them. `make up` restores them
  (volumes persist).

## Invariants

- Crypto material, channel artifacts and binaries are **gitignored**. Never
  commit them.
- The API's default gateway identity is **Org1 Admin** from this material,
  mounted read-only at `/fabric-crypto`.
- `logchannel` / `logchaincode` names must match `fabric.channel` /
  `fabric.chaincode`.

## Debug recipes

```bash
make status                                # containers on aeternislog_network
docker logs cli 2>&1 | tail -30            # init progress
docker exec cli peer channel list
docker exec cli peer lifecycle chaincode querycommitted -C logchannel
docker exec cli bash /scripts/4-warm-chaincode.sh
curl -s localhost:5001/health | jq .services.fabric
```

## Related

`flow-chaincode` · `flow-fabric-transport` · `flow-local-stack` ·
`flow-fabric-staging-network`
