import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { seal, unseal, VaultError, ITERATIONS, MIN_PASSPHRASE } from '../../admin/lib/vault.js';

const WRONG = '비밀번호가 맞지 않아요';
const SHORT = '잠금 비밀번호는 8자 이상이어야 해요';
const NO_TOKEN = '토큰을 넣어 주세요';
const UNSUPPORTED = '이 브라우저에서는 잠금을 쓸 수 없어요';

/** Passes assert.rejects only for our own VaultError carrying exactly `message` (no DOMException text). */
const vaultError = (message) => (e) => {
  assert.ok(e instanceof VaultError, `not a VaultError: ${e}`);
  assert.ok(e instanceof Error);
  assert.equal(e.name, 'VaultError');
  assert.equal(e.message, message);
  return true;
};

/** Flip one bit of one byte of a base64 string (Buffer is fine here: tests run on Node only). */
const flip = (b64, index = 0) => {
  const bytes = Buffer.from(b64, 'base64');
  bytes[index] ^= 1;
  return bytes.toString('base64');
};

// One sealed value shared by the tests that only need something to break.
const PASS = 'test-pass-1234';
const sealed = await seal('test-token', PASS);

test('roundtrip', async () => assert.equal(await unseal(await seal('test-token', 'test-pass-1234'), 'test-pass-1234'), 'test-token'));
test('wrong passphrase', async () => await assert.rejects(unseal(await seal('test-token', 'test-pass-1234'), 'wrong-pass-0000'), /비밀번호가 맞지 않아요/));
test('short passphrase', async () => await assert.rejects(seal('test-token', 'short'), /8자 이상/));
test('random salt and iv', async () => { const a = await seal('t', 'test-pass-1234'), b = await seal('t', 'test-pass-1234'); assert.notEqual(a.salt, b.salt); assert.notEqual(a.iv, b.iv); assert.equal(a.v, 1); });
test('no plaintext', async () => assert.equal(JSON.stringify(await seal('test-token', 'test-pass-1234')).includes('test-token'), false));

test('constants', () => {
  assert.equal(ITERATIONS, 310000);
  assert.equal(MIN_PASSPHRASE, 8);
});

test('sealed shape: v, 16-byte salt, 12-byte iv, ct = token + 16-byte tag, all base64', () => {
  assert.deepEqual(Object.keys(sealed).sort(), ['ct', 'iv', 'salt', 'v']);
  assert.equal(sealed.v, 1);
  for (const key of ['salt', 'iv', 'ct']) {
    assert.equal(typeof sealed[key], 'string');
    assert.match(sealed[key], /^[A-Za-z0-9+/]+={0,2}$/);
  }
  assert.equal(Buffer.from(sealed.salt, 'base64').length, 16);
  assert.equal(Buffer.from(sealed.iv, 'base64').length, 12);
  assert.equal(Buffer.from(sealed.ct, 'base64').length, 'test-token'.length + 16);
  assert.doesNotThrow(() => JSON.parse(JSON.stringify(sealed)));
});

test('sealing the same token twice gives different ct', async () => {
  const again = await seal('test-token', PASS);
  assert.notEqual(again.ct, sealed.ct);
  assert.notEqual(again.salt, sealed.salt);
  assert.notEqual(again.iv, sealed.iv);
  assert.equal(await unseal(again, PASS), 'test-token'); // and both still open
});

test('survives a JSON roundtrip (what localStorage does)', async () => {
  assert.equal(await unseal(JSON.parse(JSON.stringify(sealed)), PASS), 'test-token');
});

test('unicode token and passphrase', async () => {
  const token = 'github_pat_토큰_😀_ünï';
  const pass = '비밀번호-열쇠-😀😀';
  assert.equal(await unseal(await seal(token, pass), pass), token);
});

test('passphrase length counts Unicode code points, minimum 8', async () => {
  for (const pass of ['1234567', '😀'.repeat(7), '가나다라마바사', '']) {
    await assert.rejects(seal('t', pass), vaultError(SHORT), JSON.stringify(pass));
  }
  // 8 code points are enough, even though 8 emoji are 16 UTF-16 units and 8 Hangul syllables are 8.
  const emoji = '😀'.repeat(8);
  assert.equal(await unseal(await seal('t', emoji), emoji), 't');
});

