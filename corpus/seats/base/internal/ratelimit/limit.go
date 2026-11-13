// Package ratelimit bounds how often one caller may hit the control API.
package ratelimit

import (
	"net/http"
	"sync"
	"time"
)

// PerMinute is how many requests one caller may make in a minute.
const PerMinute = 60

// Limiter counts requests per caller in fixed one-minute windows.
type Limiter struct {
	mu     sync.Mutex
	window time.Time
	counts map[string]int
}

// Allow is whether the request may proceed; the caller is the authenticated user.
func (l *Limiter) Allow(r *http.Request, caller string, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if now.Sub(l.window) >= time.Minute {
		l.window = now.Truncate(time.Minute)
		l.counts = map[string]int{}
	}
	l.counts[caller]++
	return l.counts[caller] <= PerMinute
}
