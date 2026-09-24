// Package aeternislog is a Go client for the Tamper-Evident Data Anchoring API.
//
// Besides wrapping the HTTP API (with automatic retries), it recomputes record
// hashes and Merkle roots locally and independently of the server, so an auditor
// can verify integrity without trusting the API.
package aeternislog

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"hash"
)

// CurrentHashVersion is the integrity-hash scheme the server writes new records
// with. Records created through this client are hashed under it, so the local
// hash matches the server's. Must track the server's models.CurrentHashVersion.
const CurrentHashVersion = 2

// Merkle domain separators (v2): leaves (0x00) and internal nodes (0x01) are
// hashed with distinct prefixes so the two can never be confused.
const (
	merkleLeafPrefix byte = 0x00
	merkleNodePrefix byte = 0x01
)

// Record mirrors a record returned by the API.
type Record struct {
	Domain     string                 `json:"domain"`
	ID         string                 `json:"id"`
	Timestamp  string                 `json:"timestamp"`
	Source     string                 `json:"source"`
	Payload    map[string]interface{} `json:"payload"`
	HashFields []string               `json:"hash_fields,omitempty"`
	Hash       string                 `json:"hash"`
	// HashVersion selects the integrity-hash scheme (absent/0 = legacy v1).
	HashVersion int    `json:"hash_version,omitempty"`
	BatchID     string `json:"batch_id,omitempty"`
	MerkleRoot  string `json:"merkle_root,omitempty"`
}

// ComputeHash recomputes the record's integrity hash locally — independently of
// the server — under the record's own scheme version (absent/0 = legacy v1).
// This is what makes verification trustless: the client never relies on the
// server's hash.
func (r *Record) ComputeHash() string {
	return r.hashForVersion(r.schemeVersion())
}

// schemeVersion is the record's hash scheme (absent/0 = legacy v1).
func (r *Record) schemeVersion() int {
	if r.HashVersion >= 2 {
		return r.HashVersion
	}
	return 1
}

// computeHashV1 is the legacy scheme (plain concatenation), kept to verify
// batches anchored before v2.
func (r *Record) computeHashV1() string {
	content := r.ID + r.Timestamp + r.Source + canonicalPayload(r.Payload, r.HashFields)
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

// computeHashV2 length-prefixes each hashed field and tags the leaf with a 0x00
// domain separator, matching the server's v2 scheme byte for byte.
func (r *Record) computeHashV2() string {
	h := sha256.New()
	h.Write([]byte{merkleLeafPrefix})
	writeLenPrefixed(h, r.ID)
	writeLenPrefixed(h, r.Timestamp)
	writeLenPrefixed(h, r.Source)
	writeLenPrefixed(h, canonicalPayload(r.Payload, r.HashFields))
	return hex.EncodeToString(h.Sum(nil))
}

func (r *Record) hashForVersion(v int) string {
	if v >= 2 {
		return r.computeHashV2()
	}
	return r.computeHashV1()
}

func writeLenPrefixed(h hash.Hash, s string) {
	var n [8]byte
	binary.BigEndian.PutUint64(n[:], uint64(len(s)))
	h.Write(n[:])
	h.Write([]byte(s))
}

// canonicalPayload serializes the payload (or only the HashFields keys)
// deterministically. encoding/json sorts map keys, so the output is stable.
func canonicalPayload(payload map[string]interface{}, hashFields []string) string {
	src := payload
	if len(hashFields) > 0 {
		sub := make(map[string]interface{}, len(hashFields))
		for _, k := range hashFields {
			if v, ok := payload[k]; ok {
				sub[k] = v
			}
		}
		src = sub
	}
	b, err := json.Marshal(src)
	if err != nil {
		return ""
	}
	return string(b)
}

// MerkleRoot recomputes the Merkle root of an ordered set of records locally,
// using the scheme version recorded on the records (absent/0 = legacy v1).
func MerkleRoot(records []*Record) string {
	v := 1
	if len(records) > 0 {
		v = records[0].schemeVersion()
	}
	hashes := make([]string, len(records))
	for i, r := range records {
		hashes[i] = r.hashForVersion(v)
	}
	if v >= 2 {
		return buildMerkleTreeV2(hashes)
	}
	return buildMerkleTreeV1(hashes)
}

func buildMerkleTreeV1(hashes []string) string {
	if len(hashes) == 0 {
		return ""
	}
	level := append([]string(nil), hashes...)
	for len(level) > 1 {
		if len(level)%2 != 0 {
			level = append(level, level[len(level)-1])
		}
		next := make([]string, 0, len(level)/2)
		for i := 0; i < len(level); i += 2 {
			sum := sha256.Sum256([]byte(level[i] + level[i+1]))
			next = append(next, hex.EncodeToString(sum[:]))
		}
		level = next
	}
	return level[0]
}

func combineV2(left, right string) string {
	h := sha256.New()
	h.Write([]byte{merkleNodePrefix})
	h.Write([]byte(left))
	h.Write([]byte(right))
	return hex.EncodeToString(h.Sum(nil))
}

// buildMerkleTreeV2 mirrors the server: internal-node domain separation and odd
// nodes promoted (not duplicated — the CVE-2012-2459 weakness).
func buildMerkleTreeV2(hashes []string) string {
	if len(hashes) == 0 {
		return ""
	}
	level := append([]string(nil), hashes...)
	for len(level) > 1 {
		next := make([]string, 0, (len(level)+1)/2)
		for i := 0; i < len(level); i += 2 {
			if i+1 < len(level) {
				next = append(next, combineV2(level[i], level[i+1]))
			} else {
				next = append(next, level[i])
			}
		}
		level = next
	}
	return level[0]
}

// VerifyRecordsLocally recomputes the Merkle root of the given (ordered) records
// and compares it with an expected root — e.g. the one anchored on the blockchain.
// Returns true only if every record's content still hashes to the anchored root.
func VerifyRecordsLocally(records []*Record, expectedRoot string) bool {
	return MerkleRoot(records) == expectedRoot
}
