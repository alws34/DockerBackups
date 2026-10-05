# Contributing

Issues and pull requests are welcome, especially new workers and restore helpers.

## Workflow

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Create one branch per change and open a pull request against `dev`. `main` only
   receives tested changes from `dev`, and tagged releases are cut from `main`.
3. Keep PRs focused; CI (lint, tests, Docker build, CodeQL) must pass before merge.

## Local checks

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
ruff check app tests && ruff format --check app tests
PYTHONPATH=. pytest --cov=app
```

Dependencies are locked with hashes by [pip-tools](https://pip-tools.readthedocs.io/).
Edit `requirements.in` (runtime) or `requirements-dev.in` (tests/lint), then:

```bash
pip-compile --generate-hashes --strip-extras requirements.in -o requirements.txt
pip-compile --generate-hashes --strip-extras requirements-dev.in -o requirements-dev.txt
```

## Adding a service

See [Adding a Service](README.md#adding-a-service) in the README. Please include a
setup guide under `docs/services/` and tests that mock the app's API (no live
network in tests).

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## Sign-off

No CLA and no DCO sign-off required. By contributing you agree your work is
released under the project's [MIT license](LICENSE).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
