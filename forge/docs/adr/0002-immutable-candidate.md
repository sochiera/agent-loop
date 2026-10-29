# ADR 0002: Commit candidates before testing and review

Status: accepted for P2 implementation of the approved owner contract.

The controller creates a candidate commit with a preservation ref before any gate
or review. Its tree is the shared fingerprint for deterministic commands, fresh
read-only review and local acceptance. Mutable agent workspaces and their git
metadata are never acceptance inputs after snapshotting.

Gate commands use fresh writable exports in bubblewrap, followed by an exact tree
check; ignored build outputs are allowed. Review uses a different fresh export
mounted read-only. Controller-only hashed receipts bind both checks to the exact
candidate. Bugs additionally require persisted assertion-only RED proof and
unchanged regression blobs in GREEN.

The durable pending SQLite row is the acceptance point. CAS advances only the
local branch to that pre-existing candidate. Recovery finishes the same candidate
without creating another accepted task commit. External branch movement requires
a clean merge-tree candidate, renewed gates and a new independent review; conflict
preserves both sides and blocks. A clean merge re-clones the task workspace at
the merged commit (old workspace preserved), so later fix candidates cannot revert
external main work; recovery does not replay a pending candidate superseded by it. Cancellation is serialized against the pending
row. Idempotent notes carry receipt provenance without changing candidate content.

Costs: full clone/export/copy operations use disk and time. The first version
rejects gitlinks and exports changed by archive attributes. These explicit blocks
keep the controller away from agent-controlled git configuration and prevent a
silently different tested tree. No remote or deployment operation is introduced.