test('non-string passphrase is rejected as too short', async () => {
  for (const pass of [undefined, null, 12345678, ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']]) {
    await assert.rejects(seal('t', pass), vaultError(SHORT));
  }
});

test('the passphrase is checked before any crypto', async () => {
  const importKey = mock.method(globalThis.crypto.subtle, 'importKey');
  const getRandomValues = mock.method(globalThis.crypto, 'getRandomValues');
  try {
    await assert.rejects(seal('test-token', 'short'), vaultError(SHORT));
    assert.equal(importKey.mock.callCount(), 0);
    assert.equal(getRandomValues.mock.callCount(), 0);
  } finally {
    importKey.mock.restore();
    getRandomValues.mock.restore();
  }
});

test('wrong passphrase raises VaultError without DOMException text', async () => {
  await assert.rejects(unseal(sealed, 'wrong-pass-0000'), vaultError(WRONG));
  await assert.rejects(unseal(sealed, 'short'), vaultError(WRONG)); // too short can never be right
  await assert.rejects(unseal(sealed, undefined), vaultError(WRONG));
});

test('tampered ct, iv or salt', async () => {
  const lastCtByte = Buffer.from(sealed.ct, 'base64').length - 1; // inside the GCM tag
  const tampered = {
    'ct first byte': { ...sealed, ct: flip(sealed.ct, 0) },
    'ct tag byte': { ...sealed, ct: flip(sealed.ct, lastCtByte) },
    iv: { ...sealed, iv: flip(sealed.iv) },
    salt: { ...sealed, salt: flip(sealed.salt) },
  };
  for (const [what, value] of Object.entries(tampered)) {
    await assert.rejects(unseal(value, PASS), vaultError(WRONG), what);
  }
});

test('wrong or missing version', async () => {
  for (const v of [0, 2, '1', 1.5, null, undefined, true]) {
    await assert.rejects(unseal({ ...sealed, v }, PASS), vaultError(WRONG), String(v));
  }
});

test('missing fields', async () => {
  for (const key of ['v', 'salt', 'iv', 'ct']) {
    const { [key]: _gone, ...rest } = sealed;
    await assert.rejects(unseal(rest, PASS), vaultError(WRONG), `without ${key}`);
  }
});

test('non-base64 or mistyped fields', async () => {
  const bad = ['!!!!', 'not base64 at all', 'abc', 'QQ', 'QQ=', '', 42, null, ['QQ=='], {}];
  for (const key of ['salt', 'iv', 'ct']) {
    for (const value of bad) {
      await assert.rejects(unseal({ ...sealed, [key]: value }, PASS), vaultError(WRONG), `${key}=${JSON.stringify(value)}`);
    }
  }
});

test('salt and iv of the wrong length, ct shorter than a tag', async () => {
  const b64 = (n) => Buffer.alloc(n, 7).toString('base64');
  await assert.rejects(unseal({ ...sealed, salt: b64(8) }, PASS), vaultError(WRONG));
  await assert.rejects(unseal({ ...sealed, salt: b64(32) }, PASS), vaultError(WRONG));
  await assert.rejects(unseal({ ...sealed, iv: b64(8) }, PASS), vaultError(WRONG));
  await assert.rejects(unseal({ ...sealed, iv: b64(16) }, PASS), vaultError(WRONG));
  await assert.rejects(unseal({ ...sealed, ct: b64(15) }, PASS), vaultError(WRONG));
  await assert.rejects(unseal({ ...sealed, ct: '' }, PASS), vaultError(WRONG));
});

test('garbage input', async () => {
  for (const garbage of [undefined, null, 'garbage', '{"v":1}', 42, true, [], [sealed], {}, { v: 1 }, () => sealed]) {
    await assert.rejects(unseal(garbage, PASS), vaultError(WRONG), String(garbage));
  }
});

// ---- Fix round 1: pinned crypto parameters, token and environment checks, NFC passphrases ----

test('interop: an independent WebCrypto derivation opens the sealed blob (PBKDF2-SHA256, 310000, AES-GCM-256)', async () => {
  const { subtle } = globalThis.crypto;
  const material = await subtle.importKey('raw', new TextEncoder().encode(PASS), 'PBKDF2', false, ['deriveKey']);
  const key = await subtle.deriveKey(
    { name: 'PBKDF2', salt: Buffer.from(sealed.salt, 'base64'), iterations: 310000, hash: 'SHA-256' },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['decrypt'],
  );
  const plain = await subtle.decrypt({ name: 'AES-GCM', iv: Buffer.from(sealed.iv, 'base64') }, key, Buffer.from(sealed.ct, 'base64'));
  assert.equal(new TextDecoder().decode(plain), 'test-token');
});

test('key derivation parameters: non-extractable keys, encrypt/decrypt only (seal and unseal)', async () => {
  const importKey = mock.method(globalThis.crypto.subtle, 'importKey');
  const deriveKey = mock.method(globalThis.crypto.subtle, 'deriveKey');
  try {
    const fresh = await seal('test-token', PASS);
    assert.equal(await unseal(fresh, PASS), 'test-token');
    assert.equal(importKey.mock.callCount(), 2);
    assert.equal(deriveKey.mock.callCount(), 2);
    for (const { arguments: [format, , algorithm, extractable, usages] } of importKey.mock.calls) {
      assert.equal(format, 'raw');
      assert.equal(algorithm, 'PBKDF2');
      assert.strictEqual(extractable, false);
      assert.deepEqual(usages, ['deriveKey']);
    }
    for (const { arguments: [params, , derivedType, extractable, usages] } of deriveKey.mock.calls) {
      assert.equal(params.name, 'PBKDF2');
      assert.equal(params.hash, 'SHA-256');
      assert.equal(params.iterations, 310000);
      assert.equal(params.salt.length, 16);
      assert.deepEqual(derivedType, { name: 'AES-GCM', length: 256 });
      assert.strictEqual(extractable, false);
      assert.deepEqual(usages, ['encrypt', 'decrypt']);
    }
  } finally {
    importKey.mock.restore();
    deriveKey.mock.restore();
  }
});

test('seal rejects a non-string or empty/whitespace-only token before any crypto', async () => {
  const importKey = mock.method(globalThis.crypto.subtle, 'importKey');
  const getRandomValues = mock.method(globalThis.crypto, 'getRandomValues');
  try {
    for (const token of [undefined, null, 12345, {}, ['x'], '', ' ', '   ', '\t\n', '　']) {
      await assert.rejects(seal(token, PASS), vaultError(NO_TOKEN), JSON.stringify(token));
    }
    assert.equal(importKey.mock.callCount(), 0);
    assert.equal(getRandomValues.mock.callCount(), 0);
  } finally {
    importKey.mock.restore();
    getRandomValues.mock.restore();
  }
});

test('without WebCrypto both seal and unseal say the browser cannot lock, not "wrong password"', async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  try {
    for (const stub of [undefined, null, {}]) {
      Object.defineProperty(globalThis, 'crypto', { value: stub, configurable: true, writable: true });
      await assert.rejects(seal('test-token', PASS), vaultError(UNSUPPORTED), String(stub));
      await assert.rejects(unseal(sealed, PASS), vaultError(UNSUPPORTED), String(stub));
    }
  } finally {
    Object.defineProperty(globalThis, 'crypto', original);
  }
  assert.equal(await unseal(sealed, PASS), 'test-token'); // restored and working again
});

