# Security Policy

## Supported versions

Only the latest release on `main` receives security fixes.

| Version | Supported |
|---|---|
| v4.8.x (latest) | Yes |
| < v4.8 | No |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately via GitHub's built-in mechanism:
**[Report a vulnerability](https://github.com/askfredbv/bluesky-bot/security/advisories/new)**

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a minimal proof of concept
- The version(s) affected

You can expect an acknowledgement within **72 hours** and a resolution or status update within **14 days**.

## Scope

This is a personal automation bot. The main areas of security concern are:

- **Credential handling** — API keys for Gemini, Bluesky, and Mastodon are stored as GitHub Actions secrets and stripped from logs by `SafeLogger`. Never commit credentials to the repository.
- **SSRF protection** — The metadata scraper (`src/utils.py`) validates all URLs against a public IP allowlist before fetching, rejects private/loopback addresses, and pins DNS resolution to pre-validated IPs. Redirect chains are validated at each hop.
- **Prompt injection** — Mention text from Bluesky is sanitised and clearly delimited in the prompt with `<<< >>>` markers, instructing the model to treat it as untrusted data only.
- **Dependency vulnerabilities** — Dependabot is enabled and scans dependencies automatically. The lockfile (`requirements.txt`) is enforced by CI.
