# ADR 0001 — Bootstrap scope and blocked admission

Status: accepted for this preparation only. Not a controller authorization.

## Decision

Import the pinned vertical slice plus the six reviewed layer A files. Author navigable source catalogs and install one operator file. Do not add game features. Do not start `init` or `run`.

## Routing that the config records, and does not enforce

Intended assignment: product owner and architect use Sol strong; a hard implementer is Grok normal with Sol strong as the independent reviewer; an easy implementer is Luna xhigh with Grok normal as the reviewer. `sol-medium-normal` is disabled so this file does not offer Sol-normal as a hard author. Terra is disabled. Grok tiers are only `normal`.

`Engine.route` and `admission` in the delivered engine still select a writer without a pre-write check that a different-family reviewer is enabled, smoked, and not waiting. Review exclusion runs when the role is review. Waiting profiles are skipped. Escalation sets `escalated` and the writer tier becomes strong, which can select `sol-medium-strong`. That guarantee is UNIMPLEMENTED. Controller admission is BLOCKED. No tier waiver.

## Consequences

A later independent review of this dirty tree is required before a local commit. Parent unblock is a reviewed engine commit plus a new launch review, not a config edit made here.
