package aeternislog

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Client talks to the Tamper-Evident Data Anchoring API.
type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
	maxRetries int
}

// Option configures a Client.
type Option func(*Client)

// WithAPIKey sets the API key (sent as X-API-Key).
func WithAPIKey(key string) Option { return func(c *Client) { c.apiKey = key } }

// WithHTTPClient overrides the HTTP client.
func WithHTTPClient(h *http.Client) Option { return func(c *Client) { c.httpClient = h } }

// WithMaxRetries sets how many times transient failures (network errors, 5xx)
// are retried.
func WithMaxRetries(n int) Option { return func(c *Client) { c.maxRetries = n } }

// New creates a client for the API at baseURL (e.g. "http://localhost:5001").
func New(baseURL string, opts ...Option) *Client {
	c := &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		httpClient: &http.Client{Timeout: 10 * time.Second},
		maxRetries: 3,
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

// APIError is returned for non-2xx responses that are not retried.
type APIError struct {
	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("anchor: api error %d: %s", e.StatusCode, e.Body)
}

// CreateRecord creates a record in the domain. The id and timestamp are generated
// client-side so the returned record is fully known and locally verifiable; the
// server-returned hash is then checked against the independently computed hash.
func (c *Client) CreateRecord(ctx context.Context, domain, source string, payload map[string]interface{}, hashFields []string) (*Record, error) {
	rec := &Record{
		Domain:      domain,
		ID:          newID(),
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		Source:      source,
		Payload:     payload,
		HashFields:  hashFields,
		HashVersion: CurrentHashVersion,
	}
	rec.Hash = rec.ComputeHash()

	reqBody := map[string]interface{}{
		"id":        rec.ID,
		"timestamp": rec.Timestamp,
		"source":    source,
		"payload":   payload,
	}
	if len(hashFields) > 0 {
		reqBody["hash_fields"] = hashFields
	}

	var resp struct {
		Data struct {
			ID   string `json:"id"`
			Hash string `json:"hash"`
		} `json:"data"`
	}
	if err := c.do(ctx, http.MethodPost, "/api/v1/"+domain+"/records", reqBody, &resp); err != nil {
		return nil, err
	}
	// Trustless check: the server's hash must equal our independent hash.
	if resp.Data.Hash != rec.Hash {
		return nil, fmt.Errorf("anchor: server hash %q does not match local hash %q", resp.Data.Hash, rec.Hash)
	}
	return rec, nil
}

// GetRecord fetches a record by id.
func (c *Client) GetRecord(ctx context.Context, domain, id string) (*Record, error) {
	var rec Record
	if err := c.do(ctx, http.MethodGet, "/api/v1/"+domain+"/records/"+id, nil, &rec); err != nil {
		return nil, err
	}
	return &rec, nil
}

// BatchResult is returned by BatchRecords.
type BatchResult struct {
	BatchID    string `json:"batch_id"`
	Domain     string `json:"domain"`
	MerkleRoot string `json:"merkle_root"`
	NumRecords int    `json:"num_records"`
	TxID       string `json:"tx_id"`
	Anchored   bool   `json:"anchored"`
}

// BatchRecords batches the domain's pending records, anchors the Merkle root on
// the blockchain, and returns the batch.
func (c *Client) BatchRecords(ctx context.Context, domain string) (*BatchResult, error) {
	var res BatchResult
	if err := c.do(ctx, http.MethodPost, "/api/v1/"+domain+"/records/batch", map[string]interface{}{}, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// VerifyResult is the outcome of a server-side batch verification.
//
// OnChainMerkleRoot is the root read from the ledger. It is the value to compare
// a locally recomputed root against (VerifyRecordsLocally). OriginalMerkleRoot
// is only the root stored in the database. AnchorStatus tells whether the
// verdict was decided against the ledger (ANCHORED) or only against local state
// (UNKNOWN), or whether the batch is not on the ledger at all (UNANCHORED).
type VerifyResult struct {
	BatchID                string `json:"batch_id"`
	IsValid                bool   `json:"is_valid"`
	NumRecords             int    `json:"num_logs"`
	OriginalMerkleRoot     string `json:"original_merkle_root"`
	RecalculatedMerkleRoot string `json:"recalculated_merkle_root"`
	OnChainMerkleRoot      string `json:"on_chain_merkle_root"`
	AnchorStatus           string `json:"anchor_status"`
	Integrity              string `json:"integrity"`
	Message                string `json:"message"`
}

// VerifyBatch asks the server to verify a batch's integrity. A CORRUPTED batch
// (HTTP 409) is returned as a result with IsValid=false, not as an error.
func (c *Client) VerifyBatch(ctx context.Context, domain, batchID string) (*VerifyResult, error) {
	var res VerifyResult
	err := c.do(ctx, http.MethodPost, "/api/v1/"+domain+"/records/verify/"+batchID, nil, &res)
	if err != nil {
		var apiErr *APIError
		if errors.As(err, &apiErr) && apiErr.StatusCode == http.StatusConflict {
			if json.Unmarshal([]byte(apiErr.Body), &res) == nil {
				return &res, nil
			}
		}
		return nil, err
	}
	return &res, nil
}

// do performs a request, retrying transient failures (network errors and 5xx)
// up to maxRetries with a linear backoff. 4xx responses are returned immediately.
func (c *Client) do(ctx context.Context, method, path string, body, out interface{}) error {
	var payload []byte
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		payload = b
	}

	var lastErr error
	for attempt := 0; attempt <= c.maxRetries; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(attempt) * 200 * time.Millisecond):
			}
		}

		req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, bytes.NewReader(payload))
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/json")
		if c.apiKey != "" {
			req.Header.Set("X-API-Key", c.apiKey)
		}

		resp, err := c.httpClient.Do(req)
		if err != nil {
			lastErr = err // network error: retry
			continue
		}
		respBody, _ := io.ReadAll(resp.Body)
		resp.Body.Close()

		if resp.StatusCode >= 500 {
			lastErr = &APIError{StatusCode: resp.StatusCode, Body: string(respBody)}
			continue // server error: retry
		}
		if resp.StatusCode >= 400 {
			return &APIError{StatusCode: resp.StatusCode, Body: string(respBody)}
		}
		if out != nil && len(respBody) > 0 {
			return json.Unmarshal(respBody, out)
		}
		return nil
	}
	return lastErr
}

// newID returns a random 128-bit hex id.
func newID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return hex.EncodeToString(b)
}
