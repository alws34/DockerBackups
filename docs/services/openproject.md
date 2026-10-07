# OpenProject

Worker type: `openproject` · Method: REST API v3 (HAL+JSON)

## What's Backed Up

One `.tar.gz` with a JSON file per collection, each record exactly as the API
returns it, including its HAL `_links` (so a status, assignee, version or
parent can be resolved back to the record it points at):

- `projects` — every project (and program/portfolio) the token's user can see
- `work_packages` — all work packages in those projects, **open and closed**
- `relations` — relations between work packages (follows, blocks, relates, …)
- `versions`, `categories` (per project)
- `statuses`, `types`, `priorities` — so the export is self-describing
- `time_entries`, `memberships` (with roles)
- `principals` — users, groups and placeholder users that are members of a
  visible project (no passwords or tokens; e-mail only where the API shows it)
- `queries` — saved work package views
- `news`
- `attachments` — the **list** of files attached to each work package
  (name, size, content type, author, download link). The files themselves are
  **not** downloaded.

**Not included:** attachment file contents, wiki pages (API v3 has no way to
list them), meetings, documents, forums and instance settings. Back up
OpenProject's database and `assets` volume if you need those.

Exports only what the token's user can see, so use an administrator's token
for a complete backup.

## Setup

1. In OpenProject, open the avatar menu → **My account → Access tokens**.
2. In the **API** row click **Generate** (or **+ API token**) and copy the
   token; it is shown only once.
3. If the API row is missing, an administrator must enable it under
   **Administration → API → Enable REST web service**.
4. Set the variables below in the GUI or `.env`.
5. Enable the `openproject` service.

## `.env` Variables

| Variable                | Required | Notes                                                    |
|-------------------------|----------|----------------------------------------------------------|
| `OPENPROJECT_URL`       | Yes      | Base URL, e.g. `https://openproject.example.com`         |
| `OPENPROJECT_API_TOKEN` | Yes      | API token from My account → Access tokens → API          |

## Restoring

This export is a **readable reference**, not a file OpenProject can import. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a script against the same API v3 endpoints, which also accept writes).

New records get new IDs, so a script has to create statuses/types/projects
first and map old IDs (from each record's `_links`) to new ones before
creating work packages, relations and time entries. Attachments and wiki
pages cannot be restored from this backup.
