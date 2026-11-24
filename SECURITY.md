# Security and responsible testing

Use WebDoctor only on websites and accounts you own or have explicit permission to test. Prefer a staging environment and a dedicated test account.

Never commit API keys, passwords, browser session files, or generated reports containing private URLs or screenshots. Authenticated runs may save reusable session cookies in `storage_state.json`; protect that file like a password and delete it when no longer needed. Generated reports are excluded from Git by the repository's `.gitignore`.

For security issues in WebDoctor itself, contact the repository owner privately rather than posting sensitive exploit details publicly.
