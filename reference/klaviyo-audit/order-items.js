// Persist product titles only. Shopify order events can expose empty ItemNames
// alongside populated $extra.line_items, or encode Items with numeric object keys.
// Never serialize whole line items: they can contain customer/custom properties.
function asItemList(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];
  const keys = Object.keys(value);
  if (!keys.length || !keys.every((key) => /^(0|[1-9]\d*)$/.test(key))) return [];
  return keys.sort((a, b) => Number(a) - Number(b)).map((key) => value[key]);
}

function productTitle(item) {
  const clean = (value) => typeof value === 'string' && value.trim() ? value.trim() : null;
  if (typeof item === 'string') return clean(item);
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
  for (const field of ['ProductName', 'Name', 'name', 'title', 'Title', 'product_title']) {
    const title = clean(item[field]);
    if (title) return title;
  }
  return null;
}

function extractOrderItemNames(properties) {
  if (!properties || typeof properties !== 'object') return [];
  const sources = [properties.ItemNames, properties.Items, properties.$extra?.line_items, properties.line_items];
  for (const source of sources) {
    const titles = asItemList(source).map(productTitle).filter(Boolean);
    if (titles.length) return titles;
  }
  return [];
}

module.exports = { extractOrderItemNames };
