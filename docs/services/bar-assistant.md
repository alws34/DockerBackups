# Bar Assistant

Worker type: `bar_assistant` · Method: REST API

## What's Backed Up

For every bar on the instance: cocktails, ingredients, glasses, tags,
utensils, cocktail methods, and collections, plus the top-level bar list
itself. One subfolder per bar inside a single `tar.gz`.

## Setup

1. Open your user profile in Bar Assistant → **API tokens** → create a token.
2. Set the variables below in `.env`.
3. Enable the `bar_assistant` service in `config/services.json`.

## `.env` Variables

| Variable                 | Required | Notes                                                                                                                   |
|----------------------------|----------|-----------------------------------------------------------------------------------------------------------------------|
| `BAR_ASSISTANT_URL`        | Yes      | Base URL including any path prefix. Salt Rim reverse-proxy: `https://yourdomain.tld/bar`. Direct container: `http://bar-assistant:3000` |
| `BAR_ASSISTANT_API_KEY`    | Yes      | API token from your user profile                                                                                        |
