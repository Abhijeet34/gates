// Package outbound refuses a message that would carry a secret off the machine.
package outbound

import (
	"fmt"
	"regexp"
)

var patterns = []struct {
	name string
	re   *regexp.Regexp
}{
	{"github token", regexp.MustCompile(`\bgh[pousr]_[A-Za-z0-9]{36,}\b`)},
	{"private key", regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----`)},
	{"slack token", regexp.MustCompile(`\bxox[abprs]-[A-Za-z0-9-]{10,}\b`)},
}

// Scan returns an error naming the first secret in msg, or nil.
func Scan(msg string) error {
	for _, p := range patterns {
		if p.re.MatchString(msg) {
			return fmt.Errorf("outbound: refused: the message holds a %s", p.name)
		}
	}
	return nil
}
