// Metric Aggregate timezone controls buckets, not the datetime filter instants.
// Convert account-local calendar midnights to UTC; preserve explicit instants.
const formatters = new Map();

function formatter(timezone) {
  if (!formatters.has(timezone)) formatters.set(timezone, new Intl.DateTimeFormat('en-CA-u-ca-iso8601-nu-latn', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset',
  }));
  return formatters.get(timezone);
}

function aggregateBoundaryToUtc(value, timezone) {
  if (value instanceof Date) return value.toISOString();
  if (typeof value !== 'string') throw new Error('Aggregate boundary must be a calendar midnight or an explicit timestamp');
  // NOW and already-zoned boundaries denote actual instants, never local wall time.
  if (/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) {
    const instant = new Date(value);
    if (!Number.isFinite(instant.getTime())) throw new Error('Invalid explicit aggregate timestamp');
    return instant.toISOString();
  }
  const match = /^(\d{4}-\d{2}-\d{2})(?:T00:00:00(?:\.0{1,3})?)?$/.exec(value);
  if (!match) throw new Error('Naive aggregate timestamps must be calendar midnight; retain Z on actual UTC timestamps');
  const date = match[1], anchor = Date.parse(`${date}T00:00:00Z`);
  if (!Number.isFinite(anchor) || new Date(anchor).toISOString().slice(0, 10) !== date) throw new Error('Invalid aggregate calendar date');
  const format = formatter(timezone);
  const partsAt = instant => Object.fromEntries(format.formatToParts(new Date(instant)).map(p => [p.type, p.value]));
  // Examine surrounding offsets rather than assuming the UTC date has the same
  // offset as local midnight. Validate candidates; reject missing/ambiguous dates.
  const offsets = new Set();
  for (const days of [-2, -1, 0, 1, 2]) {
    const name = partsAt(anchor + days * 86400000).timeZoneName;
    if (name === 'GMT') { offsets.add(0); continue; }
    const offset = /^GMT([+-])(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(name);
    if (!offset) throw new Error('Unsupported timezone offset');
    offsets.add((offset[1] === '+' ? 1 : -1) * (Number(offset[2]) * 3600 + Number(offset[3]) * 60 + Number(offset[4] || 0)) * 1000);
  }
  const candidates = [...offsets].map(offset => anchor - offset).filter(instant => {
    const p = partsAt(instant);
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}` === `${date}T00:00:00`;
  });
  if (candidates.length !== 1) throw new Error('Local midnight is missing or ambiguous; supply an explicit timestamp');
  return new Date(candidates[0]).toISOString();
}

function aggregateWindowUtc(window, timezone) {
  const start = aggregateBoundaryToUtc(window.start, timezone);
  const end = aggregateBoundaryToUtc(window.end, timezone);
  if (Date.parse(start) >= Date.parse(end)) throw new Error('Aggregate end must follow start');
  return { start, end };
}

module.exports = { aggregateBoundaryToUtc, aggregateWindowUtc };
