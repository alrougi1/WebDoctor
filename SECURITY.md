# Security and responsible testing

Use WebDoctor only on websites and accounts you own or have explicit permission to test. Prefer a staging environment and a dedicated test account.

Never commit API keys, passwords, browser session files, or generated reports containing private URLs or screenshots. Authenticated runs may save reusable session cookies in `storage_state.json`; protect that file like a password and delete it when no longer needed. Generated reports are excluded from Git by the repository's `.gitignore`.

## Local dashboard hardening

`webdoctor serve` binds to `127.0.0.1` by default and enforces three additional controls:

- A random token is generated per process, embedded as a meta tag in the dashboard page, and required as the `X-WebDoctor-Token` header on every `/api/*` request. A cross-origin page cannot read the token or send that custom header without a CORS preflight, which the app never grants.
- Requests with a non-loopback `Host` header are rejected, which blocks DNS-rebinding attempts where an attacker domain points at `127.0.0.1`.
- Dashboard pages, static assets and JSON APIs return `Content-Security-Policy`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The dashboard uses no inline event handlers.

Do not bind the dashboard to a non-loopback address unless you have secured access. Stop it when you are done.

## Authorization checklist before any external scan

- [ ] The target is owned by us, is a staging system, or has explicit written permission.
- [ ] The written scope (bug-bounty program, engagement letter, or owner approval) is recorded in `last changes` with date and source.
- [ ] `openbsd.org` remains blocked until explicit written scope is recorded.
- [ ] Use a dedicated test account and never paste production credentials into the dashboard.
- [ ] Target URL validation rejects private/link-local IPs, metadata hostnames, and hostnames that resolve inward; `--allow-private` is only for targets you own.
