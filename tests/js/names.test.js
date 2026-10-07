import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nameFor, uniqueName } from '../../admin/lib/names.js';
import { loadCases } from './util.js';

test('cases', () => { for (const [given, want] of loadCases().names) assert.equal(nameFor(given), want, given); });

// Expected values below come from scripts/photolib.py name_for (Python 3.14).
test('odd names match Python', () => {
  const cases = [
    ['', 'photo'], ['.jpg', 'jpg'], ['..jpg', 'jpg'], ['.hidden', 'hidden'], ['a.', 'a'], ['a..b', 'a'],
    ['a.b.c', 'a_b'], ['x .jpg', 'x'], ['-a-.jpg', 'a'], ['___.jpg', 'photo'], ['IMG_.jpg', 'photo'],
    ['IMG_IMG_5.jpg', 'img_5'], ['img_1_EDITED.jpg', '1_edited'], ['IMG_1_edited_edited.JPG', '1'],
    ['IMG_Edited.jpg', 'img'], ['Photo (2) copy.JPG', 'photo_2_copy'], ['ÀB.jpg', 'b'],
    ['dir/IMG_0002.png', '0002'], ['C:\\pics\\IMG_0001.jpg', '0001'],
  ];
  for (const [given, want] of cases) assert.equal(nameFor(given), want, JSON.stringify(given));
});

test('uniqueName', () => {
  assert.equal(uniqueName('image', new Set()), 'image');
  assert.equal(uniqueName('image', new Set(['image', 'image-2'])), 'image-3');
});

test('uniqueName keeps the redraft- prefix for redraft requests: a picked photo never takes it', () => {
  assert.equal(uniqueName('redraft-a1', new Set()), 'photo-redraft-a1');
  assert.equal(uniqueName('redraft-a1', new Set(['photo-redraft-a1'])), 'photo-redraft-a1-2');
  assert.equal(uniqueName(nameFor('Redraft-A1.JPG'), new Set()), 'photo-redraft-a1');
  assert.equal(uniqueName('redraft', new Set()), 'redraft'); // only the prefix with its dash is reserved
  assert.equal(uniqueName('redrafts-1', new Set()), 'redrafts-1');
  assert.equal(uniqueName('my-redraft-1', new Set()), 'my-redraft-1');
});

test('uniqueName takes the smallest free number, not the next after the largest', () => {
  assert.equal(uniqueName('image', new Set(['image', 'image-3'])), 'image-2');
  assert.equal(uniqueName('image', new Set(['image-2'])), 'image');
});
