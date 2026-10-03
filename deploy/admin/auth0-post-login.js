const NS = 'https://tuttitrip.gburek.app/';
// Auth0 application "TuttiTrip Admin (oauth2-proxy)" (client ids are public).
const ADMIN_GATE_CLIENT_IDS = ['7RvmbphKAtrd224ADzycUNki6SOAOyej'];

const DISCORD_RE = /^(?:oauth2\|)?discord\|(\d+)$/;

/**
 * Identities of the user that may appear on the allow-list.
 * Emails count only for Google logins (verified by Google); Discord users match by id only.
 */
function identitiesOf(event) {
  const user = event.user;
  const ids = new Set();
  const strategy = (event.connection && event.connection.strategy) || '';
  if (strategy === 'google-oauth2' && user.email && user.email_verified === true) {
    ids.add(user.email.trim().toLowerCase());
  }
  // Database (username/password) accounts, e.g. test-superadmin, match by their Auth0 user_id.
  if (String(user.user_id || '').startsWith('auth0|')) ids.add(String(user.user_id).toLowerCase());
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
    ...split(event.secrets.ALLOWED_USER_IDS).filter((id) => id.startsWith('auth0|')),
  ]);
  const identities = identitiesOf(event);
  const matched = identities.filter((id) => allowList.has(id));
  const isAdmin = matched.length > 0;

  if (ADMIN_GATE_CLIENT_IDS.includes(event.client.client_id)) {
    if (!isAdmin) {
      api.access.deny('To konto nie ma dostępu do narzędzi administracyjnych TuttiTrip.');
      return;
    }
    const discordId = identities.find((id) => /^\d+$/.test(id));
    const email = event.user.email_verified === true && event.user.email
      ? event.user.email.toLowerCase()
      : discordId
        ? `discord-${discordId}@users.tuttitrip.invalid`
        : `${String(event.user.username || event.user.user_id).replace(/[^a-z0-9._-]/gi, '-').toLowerCase()}@users.tuttitrip.invalid`;
    api.idToken.setCustomClaim(`${NS}admin_ids`, matched);
    api.idToken.setCustomClaim(`${NS}admin_email`, email);
    return;
  }

  if (isAdmin) {
    api.accessToken.setCustomClaim(`${NS}roles`, ['admin']);
    api.idToken.setCustomClaim(`${NS}roles`, ['admin']);
  }
};
