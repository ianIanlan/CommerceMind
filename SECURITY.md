# Security Policy

## Reporting a vulnerability

Please do not publish credentials, customer data, exploit details, or production endpoints in an issue. Report security concerns privately to the repository owner through GitHub.

## Secrets and local data

- Copy `.env.example` to `.env` and keep `.env` local.
- Never commit API keys, authentication secrets, database dumps, vector-store files, logs, or conversation exports.
- Use `AUTH_MODE=demo` only for local development.
- Rotate a credential immediately if it has ever appeared in Git history, logs, screenshots, or chat messages.

## Production use

CommerceMind is an engineering prototype. A production deployment must add managed secrets, strong identity and access control, encrypted transport and storage, dependency scanning, audit retention, rate limiting, and an incident-response process.
