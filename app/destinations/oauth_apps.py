"""Client IDs behind the one-click "Login with Google / Microsoft" buttons.

These are device-flow ("TVs and limited input devices") clients, which are public by
design: Google documents that an installed app's client secret is "obviously not treated
as a secret", and Microsoft public clients have no secret at all. Shipping them in source
is what lets users connect without visiting a developer console. Tokens still go straight
from Google/Microsoft to the user's own server; nothing passes through the maintainer.

An empty value means this build has no built-in app for that provider. Users can still
enter their own client in the destination's Advanced settings. How the maintainer
registers these apps: docs/maintainers/oauth-apps.md.
"""

GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
MICROSOFT_CLIENT_ID = ""
