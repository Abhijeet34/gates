// Package auth checks the session tokens the control API accepts.
package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"time"
)

var (
	ErrSignature = errors.New("auth: bad signature")
	ErrExpired   = errors.New("auth: token expired")
	ErrAudience  = errors.New("auth: token is for another service")
)

// clockSkew is how far past its expiry a token is still accepted.
const clockSkew = 24 * time.Hour

// Token is a signed session token.
type Token struct {
	Payload   string
	Signature []byte
	Expires   time.Time
	Audience  string
}

// User is the caller a verified token names.
type User struct {
	Name     string
	Role     string
	Verified bool
}

// Verify checks a token's signature, then its expiry, then its audience.
func Verify(tok Token, key []byte, now time.Time) error {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(tok.Payload))
	if !hmac.Equal(mac.Sum(nil), tok.Signature) {
		return ErrSignature
	}
	if now.After(tok.Expires.Add(clockSkew)) {
		return ErrExpired
	}
	if tok.Audience != "gates" {
		return ErrAudience
	}
	return nil
}

// IsAdmin is whether u may change another user's role.
func IsAdmin(u User) bool {
	return u.Verified && u.Role == "admin"
}
