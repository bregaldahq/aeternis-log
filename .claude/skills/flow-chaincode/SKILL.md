---
name: flow-chaincode
description: >-
  Deep reference for the AeternisLog Fabric smart contract
  (hybrid-architecture/chaincode, module github.com/chaincode): the
  write-once, tenant-scoped StoreMerkleRoot / QueryMerkleBatch /
  GetAllMerkleBatches functions, clientTenant identity derivation, composite
  keys, the legacy log functions, and how to test and deploy chaincode
  changes. Use for ANY chaincode edit, when an anchor/query error string is
  involved, or when reasoning about what the ledger guarantees.
---

# Flow: chaincode (`logchaincode`)

The chaincode is the **ledger-side trust boundary**. It stores one record per
anchored batch, `{tenant, batch_id, merkle_root, timestamp, num_logs, log_ids}`,
under a tenant-scoped composite key. The API never trusts itself for tenancy
at this layer: the chaincode derives the tenant from the transaction signer.

## Functions

| Function | Line | Tenant-scoped | Used by the API | Notes |
|---|---|---|---|---|
| `clientTenant` (internal) | `logchaincode.go:48` | — | — | cert attr `tenant`, else MSP ID, else error |
| `batchKey` (internal) | `:64` | — | — | `CreateCompositeKey("batch", [tenant, batchID])` |
| `StoreMerkleRoot(batchID, root, ts, numLogs, logIDsJSON)` | `:289` | ✓ | anchor + reconcile | **write-once** → `batch <id> is already anchored and cannot be overwritten` |
| `QueryMerkleBatch(batchID)` | `:346` | ✓ | verify + public | missing or foreign → `batch <id> does not exist` |
| `GetAllMerkleBatches()` | `:402` | ✓ | warm script only | partial composite key on the caller's tenant |
| `VerifyBatchIntegrity(batchID, hashesJSON)` | `:374` | ✓ (via Query) | no | **v1 Merkle only**, wrong for v2 batches |
| `BuildMerkleTree` / `combineHashes` | `:245`, `:281` | — | no | legacy v1 (duplicate-last) |
| `CreateLog`, `QueryLog`, `LogExists`, `GetAllLogs`, `GetLogHistory`, `QueryLogsByLevel/Source` | `:72-241` | ✗ | no (dev test script only) | **legacy, unscoped**: plain `PutState(id)`, overwritable, readable by any member |

## Contract semantics

- **Timestamp** arg: RFC3339 from the API clock. Unparseable values silently
  fall back to the peer's `time.Now()`, which is non-deterministic across
  endorsers and can fail endorsement matching. The API always sends RFC3339.
- **`logIDs`** must be a JSON array string, otherwise an error.
- **Write-once**: `GetState(key) != nil` → error. That is what makes
  reconciliation idempotent (`flow-batch-reconciliation`) and what gives the
  product its immutability.
- **Isolation**: the same `batchID` under two tenants maps to two different
  keys, so one tenant cannot read, overwrite or enumerate another's batches.
- Error **strings are an API contract**. The API matches `already anchored`
  and `does not exist` by substring (`batch_processor.go:179,392`,
  `public.go:62`).

## Invariants

1. The tenant is **never** a function argument (`TestTenantNotSpoofableViaBatchID`).
2. Every batch read and write goes through `batchKey(clientTenant(ctx), …)`.
3. `StoreMerkleRoot` never overwrites.
4. Chaincode logic is deterministic (no local clocks or randomness in the
   written state beyond the provided args). The `time.Now()` fallback breaks
   this. Do not rely on it.

## Known gaps / risks

- **Legacy log functions are unscoped and overwritable.** Any channel member
  can `CreateLog` with any id and overwrite someone else's. They are dead for
  the product (only `scripts/3-test-network.sh` calls them). Removing them is
  a chaincode upgrade and must be coordinated with the dev test script.
- `VerifyBatchIntegrity` uses v1 Merkle, so it returns `false` for intact v2
  batches. Remove it, or port it to versioned verification.
- Composite-key migration: batches written by the pre-F14 chaincode
  (`batch_<id>` keys) are invisible to the tenant-scoped queries
  (`docs/tenant-isolation-guarantee.md`).
- Go 1.18 module (`go.mod`), older than the API's 1.25. Keep it building with
  the Fabric `fabric-tools:2.4` image.

## Tests

`hybrid-architecture/chaincode/logchaincode_isolation_test.go` uses a fake
stub (`fakeStub`, in-memory state and composite keys) plus a fake client
identity (`fakeCID`: MSP ID and `tenant` attribute). No network needed:

```bash
cd hybrid-architecture/chaincode && go test ./... -v
```

Covers: scoped store/query, cross-tenant overwrite denied, `GetAll` only
returns the caller's batches, the batch id cannot spoof the tenant, and the
MSP fallback. **Gap:** no dedicated test for same-tenant write-once
(double `StoreMerkleRoot` → "already anchored"). Add one, because the
reconciler depends on that message.

## Changing it safely

1. Red: add or extend a test in `logchaincode_isolation_test.go` using
   `ctxFor(stub, tenant)` / `ctxForMSP`.
2. Keep error messages stable, or update the API matchers in the same PR
   (with tests).
3. Deploy:
   - **dev**: the init script installs label `logchaincode_1` v1.0 seq 1 and
     **skips install if that label already exists**. After changing chaincode
     code, run `make clean-blockchain` (wipes the dev ledger) or bump
     label/version/sequence in `scripts/2-init-network.sh`.
   - **staging**: `prod/scripts/upgrade-chaincode.sh <new-version>` inside
     `cli.prod` (new label and sequence, approve 3 orgs, commit MAJORITY).
     World state is preserved. See `flow-fabric-staging-network`.
4. Run the `integrity-reviewer` agent. Update `docs/runbook-migrations-and-upgrades.md`
   and the website page `operations/chaincode-upgrades.md` if the upgrade
   procedure changes.

## Debug recipes (dev network, inside `cli`)

```bash
docker exec cli peer chaincode query -C logchannel -n logchaincode \
  -c '{"function":"QueryMerkleBatch","Args":["<batchId>"]}'
docker exec cli peer chaincode query -C logchannel -n logchaincode \
  -c '{"function":"GetAllMerkleBatches","Args":[]}' | jq length
```

The `cli` container signs as **Org1 Admin**. It sees batches in the
partition of that identity's tenant (MSP `Org1MSP` when there is no `tenant`
attribute), which is the same partition as the API's default identity in
dev.

## Related

`flow-fabric-transport` · `flow-tenancy` · `flow-batch-reconciliation` ·
`flow-fabric-dev-network` · `flow-fabric-staging-network` · `flow-merkle-tree`
