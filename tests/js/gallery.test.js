import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  SMALL_MODEL, CONFIDENT, GalleryError, serialize, allNames, addPhoto, deletePhoto, setCover, saveEdits, badges, roman,
} from '../../admin/lib/gallery.js';

const ROOT = new URL('../../', import.meta.url); // repo root, whatever the working directory
const fixtureText = (name) => readFileSync(new URL(`tests/fixtures/${name}`, ROOT), 'utf8');
const site = () => JSON.parse(fixtureText('site.json'));

test('constants', () => {
  assert.equal(SMALL_MODEL, 'gemma4:e4b-it-qat');
  assert.equal(CONFIDENT, 0.8);
  assert.ok(new GalleryError('x') instanceof Error);
  assert.equal(new GalleryError('x').name, 'GalleryError');
});

// --- serialize ------------------------------------------------------------------------------

test('serialize matches Python', () => assert.equal(serialize(site()), fixtureText('site.canonical.json')));

test('serialize is stable: canonical text parses and serializes to itself', () => {
  const canonical = fixtureText('site.canonical.json');
  assert.equal(serialize(JSON.parse(canonical)), canonical);
});

test('serialize layout rules', () => {
  const data = {
    site: {},
    list: [],
    nested: { a: [1, { b: null }], c: {} },
    rooms: [{ id: 'r', photos: [] }, { id: 's', photos: [{ file: 'x', ai: { model: 'm', fields: [], confidence: 0.5 } }] }],
    other: [{ photos: [{ file: 'y' }] }], // "photos" outside rooms/*/ is laid out like everything else
  };
  assert.equal(serialize(data), [
    '{',
    '  "site": {},',
    '  "list": [],',
    '  "nested": {',
    '    "a": [',
    '      1,',
    '      {',
    '        "b": null',
    '      }',
    '    ],',
    '    "c": {}',
    '  },',
    '  "rooms": [',
    '    {',
    '      "id": "r",',
    '      "photos": []',
    '    },',
    '    {',
    '      "id": "s",',
    '      "photos": [',
    '        {"file":"x","ai":{"model":"m","fields":[],"confidence":0.5}}',
    '      ]',
    '    }',
    '  ],',
    '  "other": [',
    '    {',
    '      "photos": [',
    '        {',
    '          "file": "y"',
    '        }',
    '      ]',
    '    }',
    '  ]',
    '}',
    '',
  ].join('\n'));
});

// Expected text comes from the real Python scripts/photolib.py dumps_data, run on the same JSON.
const PY_SCRIPT = [
  'import sys, json',
  'sys.path.insert(0, sys.argv[1])',
  'import photolib',
  "data = json.load(open(sys.argv[2], encoding='utf-8'))",
  "sys.stdout.buffer.write(photolib.dumps_data(data).encode('utf-8'))",
].join('; ');
const SCRIPTS_DIR = fileURLToPath(new URL('scripts', ROOT));
const hasPython = (() => {
  const r = spawnSync('py', ['--version'], { encoding: 'utf8' });
  return !r.error && r.status === 0;
})();

