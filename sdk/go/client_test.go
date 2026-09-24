package aeternislog

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

// TestCreateRecord verifies the request shape and the trustless hash check: the
// server (here mimicked) hashes new records under the v2 scheme, exactly like
// the real API, and the client must independently compute the same hash.
func TestCreateRecord(t *testing.T) {
	var got map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &got)

		rec := &Record{
			ID:          got["id"].(string),
			Timestamp:   got["timestamp"].(string),
			Source:      got["source"].(string),
			Payload:     got["payload"].(map[string]interface{}),
			HashVersion: 2,
		}
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{"id": rec.ID, "hash": rec.ComputeHash()},
		})
	}))
	defer srv.Close()

	c := New(srv.URL)
	rec, err := c.CreateRecord(context.Background(), "audit", "crm", map[string]interface{}{"k": 1}, nil)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if rec.Domain != "audit" || rec.Source != "crm" || rec.ID == "" {
		t.Errorf("unexpected record: %+v", rec)
	}
	if rec.HashVersion != 2 {
		t.Errorf("created record must carry the server's hash version 2, got %d", rec.HashVersion)
	}
	if got["source"] != "crm" {
		t.Errorf("server did not receive source, got %v", got)
	}
}

// TestCreateRecordHashMismatch verifies the client rejects a server hash that
// does not match its independent computation.
func TestCreateRecordHashMismatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{"id": "x", "hash": "deadbeef"},
		})
	}))
	defer srv.Close()

	c := New(srv.URL)
	if _, err := c.CreateRecord(context.Background(), "audit", "crm", map[string]interface{}{"k": 1}, nil); err == nil {
		t.Error("expected an error when the server hash does not match the local hash")
	}
}

// TestVerifyBatchExposesAnchor verifies that the on-chain root and anchor status
// are surfaced, so callers can tell a ledger-backed verdict from a local-only one
// and can compare a locally recomputed root against the ledger, not the database.
func TestVerifyBatchExposesAnchor(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"batch_id":"b1","is_valid":true,"num_logs":3,
			"original_merkle_root":"db","recalculated_merkle_root":"chain",
			"on_chain_merkle_root":"chain","anchor_status":"ANCHORED",
			"integrity":"VALID","message":"ok"}`))
	}))
	defer srv.Close()

	res, err := New(srv.URL).VerifyBatch(context.Background(), "audit", "b1")
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if res.OnChainMerkleRoot != "chain" || res.AnchorStatus != "ANCHORED" || res.NumRecords != 3 {
		t.Errorf("anchor fields not exposed: %+v", res)
	}
}

func TestRetryOn5xx(t *testing.T) {
	var count int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if atomic.AddInt32(&count, 1) < 3 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := New(srv.URL, WithMaxRetries(3))
	var out map[string]interface{}
	if err := c.do(context.Background(), http.MethodGet, "/x", nil, &out); err != nil {
		t.Fatalf("expected success after retries, got %v", err)
	}
	if c := atomic.LoadInt32(&count); c != 3 {
		t.Errorf("expected 3 attempts (2 failures + success), got %d", c)
	}
}

func TestNoRetryOn4xx(t *testing.T) {
	var count int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&count, 1)
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer srv.Close()

	c := New(srv.URL, WithMaxRetries(3))
	err := c.do(context.Background(), http.MethodGet, "/x", nil, nil)
	if err == nil {
		t.Fatal("expected an error for 4xx")
	}
	if c := atomic.LoadInt32(&count); c != 1 {
		t.Errorf("4xx must not be retried, got %d attempts", c)
	}
}
