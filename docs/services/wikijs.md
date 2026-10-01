# Wiki.js

Worker type: `wikijs` · Method: GraphQL API

## What's Backed Up

Every page's content and metadata (title, path, content type, last updated),
written out as individual Markdown/HTML files and bundled into a `tar.gz`.

## Setup

1. Open Wiki.js admin panel → **Administration → API Access**.
2. Enable the API if it isn't already.
3. Click **New API Key**, give it a name, and copy the token.
4. Set the variables below in `.env`.
5. Enable the `wikijs` service in `config/services.json`.

## `.env` Variables

| Variable          | Required | Notes                                         |
|--------------------|----------|-------------------------------------------------|
| `WIKIJS_URL`       | Yes      | Base URL, e.g. `https://wiki.your-domain.com`  |
| `WIKIJS_API_TOKEN` | Yes      | Admin API token from Administration → API Access |
