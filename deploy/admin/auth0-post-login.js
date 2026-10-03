/**
 * Auth0 post-login Action "TuttiTrip superadmins" (trigger post-login, node22).
 *
 * One allow-list, two effects:
 *  - admin gate client (oauth2-proxy in front of pgAdmin / DBOS dashboard):
 *    people outside the list are denied; listed people get the id-token claims
 *    oauth2-proxy checks against its own copy of the list (host env files);
 *  - every other client (TuttiTrip Web): listed people get
 *    `https://tuttitrip.gburek.app/roles: ["admin"]` in the access token (read
 *    by the backend: CurrentUser.roles / require_admin) and the id token.
 *
 * Secrets (Action secrets, never in a repo), comma-separated:
 *   ALLOWED_EMAILS       emails; match only when Auth0 marks them verified
 *   ALLOWED_DISCORD_IDS  Discord user ids (sub = oauth2|discord|<id>);
 *                        Discord users are matched by id, never by username
 */
const NS = 'https://tuttitrip.gburek.app/';
// Auth0 application "TuttiTrip Admin (oauth2-proxy)" (client ids are public).
const ADMIN_GATE_CLIENT_IDS = ['7RvmbphKAtrd224ADzycUNki6SOAOyej'];

const DISCORD_RE = /^(?:oauth2\|)?discord\|(\d+)$/;

/** Identities of the user that may appear on the allow-list. */
function identitiesOf(user) {
  const ids = new Set();
  if (user.email && user.email_verified === true) ids.add(user.email.trim().toLowerCase());
  const candidates = [user.user_id || ''];
  for (const identity of user.identities || []) {
    if (identity.connection === 'discord') candidates.push(`discord|${identity.user_id}`);
  }
  for (const candidate of candidates) {
    const match = DISCORD_RE.exec(String(candidate));
    if (match) ids.add(match[1]);
  }
  return [...ids];
}

exports.onExecutePostLogin = async (event, api) => {
  const split = (value) => String(value || '')
    .split(',')
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
  const allowList = new Set([
    ...split(event.secrets.ALLOWED_EMAILS),
    ...split(event.secrets.ALLOWED_DISCORD_IDS).filter((id) => /^\d+$/.test(id)),
  ]);
  const identities = identitiesOf(event.user);
  const matched = identities.filter((id) => allowList.has(id));
  const isAdmin = matched.length > 0;

  if (ADMIN_GATE_CLIENT_IDS.includes(event.client.client_id)) {
    if (!isAdmin) {
      api.access.deny('To konto nie ma dostępu do narzędzi administracyjnych TuttiTrip.');
      return;
    }
    // oauth2-proxy: groups = matched identities (checked against its own list),
    // email = a stable, email-shaped login for pgAdmin (Discord may lack one).
    const discordId = identities.find((id) => /^\d+$/.test(id));
    const email = event.user.email_verified === true && event.user.email
      ? event.user.email.toLowerCase()
      : `discord-${discordId}@users.tuttitrip.invalid`;
    api.idToken.setCustomClaim(`${NS}admin_ids`, matched);
    api.idToken.setCustomClaim(`${NS}admin_email`, email);
    return;
  }

  if (isAdmin) {
    api.accessToken.setCustomClaim(`${NS}roles`, ['admin']);
    api.idToken.setCustomClaim(`${NS}roles`, ['admin']);
  }
};

// Exported for the local test (deploy/admin/test-auth0-action.mjs); Auth0 ignores it.
exports.identitiesOf = identitiesOf;
