const assert = require('node:assert/strict');
const test = require('node:test');
const { aggregateBoundaryToUtc, aggregateWindowUtc } = require('./aggregate-boundaries');

test('account-local calendar boundaries use Eastern and Central summer/winter offsets', () => {
  for (const [zone, summer, winter] of [
    ['America/New_York', '04', '05'],
    ['America/Chicago', '05', '06'],
  ]) {
    assert.equal(aggregateBoundaryToUtc('2026-07-01T00:00:00', zone), `2026-07-01T${summer}:00:00.000Z`);
    assert.equal(aggregateBoundaryToUtc('2026-01-01T00:00:00', zone), `2026-01-01T${winter}:00:00.000Z`);
    const holiday = aggregateWindowUtc({ start: '2025-11-01T00:00:00', end: '2026-01-01T00:00:00' }, zone);
    assert.equal(holiday.start, `2025-11-01T${summer}:00:00.000Z`);
    assert.equal(holiday.end, `2026-01-01T${winter}:00:00.000Z`);
  }
});

test('local daily windows spanning DST have 23 or 25 hours', () => {
  for (const zone of ['America/New_York', 'America/Chicago']) {
    for (const [start, end, hours] of [['2026-03-08', '2026-03-09', 23], ['2026-11-01', '2026-11-02', 25]]) {
      const window = aggregateWindowUtc({ start, end }, zone);
      assert.equal((Date.parse(window.end) - Date.parse(window.start)) / 3600000, hours);
    }
  }
});

test('actual UTC NOW values and explicit offsets retain their instant', () => {
  for (const zone of ['America/New_York', 'America/Chicago']) {
    const now = '2026-10-02T22:04:45.178Z';
    assert.equal(aggregateBoundaryToUtc(now, zone), now);
    assert.equal(aggregateBoundaryToUtc(new Date(now), zone), now);
    assert.equal(aggregateBoundaryToUtc('2026-10-01T00:00:00+00:00', zone), '2026-10-01T00:00:00.000Z');
    const current = aggregateWindowUtc({ start: '2025-11-01T00:00:00', end: now }, zone);
    assert.equal(current.end, now);
  }
});

test('invalid, unzoned non-midnight, or unsupported local-midnight ambiguity fails explicitly', () => {
  assert.throws(() => aggregateBoundaryToUtc('2026-02-30T00:00:00', 'America/New_York'), /Invalid/);
  assert.throws(() => aggregateBoundaryToUtc('2026-10-02T22:04:45', 'America/Chicago'), /retain Z/);
  assert.throws(() => aggregateBoundaryToUtc('2011-12-30', 'Pacific/Apia'), /missing or ambiguous/);
  assert.throws(() => aggregateBoundaryToUtc('2026-01-01', 'Invalid/Timezone'), /time zone/i);
  assert.throws(() => aggregateWindowUtc({ start: '2026-01-02', end: '2026-01-01' }, 'UTC'), /end must follow/);
});