test('only a failed GCM check means "wrong password"; other crypto failures are not disguised as one', async () => {
  const boom = new DOMException('boom', 'NotSupportedError');
  const notWrong = (e) => { assert.ok(!(e instanceof VaultError), `disguised: ${e}`); assert.equal(e, boom); return true; };
  for (const method of ['importKey', 'deriveKey', 'decrypt']) {
    const stub = mock.method(globalThis.crypto.subtle, method, async () => { throw boom; });
    try {
      await assert.rejects(unseal(sealed, PASS), notWrong, method);
    } finally {
      stub.mock.restore();
    }
  }
  const failed = mock.method(globalThis.crypto.subtle, 'decrypt', async () => { throw new DOMException('tag mismatch', 'OperationError'); });
  try {
    await assert.rejects(unseal(sealed, PASS), vaultError(WRONG));
  } finally {
    failed.mock.restore();
  }
});

test('passphrases are NFC-normalized: NFD and NFC forms are the same passphrase', async () => {
  const nfc = '가나다라마바사아';
  const nfd = nfc.normalize('NFD');
  assert.notEqual(nfc, nfd);
  assert.equal(await unseal(await seal('test-token', nfd), nfc), 'test-token'); // sealed with NFD, opened with NFC
  assert.equal(await unseal(await seal('test-token', nfc), nfd), 'test-token'); // and vice versa
  // the length check counts the normalized text: 7 syllables stay 7 even though their NFD form has 21 code points
  await assert.rejects(seal('t', '가나다라마바사'.normalize('NFD')), vaultError(SHORT));
});
