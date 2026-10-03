// Local test of auth0-post-login.js with fake events: node deploy/admin/test-auth0-action.mjs
// Uses made-up identities only (the real allow-list never enters the repo).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const action = createRequire(import.meta.url)('./auth0-post-login.js');
const ADMIN_CLIENT = '7RvmbphKAtrd224ADzycUNki6SOAOyej';
const WEB_CLIENT = 'Q18lEcCNyP3Xut5wnuHYrGsoVf1pbKdl';
const NS = 'https://tuttitrip.gburek.app/';
const SECRETS = { ALLOWED_EMAILS: 'Admin@Example.com, other@example.com', ALLOWED_DISCORD_IDS: '111111111111111111, ' };

async function run(user, clientId) {
  const calls = { denied: null, id: {}, access: {} };
  const api = {
    access: { deny: (reason) => { calls.denied = reason; } },
    idToken: { setCustomClaim: (k, v) => { calls.id[k] = v; } },
    accessToken: { setCustomClaim: (k, v) => { calls.access[k] = v; } },
  };
  await action.onExecutePostLogin({ user, client: { client_id: clientId }, secrets: SECRETS }, api);
  return calls;
}

const google = (email, verified = true) => ({
  user_id: 'google-oauth2|1', email, email_verified: verified,
  identities: [{ connection: 'google-oauth2', provider: 'google-oauth2', user_id: '1' }],
});
const discord = (id, email) => ({
  user_id: `oauth2|discord|${id}`, email, email_verified: false,
  identities: [{ connection: 'discord', provider: 'oauth2', user_id: `discord|${id}` }],
});

test('admin gate: listed email gets in with claims', async () => {
  const c = await run(google('admin@example.com'), ADMIN_CLIENT);
  assert.equal(c.denied, null);
  assert.deepEqual(c.id[`${NS}admin_ids`], ['admin@example.com']);
  assert.equal(c.id[`${NS}admin_email`], 'admin@example.com');
});

test('admin gate: unverified listed email is denied', async () => {
  assert.ok((await run(google('admin@example.com', false), ADMIN_CLIENT)).denied);
});

test('admin gate: listed Discord id gets in, unverified email ignored', async () => {
  const c = await run(discord('111111111111111111', 'x@example.com'), ADMIN_CLIENT);
  assert.equal(c.denied, null);
  assert.deepEqual(c.id[`${NS}admin_ids`], ['111111111111111111']);
  assert.equal(c.id[`${NS}admin_email`], 'discord-111111111111111111@users.tuttitrip.invalid');
});

test('admin gate: strangers are denied', async () => {
  assert.ok((await run(google('someone@example.com'), ADMIN_CLIENT)).denied);
  assert.ok((await run(discord('222222222222222222'), ADMIN_CLIENT)).denied);
});

test('admin gate: Discord id in a Google sub does not count', async () => {
  const u = google('someone@example.com');
  u.user_id = 'google-oauth2|111111111111111111';
  assert.ok((await run(u, ADMIN_CLIENT)).denied);
});

test('web app: admins get the roles claim, others nothing, nobody denied', async () => {
  const admin = await run(discord('111111111111111111'), WEB_CLIENT);
  assert.deepEqual(admin.access[`${NS}roles`], ['admin']);
  assert.deepEqual(admin.id[`${NS}roles`], ['admin']);
  const other = await run(google('someone@example.com'), WEB_CLIENT);
  assert.equal(other.denied, null);
  assert.deepEqual(other.access, {});
});

test('an email in the Discord id secret does not count', async () => {
  const calls = { denied: null };
  const api = { access: { deny: (r) => { calls.denied = r; } }, idToken: { setCustomClaim() {} }, accessToken: { setCustomClaim() {} } };
  await action.onExecutePostLogin({ user: google('admin@example.com'), client: { client_id: ADMIN_CLIENT }, secrets: { ALLOWED_DISCORD_IDS: 'admin@example.com' } }, api);
  assert.ok(calls.denied);
});

test('empty allow-list denies the gate', async () => {
  const calls = { denied: null };
  const api = { access: { deny: (r) => { calls.denied = r; } }, idToken: { setCustomClaim() {} }, accessToken: { setCustomClaim() {} } };
  await action.onExecutePostLogin({ user: google('admin@example.com'), client: { client_id: ADMIN_CLIENT }, secrets: {} }, api);
  assert.ok(calls.denied);
});
