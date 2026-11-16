// Package permit decides whether a worker may run one shell command.
package permit

import "regexp"

// Decision is what the permit host answers for one command.
type Decision int

const (
	Deny Decision = iota
	Allow
	Ask
)

type rule struct {
	pattern  *regexp.Regexp
	decision Decision
	why      string
}

// rules are checked in order; the first match decides.
var rules = []rule{
	{regexp.MustCompile(`(^|[;&|]\s*)rm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME)(\s|$)`), Deny, "recursive delete of the root or a home"},
	{regexp.MustCompile(`\b(curl|wget)\b[^|]*\|\s*(ba|z|da)?sh\b`), Deny, "piping a download into a shell"},
	{regexp.MustCompile(`\bgit\s+push\b`), Ask, "push"},
	{regexp.MustCompile(`\bgit\s+push\b.*(--force|-f\b)`), Deny, "force push"},
	{regexp.MustCompile(`^(go|npm|cargo)\s+test(\s+[\w./-]+)*$`), Allow, "a test run"},
	{regexp.MustCompile(`^git\s+(status|diff|log)(\s+[\w./-]+)*$`), Allow, "a read-only git command"},
}

// Check returns the decision for cmd and why. A command no rule matches is denied.
func Check(cmd string) (Decision, string) {
	for _, r := range rules {
		if r.pattern.MatchString(cmd) {
			return r.decision, r.why
		}
	}
	return Deny, "no rule allows it"
}
