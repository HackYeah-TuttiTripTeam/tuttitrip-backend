"""pgAdmin settings for tuttitrip-pgadmin (mounted as /pgadmin4/config_local.py).

Login happens in front of pgAdmin: tuttitrip-admin-gateway asks oauth2-proxy
(Auth0 + superadmin allow-list) and forwards the verified email in
X-Auth-Request-Email, overwriting whatever the client sent. pgAdmin only
accepts that header from the tuttitrip-admin network (the gateway is the only
thing there that proxies user traffic) and only together with a shared secret
that the gateway injects. Values come from the container env (host env
files written by deploy/admin/setup.sh); nothing secret is in this file.
"""

import os

SERVER_MODE = True
AUTHENTICATION_SOURCES = ["webserver"]
WEBSERVER_AUTO_CREATE_USER = True
WEBSERVER_REMOTE_USER = "X-Auth-Request-Email"
WEBSERVER_REMOTE_USER_FROM_HEADER = True
WEBSERVER_TRUSTED_PROXIES = [
    p for p in os.environ.get("TUTTITRIP_PGADMIN_TRUSTED_PROXIES", "").split(",") if p
]
WEBSERVER_SHARED_SECRET = os.environ["TUTTITRIP_PGADMIN_WEBSERVER_SECRET"]

# Discord admins without an email log in as discord-<id>@users.tuttitrip.invalid.
ALLOW_SPECIAL_EMAIL_DOMAINS = ["invalid"]

# Saved passwords (e.g. a write connection added by hand) are encrypted with a
# key printed by this hook (host-generated, never typed by anyone).
MASTER_PASSWORD_REQUIRED = True
MASTER_PASSWORD_HOOK = "/pgadmin4/tuttitrip-master-password.sh %u"

# Sessions are bound to the client IP otherwise; behind Cloudflare + gateway
# the peer address is not stable.
ENHANCED_COOKIE_PROTECTION = False
UPGRADE_CHECK_ENABLED = False
MFA_ENABLED = False  # Auth0 handles the login
