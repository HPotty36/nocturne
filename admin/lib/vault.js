// Passphrase-sealed token: PBKDF2-SHA256 -> AES-GCM-256 with WebCrypto only, so it runs unchanged in browsers and Node.
// A sealed token is {v: 1, salt, iv, ct}, all base64, and is safe to keep in localStorage.

// Sealed blobs do not store the iteration count, so changing ITERATIONS makes every existing blob undecryptable:
// bump the blob version `v` (and keep reading the old one with its old count) before touching this number.
export const ITERATIONS = 310000;
export const MIN_PASSPHRASE = 8;

const SALT_BYTES = 16;
const IV_BYTES = 12;
const TAG_BYTES = 16; // the GCM default tag (128 bits) is appended to ct
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const TOO_SHORT = '잠금 비밀번호는 8자 이상이어야 해요';
const NO_TOKEN = '토큰을 넣어 주세요';
const WRONG = '비밀번호가 맞지 않아요';
const UNSUPPORTED = '이 브라우저에서는 잠금을 쓸 수 없어요';

export class VaultError extends Error {
  constructor(message) {
    super(message);
    this.name = 'VaultError';
  }
}

function toBase64(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return globalThis.btoa(binary);
}

/** Strict base64 -> bytes; anything else is the same "wrong password" error as a failed decrypt. */
function fromBase64(text) {
  if (typeof text !== 'string' || text === '' || !BASE64.test(text)) throw new VaultError(WRONG);
  return Uint8Array.from(globalThis.atob(text), (ch) => ch.charCodeAt(0));
}

/** The WebCrypto subtle object, or a VaultError when this environment has none (not a wrong password). */
function requireSubtle() {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new VaultError(UNSUPPORTED);
  return subtle;
}

/** NFC, so a passphrase typed on a keyboard that composes Hangul differently is still the same passphrase. */
function normalizePassphrase(passphrase) {
  return passphrase.normalize('NFC');
}

async function deriveKey(subtle, passphrase, salt) {
  const material = await subtle.importKey('raw', new TextEncoder().encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: ITERATIONS, hash: 'SHA-256' },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

/**
 * Encrypts `token` under `passphrase`. Throws VaultError for a missing token, a passphrase shorter than 8
 * characters, or an environment without WebCrypto; all of that is checked before any crypto runs.
 */
export async function seal(token, passphrase) {
  const subtle = requireSubtle();
  if (typeof token !== 'string' || token.trim() === '') throw new VaultError(NO_TOKEN);
  if (typeof passphrase !== 'string') throw new VaultError(TOO_SHORT);
  const normalized = normalizePassphrase(passphrase);
  if ([...normalized].length < MIN_PASSPHRASE) throw new VaultError(TOO_SHORT);
  const salt = globalThis.crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const key = await deriveKey(subtle, normalized, salt);
  const ct = await subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(token));
  return { v: 1, salt: toBase64(salt), iv: toBase64(iv), ct: toBase64(new Uint8Array(ct)) };
}

/**
 * Decrypts a sealed token. A wrong passphrase or a damaged or malformed value is VaultError("비밀번호가 맞지 않아요").
 * An environment without WebCrypto is its own VaultError, and any other crypto failure is rethrown as is.
 */
export async function unseal(sealed, passphrase) {
  const subtle = requireSubtle();
  if (sealed === null || typeof sealed !== 'object' || sealed.v !== 1 || typeof passphrase !== 'string') {
    throw new VaultError(WRONG);
  }
  const salt = fromBase64(sealed.salt);
  const iv = fromBase64(sealed.iv);
  const ct = fromBase64(sealed.ct);
  if (salt.length !== SALT_BYTES || iv.length !== IV_BYTES || ct.length < TAG_BYTES) throw new VaultError(WRONG);
  try {
    const key = await deriveKey(subtle, normalizePassphrase(passphrase), salt);
    const plain = await subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
    return new TextDecoder().decode(plain);
  } catch (error) {
    // AES-GCM reports a failed tag check (wrong key, altered data) as OperationError; anything else is not about the password.
    if (error?.name === 'OperationError') throw new VaultError(WRONG);
    throw error;
  }
}
