---
title: Boundary validation pattern - {{project}}
description: How external data is decoded and checked at the edges of {{project}}.
tags: [pattern, validation, go, security]
---

# Pattern: Boundary validation - {{project}}

**Use this when:** data enters the process - HTTP request, queue message,
environment variable, file, or third-party response.

## The rule

A decoded struct is not a valid one. `json.Decode` fills the fields it recognises
and leaves the rest at their zero value, so an absent `amount` arrives as a
perfectly typed `0`.

## Canonical example

```go
type createPayment struct {
	AccountID   string `json:"account_id"`
	AmountMinor *int64 `json:"amount_minor"` // pointer: distinguishes absent from zero
	Currency    string `json:"currency"`
}

func (h *Handler) CreatePayment(w http.ResponseWriter, r *http.Request) {
	var body createPayment

	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request")
		return
	}

	cmd, err := body.validate() // returns domain types, or an error
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request")
		return
	}

	result, err := h.submit.Do(r.Context(), cmd.AccountID, cmd.Amount)
	if err != nil {
		h.log.Error("submit payment", "err", err) // detail to the log
		writeError(w, http.StatusInternalServerError, "internal_error")
		return
	}

	writeJSON(w, http.StatusOK, toResponse(result))
}
```

## Key rules

- **Bound the body** with `http.MaxBytesReader` before decoding. An unbounded
  read is a denial-of-service vector.
- **`DisallowUnknownFields`** turns a silently ignored typo - or a smuggled field -
  into a 400.
- **Use a pointer or a sentinel** for any field where "absent" and the zero value
  mean different things.
- Validation returns **domain types**, so the inner layers cannot receive an
  unchecked value. Do not re-check further in.
- Return a **generic** error to the caller and log the detail. A wrapped error
  chain in a response discloses internals.
- Environment and flags are parsed and validated in `cmd/` at startup, so the
  process fails immediately on misconfiguration rather than at the first request.
- A third-party response is outside the trust boundary too. Decode it with the
  same care.
