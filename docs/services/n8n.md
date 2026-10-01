# n8n

Worker type: `n8n` · Method: REST API

## What's Backed Up

All workflows, tags, and variables, exported as a single JSON file.
(The variables endpoint was added in n8n v1.0 — on older instances it's
skipped gracefully rather than failing the backup.)

## Setup

1. Open n8n → **Settings → n8n API → Create an API key**.
2. Set the variables below in `.env`.
3. Enable the `n8n` service in `config/services.json`.

## `.env` Variables

| Variable       | Required | Notes                                   |
|------------------|----------|---------------------------------------------|
| `N8N_URL`        | Yes      | Base URL, e.g. `https://n8n.your-domain.com` |
| `N8N_API_KEY`    | Yes      | API key from Settings → n8n API             |
