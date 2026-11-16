#!/usr/bin/env node
// Encrypts fleet.json into the FLEET_BLOB inside index.html.
//
// This repo is PUBLIC. The Fleet tab's data must only ever be committed as
// ciphertext. fleet.json and .fleet-password are gitignored and stay on the Mac.
//
//   node tools/fleet-encrypt.mjs            password from .fleet-password
//   FLEET_PASSWORD=... node tools/fleet-encrypt.mjs
//   node tools/fleet-encrypt.mjs --new-password   write a fresh random one first
//
// To change the password: put the new one on a single line in .fleet-password
// (or use --new-password) and run this again. The old password stops working.
import { readFileSync, writeFileSync, existsSync, chmodSync } from 'node:fs';
import { webcrypto as crypto } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const pwFile = join(root, '.fleet-password');
const dataFile = join(root, 'fleet.json');
const pageFile = join(root, 'index.html');
const ITER = 600000;
const START = '/*FLEET_BLOB_START*/', END = '/*FLEET_BLOB_END*/';
const b64 = u8 => Buffer.from(u8).toString('base64');

if (process.argv.includes('--new-password')) {
  // 20 characters from an unambiguous alphabet, in groups of 5: ~100 bits.
  const abc = 'abcdefghjkmnpqrstuvwxyz23456789';
  const pick = Array.from(crypto.getRandomValues(new Uint8Array(40)))
    .filter(b => b < 248).slice(0, 20).map(b => abc[b % abc.length]).join('');
  writeFileSync(pwFile, pick.match(/.{5}/g).join('-') + '\n', { mode: 0o600 });
  chmodSync(pwFile, 0o600);
  console.log('New password written to .fleet-password (not shown here).');
}

const password = (process.env.FLEET_PASSWORD ||
  (existsSync(pwFile) ? readFileSync(pwFile, 'utf8') : '')).trim();
if (password.length < 12) {
  console.error('No password of at least 12 characters. Put one in .fleet-password or run with --new-password.');
  process.exit(1);
}

const fleet = JSON.parse(readFileSync(dataFile, 'utf8'));
if (!fleet.asOf || !Array.isArray(fleet.stoves)) {
  console.error('fleet.json must look like { "asOf": "YYYY-MM-DD", "stoves": [ ... ] }');
  process.exit(1);
}

const salt = crypto.getRandomValues(new Uint8Array(16));
const iv = crypto.getRandomValues(new Uint8Array(12));
const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
const key = await crypto.subtle.deriveKey(
  { name: 'PBKDF2', salt, iterations: ITER, hash: 'SHA-256' }, base,
  { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
const ct = new Uint8Array(await crypto.subtle.encrypt(
  { name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(fleet))));

const blob = JSON.stringify({ v: 1, iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) });
const page = readFileSync(pageFile, 'utf8');
const a = page.indexOf(START), z = page.indexOf(END);
if (a < 0 || z < a || page.indexOf(START, a + 1) >= 0) {
  console.error('index.html must contain exactly one FLEET_BLOB_START … FLEET_BLOB_END pair.');
  process.exit(1);
}
writeFileSync(pageFile, page.slice(0, a + START.length) + blob + page.slice(z));
console.log(`Encrypted ${fleet.stoves.length} stoves (as of ${fleet.asOf}) into index.html.`);
