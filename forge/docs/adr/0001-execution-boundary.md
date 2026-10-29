# ADR-0001: Enforced native execution boundary

Status: Accepted. Enforcement for native calls and gate sandboxes implemented in P1
(`src/forge/sandbox.py`); gate command wiring is P2, live native smokes are P5.

Forge's deterministic controller owns trusted config, state, git index, credentials
and integration decisions. Target source, model output and tests are untrusted.
Changing HOME, filtering environment or instructing an agent is not isolation.

Every native call and target gate command runs in bubblewrap with PID and
other namespaces, die-with-parent, clearenv, fixed system mounts and an exact
selected workspace leaf. Unsupported or failed enforcement disables the route and
blocks acceptance; there is no host fallback. Protected roots include every hidden
top-level HOME entry (except the `.local` container) so new credential directories
are protected by default. Host note: `/etc/resolv.conf` links into
`/run/systemd/resolve`, which network-enabled calls bind read-only.

Mounts are classified as controller-selected work (/work), input (/in), exact
native executable (/opt/forge-native/bin/<harness>), operator-approved in-use
harness leaves, or read-only gate toolchain extras. Canonical paths and inode
identity must be revalidated before launch. State/config/HOME roots, ancestors,
sibling workspaces/calls and other credentials remain unavailable. Only the exact
selected work/input leaves may be exposed from state. An executable may overlap
the in-use harness leaf; this is the sole permitted source overlap. Operator
extras cannot impersonate controller mount classes. Gate calls omit harness
credentials and native mounts; network is disabled by default. Agent calls need
network and the selected harness's credentials.

Accepted risk: an agent and its tools can read/exfiltrate that one harness's own
credentials over its network. Other harnesses, SSH/GPG, real HOME and controller
state must remain unmounted. Smoke tests must verify the exact binary/model/effort,
resume SID/write behavior, read-only denial and image behavior; installed binaries
and config eligibility alone prove none of these capabilities.

Controller git operations will use trusted target GIT_DIR, disabled hooks/fsmonitor
and a controller-private index, never agent-clone config. Gates and fresh independent
reviews consume exports of a durable candidate commit; tree equality is checked
after both. A pending exact-candidate record precedes compare-and-swap acceptance;
external branch movement requires re-gate and fresh review. Cancellation after the
pending record cannot undo acceptance. Full crash/cancel recovery is P2/P3 work.
