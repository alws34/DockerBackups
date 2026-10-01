# KitchenOwl

Worker type: `kitchenowl` · Method: REST API

## What's Backed Up

For every household: recipes, the item/ingredient catalogue, and shopping
lists, bundled into a `tar.gz`.

## Setup

1. Get a long-lived token via KitchenOwl **Settings → Account → Long-Lived
   Tokens**, or call `POST /api/auth/llt` with a logged-in session.
2. Set the variables below in `.env`.
3. Enable the `kitchenowl` service in `config/services.json`.

## `.env` Variables

| Variable            | Required | Notes                                      |
|-----------------------|----------|-----------------------------------------------|
| `KITCHENOWL_URL`      | Yes      | Base URL, e.g. `http://kitchenowl:80`        |
| `KITCHENOWL_TOKEN`    | Yes      | Long-lived access token                       |
