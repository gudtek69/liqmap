# Security Policy

## Reporting a vulnerability

Please report security issues **privately**. Don't open a public issue.

1. Go to the repository's **Security** tab on GitHub.
2. Click **"Report a vulnerability"** to open a private security advisory.
3. Describe the issue, how to reproduce it, and its impact.

Only the maintainers can see the report. We'll acknowledge it as soon as we can, work on a fix privately, and credit you in the advisory if you want. Please give us reasonable time to release a fix before you disclose anything publicly.

## Scope

In scope:

- The local web server in `web/fetcher.py`: binding, path handling, file whitelist, security headers, Host-header checks.
- The map page (`web/index.html`, `web/app.js`, `web/style.css`), e.g. script injection from API data, CSP bypasses.
- The tracker / optimizer scripts in `tracker/`: handling of untrusted API responses, unsafe parsing, file writes.
- `start.sh` and the shipped configuration examples.

Out of scope:

- The accuracy of the model or its predictions. It's a research toy, not financial advice.
- Vulnerabilities in the third-party public APIs themselves (Hyperliquid, OKX). Report those to the providers.
- Problems that only exist when you deliberately expose the server to a network (e.g. `LIQMAP_HOST=0.0.0.0`), beyond what's documented as risky.

## Important: no keys or wallets, ever

This project uses **only free public market-data endpoints**. It **never** needs an API key, an exchange login, a wallet, a wallet address, or a private key or seed phrase, and it has no trading code.

- **Never paste a private key or seed phrase** into this tool, its `.env` file, an issue, a security report, or a chat about it.
- If someone (including someone claiming to be a maintainer) asks for one, it's a scam.
- Leave secrets and personal data out of security reports. They aren't needed to reproduce anything here.
