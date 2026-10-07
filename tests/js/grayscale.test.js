import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chromaOfRGBA, grayscaleFromChroma } from '../../admin/lib/grayscale.js';
import { expandGrayCase, loadCases } from './util.js';

test('cases', () => { for (const c of loadCases().grayscale) assert.equal(grayscaleFromChroma(expandGrayCase(c)), c.expect, JSON.stringify(c)); });

test('rgba', () => assert.deepEqual(chromaOfRGBA(new Uint8ClampedArray([10, 20, 30, 255, 5, 5, 5, 255])), [20, 0]));

test('no pixels is not grayscale', () => assert.equal(grayscaleFromChroma([]), false));

test('accepts a typed array and does not reorder it', () => {
  const values = Uint8Array.from([9, 0, 0, 0]);
  assert.equal(grayscaleFromChroma(values), true);
  assert.deepEqual([...values], [9, 0, 0, 0]);
});
