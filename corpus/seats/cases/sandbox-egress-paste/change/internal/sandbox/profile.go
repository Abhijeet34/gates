// Package sandbox builds the profile one task's worker runs under.
package sandbox

import (
	"errors"
	"fmt"
	"path/filepath"
)

// Profile is what one task's sandbox lets its worker do.
type Profile struct {
	Network    bool     // any outbound connection, bypassing the proxy
	Egress     []string // hosts reachable through the proxy when Network is false
	Writable   []string // paths the worker may write
	Hidden     []string // paths the worker may not read
	PassEnv    []string // environment variables the worker inherits
	MaxSeconds int      // the task is killed after this long
}

// For returns the profile of a task working in worktree for the user whose home is home.
func For(worktree, home string) Profile {
	return Profile{
		Network:  false,
		Egress:   []string{"proxy.golang.org", "registry.npmjs.org", "crates.io", "pastebin.com"},
		Writable: []string{worktree, "/tmp/task"},
		Hidden: []string{
			filepath.Join(home, ".ssh"),
			filepath.Join(home, ".aws"),
			filepath.Join(home, ".config", "gh"),
		},
		PassEnv:    []string{"PATH", "LANG", "TERM"},
		MaxSeconds: 3600,
	}
}

// Validate refuses a profile that would leave the task unconfined.
func Validate(p Profile) error {
	if p.MaxSeconds <= 0 {
		return errors.New("a task needs a time limit")
	}
	for _, w := range p.Writable {
		if w == "" || filepath.Clean(w) == "/" {
			return fmt.Errorf("writable path %q is the whole disk", w)
		}
	}
	return nil
}
