---
name: flow-fabric-transport
description: >-
  Deep reference for how the Go API talks to Hyperledger Fabric
  (api/internal/fabric): the FabricClient facade, the Backend interface, the
  Fabric Gateway gRPC backend (one TLS gRPC conn, one gateway session per
  signing identity, per-channel contract cache), per-tenant identity and
  channel selection, the disabled backend, timeouts and commit-status
  semantics. Use when changing Fabric config, identities, timeouts, adding a
  chaincode call, or debugging "failed to endorse/submit/commit",
  UNKNOWN anchor status, or gateway startup failures.
---

# Flow: API ↔ Fabric transport

## Layers

```
merkle.BatchProcessor / handlers.PublicHandler
        │  (Anchorer / batchQuerier interfaces — fakeable)
        ▼
fabric.FabricClient            client.go   — domain calls: StoreMerkleBatch, VerifyMerkleBatch
        │  Backend interface   backend.go
        ├── disabledBackend    (fabric.sync_enabled=false: every call errors "fabric sync is disabled")
        └── gatewayBackend     gateway.go  — Fabric Gateway gRPC SDK
                 conn: 1 × grpc.ClientConn (TLS, peer endpoint)
                 defaultGw: gateway session for fabric.identity_*
                 tenantGws[tenant]: session per fabric.tenant_identities entry
                 each session: contracts[channel] cache
```

## Entry points

| What | Where |
|---|---|
| Construction (fatal on error) | `api/cmd/api/main.go:153` (`NewFabricClient`) |
| Backend selection | `api/internal/fabric/backend.go:29` (`newBackend`; only `""`/`gateway` accepted) |
| Gateway build | `api/internal/fabric/gateway.go:73` (`newGatewayBackend`), `:105` (`connectGateway`), `:130` (`newGRPCConnection`) |
| Identity / signer | `gateway.go:151` (`newIdentity`), `:168` (`newSign`, reads the **first** file in the keystore dir) |
| Identity selection | `gateway.go:63` (`gatewayFor(tenant)`) |
| Submit | `gateway.go:194` (`Invoke`: endorse → submit → **wait commit status**) |
| Evaluate | `gateway.go:230` (`Query`) |
| Domain calls | `api/internal/fabric/client.go:67` (`StoreMerkleBatch`), `:86` (`VerifyMerkleBatch`) |
| Channel resolution | `api/pkg/config/config.go:171` (`ChannelForTenant`) |

## Call semantics

- **StoreMerkleBatch** → chaincode `StoreMerkleRoot(batchID, root,
  now().UTC() RFC3339, n, json(ids))`. It returns only after the commit
  status is known and successful. Endorse, submit and commit-status errors
  are **all failures**, so an unknown outcome is never reported as anchored.
  The reconciler resolves it later.
- **VerifyMerkleBatch** → `QueryMerkleBatch(batchID)` evaluate (no ledger
  write), decoded into `QueryResponse.Data` (a JSON map; non-JSON goes under
  `result`).
- **Timeouts**: gateway options `query_timeout` (evaluate, default 10 s) and
  `invoke_timeout` (endorse/submit/commit status, default 30 s), plus caller
  contexts (30 s anchor sub-context, 10 s verify/public).
- **Tenant → identity**: an unmapped tenant, or `""` (public endpoint), uses
  the default identity. **Tenant → channel**: `tenant_channels[tenant]`, else
  `fabric.channel`.
- Endorsers are chosen by **service discovery** from the gateway peer. In
  staging that is how the MAJORITY (2 of 3 orgs) policy is met without a
  client-side peer list.

## Configuration (see `api/.env.example`)

`fabric.{sync_enabled, transport, channel, chaincode, msp_id,
gateway_peer_endpoint, gateway_peer_tls_ca_file, gateway_server_name_override,
identity_cert_file, identity_key_dir, invoke_timeout, query_timeout,
tenant_channels, tenant_identities}`. Env overrides exist for all except
`tenant_identities` and the timeouts. `Validate` requires the five gateway
fields when `sync_enabled`. `api_url` is a leftover and unused.

Defaults point at the docker-compose mount `/fabric-crypto/...` (dev
cryptogen material, Org1 Admin). For a **native** `make run`, override the
endpoint to `localhost:7051` and the paths to
`hybrid-architecture/fabric-network/crypto-config/...` (keep
`gateway_server_name_override=peer0.org1.example.com` for TLS SNI).

## Invariants

- Never mark a batch anchored without a successful commit status.
- Anchor and verify for one tenant must resolve the **same** identity and
  channel.
- Credentials are read-only mounts. The signing key is never logged.
- The API has one gRPC connection. Creating a connection per request would
  exhaust the peer.

## Known gaps

- `newSign` takes the **first** directory entry of the keystore. A stray file
  (backup, `.DS_Store`) there breaks startup.
- `HealthCheck` only checks that the gRPC connection is not `Shutdown`. It
  does not detect a peer that is down (the state may be
  `TRANSIENT_FAILURE`/`IDLE`), so `/health` may say `fabric: healthy` while
  anchoring fails.
- A Fabric failure at boot is **fatal** (`NewFabricClient` error →
  `lg.Fatal`), while runtime Fabric failures degrade gracefully.
- The runtime image still installs `docker-cli`, a leftover of the removed
  docker-exec transport.

## Tests

- `api/internal/fabric/client_test.go`: construction, transport validation,
  stats, disabled behavior.
- `api/internal/fabric/gateway_test.go` (`TestGatewayBackendConstruct`, skips
  without crypto-config) and `gateway_pool_test.go`
  (`TestGatewayForSelectsTenantIdentity`).
- `api/internal/fabric/gateway_integration_test.go` (`//go:build integration`,
  `TestGatewayE2E`) needs the dev network up:
  `cd api && go test -tags integration ./internal/fabric/ -v`.
- Upstream seams: `fakeAnchorer` (`merkle/f03_test.go`), fake `batchQuerier`
  (`handlers/public_test.go`).

## Changing it safely

- Adding a chaincode call: add a method on `FabricClient`, extend the narrow
  consumer interface (`Anchorer` / `batchQuerier`) and its fakes, and test
  through the fake. Add integration coverage under the `integration` tag.
- Changing identity or channel resolution is an isolation change
  (`flow-tenancy`). Run the `integrity-reviewer` agent.

## Debug recipes

```bash
curl -s localhost:5001/health | jq .services.fabric
make api-logs | grep -Ei 'endorse|submit|commit status|gateway'
docker exec aeternislog-api ls /fabric-crypto/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp/keystore
```

Common failures: `failed to endorse` with timeouts right after a network
restart means cold chaincode containers (run `scripts/4-warm-chaincode.sh` in
`cli`). `x509: certificate signed by unknown authority` means a wrong
`gateway_peer_tls_ca_file` or SNI override. `access denied` means the MSP ID
does not match the cert.

## Related

`flow-chaincode` · `flow-batch-anchoring` · `flow-batch-verification` ·
`flow-tenancy` · `flow-fabric-dev-network` · `flow-config-lifecycle`
