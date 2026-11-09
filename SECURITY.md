# Security policy

## Reporting a vulnerability

Please report it privately, not in a public issue: open
[a private security advisory](https://github.com/mattew124/teslamate-expenses/security/advisories/new)
on this repository. Say what you found, how to reproduce it, and what it could expose.

This is a small project maintained in spare time. Expect an acknowledgement within a few days and a
fix or an explanation as soon as practical. A fix is released as a new version, and the advisory
credits you if you'd like.

## Supported versions

Only the latest release (and the current release candidate, if there is one). Upgrade to get fixes.

## What matters most

- The app has **no login of its own**. It must only ever be published behind a forward-auth gate;
  anything that bypasses or weakens that assumption is in scope.
- The OneDrive sign-in is stored encrypted, with a key (`EXPENSES_ENCRYPTION_KEY`) kept outside the
  database. Anything that exposes the key, the client secret or the saved tokens is in scope.
- The project is "vibe coded" (see the README). It has tests, static analysis and dependency and image
  scanning, but has not had a manual security review.

## Out of scope

Findings that need an attacker who is already logged in to your forward-auth gate and only reach
data that user can already see, and issues in TeslaMate, Grafana or Traefik themselves.
