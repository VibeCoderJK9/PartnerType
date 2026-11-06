# Security policy

PartnerType stores partner contracts, client contacts and money owed, so we treat security reports
as the highest priority.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately, in either way:

- GitHub: **Security › Report a vulnerability** on this repository (private advisory), or
- e-mail: **kontakt@partnertype.com** with the subject `SECURITY`.

Please include the affected version or commit, steps to reproduce, and the impact you expect. A
proof of concept against your own local instance (see the demo in the README) is welcome; please do
not test against instances you do not own.

## What happens next

- We confirm that we received the report within **3 working days**.
- We give a first assessment (accepted or not, and severity) within **10 working days**.
- We fix accepted issues as fast as their severity requires and tell you before the fix is public.
- Once the fix is released we publish an advisory and, if you wish, credit you by name.

## Supported versions

PartnerType is released from the `main` branch. Security fixes go to `main`; please update to the
latest commit or release.

## Scope

In scope: the application (`api/`, `js/`, `pages/`, `server.py`), the API gateway and MCP endpoint,
and the operator tooling (`tools/`, `deploy/`, `Dockerfile`).

Out of scope: the local demo in `compose.demo.yaml` (it uses fixed, public passwords by design),
findings that need a compromised operator machine, and denial of service by volume.

## How the project guards security

- Every change runs the full test suite in CI (over 4,000 tests).
- `scripts/security-sink-audit.mjs` blocks unreviewed HTML sinks in the frontend.
- `scripts/security-scan.sh` scans Python dependencies (`pip-audit`) and the release image (trivy).
- Dependabot keeps dependencies and GitHub Actions up to date.
