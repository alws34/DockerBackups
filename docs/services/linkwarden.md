# Linkwarden

Worker type: `linkwarden` · Method: REST API (migration export endpoint)

## What's Backed Up

A full export via Linkwarden's own migration endpoint — every link and
collection — written as a single JSON file.

## Setup

1. Open Linkwarden → **Settings → Access Tokens → New Access Token**.
2. Set the variables below in `.env`.
3. Enable the `linkwarden` service in `config/services.json`.

## `.env` Variables

| Variable                   | Required | Notes                                      |
|-------------------------------|----------|------------------------------------------------|
| `LINKWARDEN_URL`              | Yes      | Base URL, e.g. `http://linkwarden:3000`       |
| `LINKWARDEN_ACCESS_TOKEN`     | Yes      | Token from Settings → Access Tokens           |
