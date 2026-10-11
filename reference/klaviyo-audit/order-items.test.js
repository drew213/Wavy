const assert = require('node:assert/strict');
const test = require('node:test');
const { extractOrderItemNames } = require('./order-items');

test('empty upper-level arrays do not mask Shopify $extra.line_items', () => {
  const properties = {
    ItemNames: [], Items: [],
    Name: 'CUSTOMER NAME MUST NOT BE COPIED',
    $extra: {
      email: 'customer@example.invalid',
      shipping_address: { name: 'CUSTOMER NAME MUST NOT BE COPIED' },
      line_items: [
        { title: 'D-Bloat', sku: 'DB01', properties: { email: 'customer@example.invalid' } },
        { name: 'B-Clear', quantity: 2, customer: { name: 'CUSTOMER NAME MUST NOT BE COPIED' } },
      ],
    },
  };
  assert.deepEqual(extractOrderItemNames(properties), ['D-Bloat', 'B-Clear']);
});

test('numeric Items objects preserve product order and only extract whitelisted titles', () => {
  const properties = { Items: {
    10: { ProductName: 'Third', email: 'customer@example.invalid' },
    2: { Name: 'Second', address: { name: 'PRIVATE' } },
    0: { title: 'First', properties: [{ name: 'PRIVATE' }] },
  } };
  assert.deepEqual(extractOrderItemNames(properties), ['First', 'Second', 'Third']);
});

test('invalid names and arbitrary objects cannot become serialized product names', () => {
  assert.deepEqual(extractOrderItemNames({
    ItemNames: [{ customer: { name: 'PRIVATE' } }],
    Items: { customer: { Name: 'PRIVATE' } },
    $extra: { line_items: [
      { title: { name: 'PRIVATE' } },
      { name: ['PRIVATE'] },
      { email: 'customer@example.invalid' },
      { title: '  ', name: null },
      { title: 'Valid product', customer: { name: 'PRIVATE' } },
    ] },
  }), ['Valid product']);
  assert.deepEqual(extractOrderItemNames({ Name: 'PRIVATE', email: 'customer@example.invalid' }), []);
});

test('existing ItemNames and Items array shapes remain supported without duplication', () => {
  assert.deepEqual(extractOrderItemNames({ ItemNames: [' D-Bloat '], Items: [{ Name: 'D-Bloat' }] }), ['D-Bloat']);
  assert.deepEqual(extractOrderItemNames({ Items: [{ ProductName: 'One' }, { Name: 'Two' }, { name: 'Three' }] }), ['One', 'Two', 'Three']);
  assert.deepEqual(extractOrderItemNames({ Items: {}, $extra: { line_items: [{ title: 'Fallback' }] } }), ['Fallback']);
  assert.deepEqual(extractOrderItemNames(null), []);
});
