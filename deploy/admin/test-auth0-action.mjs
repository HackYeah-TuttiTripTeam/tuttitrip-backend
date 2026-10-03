// Local test of auth0-post-login.js (the deployed Action, copied 1:1) with fake
// events: node --test deploy/admin/test-auth0-action.mjs
// Made-up identities only; the real allow-list never enters the repo.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const action = createRequire(import.meta.url)('./auth0-post-login.js');
const ADMIN_CLIENT = '7RvmbphKAtrd224ADzycUNki6SOAOyej';
const WEB_CLIENT = 'Q18lEcCNyP3Xut5wnuHYrGsoVf1pbKdl';
const NS = 'https://tuttitrip.gburek.app/';
const SECRETS = {
  ALLOWED_EMAILS: 'Admin@Example.com, other@example.com',
  ALLOWED_DISCORD_IDS: '111111111111111111, ',
  ALLOWED_USER_IDS: 'auth0|abc123, not-an-auth0-id',
};

const google = (email, verified = true) => ({
  connection: { name: 'google-oauth2', strategy: 'google-oauth2' },
  user: {
    user_id: 'google-oauth2|1', email, email_verified: verified,
    identities: [{ connection: 'google-oauth2', provider: 'google-oauth2', user_id: '1' }],
  },
});
const discord = (id, email, verified = false) => ({
  connection: { name: 'discord', strategy: 'oauth2' },
  user: {
    user_id: `oauth2|discord|${id}`, email, email_verified: verified,
    identities: [{ connection: 'discord', provider: 'oauth2', user_id: `discord|${id}` }],
  },
});
const database = (userId, username, email) => ({
  connection: { name: 'Username-Password-Authentication', strategy: 'auth0' },
  user: {
    user_id: userId, username, email, email_verified: false,
    identities: [{ connection: 'Username-Password-Authentication', provider: 'auth0', user_id: userId.slice(6) }],
  },
});

async function run(login, clientId, secrets = SECRETS) {
  const calls = { denied: null, id: {}, access: {} };
  const api = {
    access: { deny: (reason) => { calls.denied = reason; } },
    idToken: { setCustomClaim: (k, v) => { calls.id[k] = v; } },
    accessToken: { setCustomClaim: (k, v) => { calls.access[k] = v; } },
  };
  await action.onExecutePostLogin({ ...login, client: { client_id: clientId }, secrets }, api);
  return calls;
}

test('admin gate: listed Google email gets in with claims', async () => {
  const c = await run(google('admin@example.com'), ADMIN_CLIENT);
  assert.equal(c.denied, null);
  assert.deepEqual(c.id[`${NS}admin_ids`], ['admin@example.com']);
  assert.equal(c.id[`${NS}admin_email`], 'admin@example.com');
});

test('admin gate: unverified Google email is denied', async () => {
  assert.ok((await run(google('admin@example.com', false), ADMIN_CLIENT)).denied);
});

test('admin gate: a listed email via Discord does not count, even verified', async () => {
  const c = await run(discord('222222222222222222', 'admin@example.com', true), ADMIN_CLIENT);
  assert.ok(c.denied);
});

test('admin gate: listed Discord id gets in with a synthetic email', async () => {
  const c = await run(discord('111111111111111111', 'x@example.com'), ADMIN_CLIENT);
  assert.equal(c.denied, null);
  assert.deepEqual(c.id[`${NS}admin_ids`], ['111111111111111111']);
  assert.equal(c.id[`${NS}admin_email`], 'discord-111111111111111111@users.tuttitrip.invalid');
});

test('admin gate: listed database account gets a username-based email', async () => {
  const c = await run(database('auth0|abc123', 'Test Super', 't@example.com'), ADMIN_CLIENT);
  assert.equal(c.denied, null);
  assert.deepEqual(c.id[`${NS}admin_ids`], ['auth0|abc123']);
  assert.equal(c.id[`${NS}admin_email`], 'test-super@users.tuttitrip.invalid');
});

test('admin gate: strangers are denied', async () => {
  assert.ok((await run(google('someone@example.com'), ADMIN_CLIENT)).denied);
  assert.ok((await run(discord('333333333333333333'), ADMIN_CLIENT)).denied);
  assert.ok((await run(database('auth0|zzz', 'test-user'), ADMIN_CLIENT)).denied);
});

test('admin gate: a Discord-looking id inside a Google sub does not count', async () => {
  const login = google('someone@example.com');
  login.user.user_id = 'google-oauth2|111111111111111111';
  assert.ok((await run(login, ADMIN_CLIENT)).denied);
});

test('secrets are type-checked: ids only in their own lists', async () => {
  const secrets = { ALLOWED_DISCORD_IDS: 'admin@example.com', ALLOWED_USER_IDS: 'admin@example.com' };
  assert.ok((await run(google('admin@example.com'), ADMIN_CLIENT, secrets)).denied);
});

test('web app: admins get the roles claim, others nothing, nobody denied', async () => {
  for (const login of [google('admin@example.com'), discord('111111111111111111'), database('auth0|abc123', 'ts')]) {
    const c = await run(login, WEB_CLIENT);
    assert.equal(c.denied, null);
    assert.deepEqual(c.access[`${NS}roles`], ['admin']);
    assert.deepEqual(c.id[`${NS}roles`], ['admin']);
  }
  const other = await run(google('someone@example.com'), WEB_CLIENT);
  assert.equal(other.denied, null);
  assert.deepEqual(other.access, {});
});

test('empty secrets deny the gate', async () => {
  assert.ok((await run(google('admin@example.com'), ADMIN_CLIENT, {})).denied);
});