function pythonDumps(data) {
  const dir = mkdtempSync(join(tmpdir(), 'nocturne-serialize-'));
  try {
    const file = join(dir, 'data.json');
    writeFileSync(file, JSON.stringify(data), 'utf8');
    const r = spawnSync('py', ['-c', PY_SCRIPT, SCRIPTS_DIR, file], { encoding: 'buffer' });
    assert.equal(r.status, 0, `py failed: ${r.error ?? r.stderr.toString('utf8')}`);
    return r.stdout.toString('utf8');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('serialize matches Python on tricky data', { skip: hasPython ? false : 'py is not available' }, () => {
  const tricky = {
    'empty site and rooms': { site: {}, cover: 'q', rooms: [] },
    'empty containers': { site: {}, rooms: [{ id: 'a', name: '방', photos: [] }], list: [], obj: {}, deep: { a: [[], {}, [[]]] } },
    'quotes backslash newline tab control emoji': {
      site: { title: 'He said "hi" \\ back', note: '줄\n바꿈\t탭\r끝 \u0001\u001f \u007f  ' },
      cover: 'x',
      rooms: [{
        id: 'a', name: '강', note: '따옴표 " 와 \\ 와 😀',
        photos: [
          { file: 'x', title: '따옴표 " 와 \\ 와 \n 와 \t 와 \u0001 와 😀 와 한글', alt: '', date: null },
          { file: 'y', alt: 'a/b </script> & é', ai: { model: 'gemma4:e4b-it-qat', fields: ['title', 'alt'], confidence: 0.61 } },
          { file: 'z', ai: { model: 'm', fields: [], confidence: 0 }, extra: { list: [1, 2.5, { b: null }], empty: [], none: {} } },
        ],
      }, { id: 'b', name: '밤', photos: [] }],
    },
    'numbers and literals': { n: null, t: true, f: false, int: 3, neg: -2, half: 0.5, ratio: 0.625, third: 0.333, big: 1234567890 },
    'photos path only under rooms/*/': {
      rooms: [[{ a: 1 }], { photos: [{ file: 'x' }] }],
      other: [{ photos: [{ file: 'y' }] }],
      photos: [{ file: 'z' }],
    },
    'top level array': [1, { rooms: [{ photos: [{ file: 'x' }] }] }, []],
    'top level empty object': {},
    'top level empty array': [],
    'top level string': '한글 "q"',
  };
  for (const [label, data] of Object.entries(tricky)) {
    assert.equal(serialize(data), pythonDumps(data), label);
  }
});

// --- allNames, addPhoto, deletePhoto, setCover ----------------------------------------------

test('allNames', () => assert.deepEqual(allNames(site()), new Set(['a1', 'a2', 'n1'])));

test('delete, cover, add', () => {
  const d = site();
  assert.throws(() => deletePhoto(d, 'a1'), /대표 사진은/);
  setCover(d, 'n1'); assert.equal(deletePhoto(d, 'a1').file, 'a1');
  addPhoto(d, 'night', {file: 'n2', title: 't', alt: 'a'});
  assert.throws(() => addPhoto(d, 'moon', {file: 'x', title: 't', alt: 'a'}), GalleryError);
  assert.throws(() => addPhoto(d, 'night', {file: 'n1', title: 't', alt: 'a'}), GalleryError);
});

test('deletePhoto removes the photo from its room and returns the entry', () => {
  const d = site();
  const gone = deletePhoto(d, 'a2');
  assert.deepEqual(d.rooms[0].photos.map((p) => p.file), ['a1']);
  assert.equal(gone.title, '다리 아래');
  assert.equal(d.cover, 'a1');
});

test('deletePhoto and setCover errors', () => {
  const d = site();
  const before = serialize(d);
  assert.throws(() => deletePhoto(d, 'a1'), new GalleryError('대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요'));
  assert.throws(() => deletePhoto(d, 'zzz'), new GalleryError('없는 사진이에요: zzz'));
  assert.throws(() => setCover(d, 'zzz'), new GalleryError('없는 사진이에요: zzz'));
  assert.equal(serialize(d), before);
  setCover(d, 'n1');
  assert.equal(d.cover, 'n1');
});

test('addPhoto appends to the end of the room; the room is checked before the name', () => {
  const d = site();
  const entry = { file: 'n2', title: 't', alt: 'a' };
  addPhoto(d, 'night', entry);
  assert.equal(d.rooms[1].photos.at(-1), entry);
  assert.throws(() => addPhoto(d, 'moon', { file: 'n1', title: 't', alt: 'a' }), new GalleryError('없는 방이에요: moon'));
  assert.throws(() => addPhoto(d, 'river', { file: 'n1', title: 't', alt: 'a' }), new GalleryError('이미 있는 사진 이름이에요: n1'));
  assert.equal(d.rooms[0].photos.length, 2);
});

// --- saveEdits ------------------------------------------------------------------------------

test('saveEdits drops edited fields', () => {
  const d = site();
  saveEdits(d, 'a2', {title: '내가 고침', alt: '다리 상판'});          // alt는 그대로
  const p = d.rooms[0].photos[1];
  assert.deepEqual(p.ai, {model: 'gemma4:e4b-it-qat', fields: ['alt']});
  saveEdits(d, 'a2', {alt: '새 설명'});
  assert.equal(p.ai, undefined);
});

test('saveEdits moves room', () => { const d = site(); saveEdits(d, 'a2', {room: 'night'}); assert.equal(d.rooms[1].photos.at(-1).file, 'a2'); });

test('saveEdits moves the same object to the end of the new room and keeps the rest', () => {
  const d = site();
  const p = d.rooms[0].photos[1];
  saveEdits(d, 'a2', { room: 'night', title: '새 제목' });
  assert.equal(d.rooms[1].photos.at(-1), p);
  assert.deepEqual(d.rooms[0].photos.map((x) => x.file), ['a1']);
  assert.deepEqual(d.rooms[1].photos.map((x) => x.file), ['n1', 'a2']);
  assert.equal(p.title, '새 제목');
  assert.deepEqual(p.ai.fields, ['alt']);
});

test('saveEdits to the same room does not move the photo', () => {
  const d = site();
  saveEdits(d, 'a1', { room: 'river' });
  assert.deepEqual(d.rooms[0].photos.map((x) => x.file), ['a1', 'a2']);
});

test('saveEdits with no change still drops the confidence ("seen by a person")', () => {
  const d = site();
  saveEdits(d, 'a2', {});
  assert.deepEqual(d.rooms[0].photos[1].ai, { model: SMALL_MODEL, fields: ['title', 'alt'] });
  const e = site();
  saveEdits(e, 'a2', { title: '다리 아래', alt: '다리 상판' }); // same values: not edited
  assert.deepEqual(e.rooms[0].photos[1].ai, { model: SMALL_MODEL, fields: ['title', 'alt'] });
});

test('saveEdits keeps untouched key order and leaves photos without ai alone', () => {
  const d = site();
  saveEdits(d, 'n1', { title: '전화', alt: '부스', room: 'night' });
  assert.deepEqual(Object.keys(d.rooms[1].photos[0]), ['file', 'title', 'alt', 'date']);
  assert.deepEqual(d.rooms[1].photos[0], { file: 'n1', title: '전화', alt: '부스', date: '2025.11.30' });
});

test('saveEdits removes ai when fields end up empty, even without a confidence', () => {
  const d = site();
  delete d.rooms[0].photos[1].ai.confidence;
  saveEdits(d, 'a2', { title: 't', alt: 'a' });
  assert.equal('ai' in d.rooms[0].photos[1], false);
});

test('saveEdits refuses what it cannot do and then changes nothing', () => {
  const d = site();
  const before = serialize(d);
  assert.throws(() => saveEdits(d, 'zzz', { title: 'x' }), new GalleryError('없는 사진이에요: zzz'));
  assert.throws(() => saveEdits(d, 'a2', { title: 'x', file: 'b' }), new GalleryError('고칠 수 없는 항목이에요: file'));
  assert.throws(() => saveEdits(d, 'a2', { ai: null }), new GalleryError('고칠 수 없는 항목이에요: ai'));
  assert.throws(() => saveEdits(d, 'a2', { title: 'x', alt: '   ' }), new GalleryError('제목과 설명을 채워 주세요'));
  assert.throws(() => saveEdits(d, 'a2', { title: '' }), new GalleryError('제목과 설명을 채워 주세요'));
  assert.throws(() => saveEdits(d, 'a2', { title: 'x', room: 'moon' }), new GalleryError('없는 방이에요: moon'));
  assert.equal(serialize(d), before);
});

// --- badges ---------------------------------------------------------------------------------

test('badges', () => {
  assert.deepEqual(badges(site().rooms[0].photos[1]), ['확인 필요', 'AI 설명 · 다듬기 대기']);
  assert.deepEqual(badges({ai: {model: 'gemma4:12b-it-qat', fields: ['alt']}}), ['AI 설명']);
  assert.deepEqual(badges({}), []);
});

test('badges edge cases', () => {
  const ai = (extra) => ({ ai: { model: 'gemma4:12b-it-qat', fields: ['title'], ...extra } });
  assert.deepEqual(badges(ai({ confidence: 0.79 })), ['확인 필요', 'AI 설명']);
  assert.deepEqual(badges(ai({ confidence: 0.8 })), ['AI 설명']);
  assert.deepEqual(badges(ai({ confidence: 0 })), ['확인 필요', 'AI 설명']);
  assert.deepEqual(badges(ai({ confidence: 1 })), ['AI 설명']);
  assert.deepEqual(badges(ai({ fields: [] })), []);
  assert.deepEqual(badges({ ai: { model: SMALL_MODEL, fields: [], confidence: 0.2 } }), ['확인 필요']);
  assert.deepEqual(badges({ ai: { model: SMALL_MODEL, fields: ['title'] } }), ['AI 설명 · 다듬기 대기']);
  assert.deepEqual(badges(site().rooms[0].photos[0]), []);
});

test('roman numbers rooms as the site does', () => {
  assert.deepEqual([1, 2, 3, 4, 9, 14, 40, 1994].map(roman), ['I', 'II', 'III', 'IV', 'IX', 'XIV', 'XL', 'MCMXCIV']);
});
