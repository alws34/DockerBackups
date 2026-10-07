# Open WebUI

Worker type: `openwebui` · Method: REST API

## What's Backed Up

Everything the API key's owner has, one JSON file each:

- `chats`: every chat with its full message history, archived chats included.
  This is the same file as **Settings → Data Controls → Export Chats**.
- `folders` (with their system prompts and attached files), `chat_tags`, `notes`,
  `memories` and `user_settings`
- Workspace: `models` (custom model presets), `prompts`, `tools` (with code),
  `skills`, and `knowledge` (each knowledge base and the list of its files)
- `groups`
- `user`: the key owner's profile and permissions

With an **admin's** key it also saves:

- `functions` (with code and valve settings)
- `config`: the instance config, the same file as **Admin Panel → Settings → Database → Export Config**
- `users`: the user list (names, emails, roles; no passwords)

Anything the key can't read (admin-only, a permission turned off, or a route an
older Open WebUI doesn't have) is skipped instead of failing the backup.
`skipped.json` says what was skipped and why.

**Not included:** uploaded files and the documents inside knowledge bases
(only their names and metadata), generated images, and other users' chats.

`config.json` and `functions.json` can contain secrets such as connection API
keys and valve values. Keep the archive private.

## Setup

1. An admin turns on **Admin Panel → Settings → General → Enable API Keys**.
   For a non-admin user, the admin also allows API keys for that user's group
   (**Admin Panel → Users → Groups → Permissions → Features → API Keys**). If
   **API Key Endpoint Restrictions** is on, add `/api/v1` to the allowed endpoints.
2. As the user to back up, open **Settings → Account → API keys → Create new secret key**
   and copy the `sk-…` key. Use an admin's key to also save functions, config and users.
3. Set the variables below.
4. Enable the `openwebui` service.

## `.env` Variables

| Variable             | Required | Notes                                                   |
|----------------------|----------|---------------------------------------------------------|
| `OPENWEBUI_URL`      | Yes      | Base URL, e.g. `https://chat.your-domain.com`           |
| `OPENWEBUI_API_KEY`  | Yes      | `sk-…` key from Settings → Account → API keys           |

## Restoring

Extract the archive. Most of it imports straight back into Open WebUI:

- **Chats:** log in as the user, open **Settings → Data Controls → Import Chats**
  and pick `chats.json`. Folder links only come back if the folders still exist
  with the same IDs.
- **Instance config:** **Admin Panel → Settings → Database → Import Config**, pick `config.json`.
- **Models, prompts, tools, skills:** each page under **Workspace** has an
  **Import** button. Pick `models.json`, `prompts.json`, `tools.json` or `skills.json`.
- **Functions:** **Admin Panel → Functions → Import**, pick `functions.json`.
- **Knowledge bases:** create them again and re-upload the documents. The
  `knowledge.json` file lists which files each one had.
- **Folders, notes, memories, users, groups:** re-create them by hand from the
  JSON. Memories go in **Settings → Personalization → Memory**.
