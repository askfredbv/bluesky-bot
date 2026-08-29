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
- **SSRF protection** — Both the RSS feed fetcher and the metadata scraper (`src/utils.py`) validate URLs against a public-IP allowlist before fetching, reject private/loopback/link-local addresses across every resolved A/AAAA record, and pin DNS resolution to the pre-validated IPs to defeat rebinding. Redirect chains are validated at each hop and cross-scheme redirects are blocked. The metadata scraper additionally enforces a domain allow/block list (feeds are a fixed trusted source list, so they rely on the public-IP checks instead).
- **Prompt injection** — Mention text from Bluesky is sanitised and clearly delimited in the prompt with `<<< >>>` markers, instructing the model to treat it as untrusted data only.
- **Dependency vulnerabilities** — Dependabot is enabled and scans dependencies automatically. The lockfile (`requirements.txt`) is enforced by CI.
