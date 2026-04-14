import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getStock,
  getProductStock,
  getProducts,
  getLocations,
  getUnits,
  addStock,
  consumeStock,
  openStock,
  transferStock,
  deleteStockEntry,
  productImageUrl,
} from '../api';

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtDate(iso) {
  if (!iso) return '–';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function daysUntil(iso) {
  if (!iso) return Infinity;
  const diff = new Date(iso) - new Date(new Date().toISOString().slice(0, 10));
  return Math.ceil(diff / 86_400_000);
}

function bbClass(iso) {
  const d = daysUntil(iso);
  if (d < 0) return 'bg-red-500/20 text-red-400';
  if (d <= 3) return 'bg-orange-500/20 text-orange-400';
  if (d <= 7) return 'bg-yellow-500/20 text-yellow-400';
  return '';
}

function stockLevelClass(amount, min) {
  if (min > 0 && amount < min) return 'text-red-400';
  if (min > 0 && amount <= min * 1.25) return 'text-yellow-400';
  return 'text-green-400';
}

/* ── Modal shell ─────────────────────────────────────────────────────────── */

function Modal({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h3 className="font-semibold text-gray-100">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-xl leading-none">&times;</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

/* ── ProductCombobox ─────────────────────────────────────────────────────── */

function fuzzyMatch(query, name) {
  const q = query.toLowerCase();
  const n = name.toLowerCase();
  if (n.includes(q)) return true;
  // Allow characters to appear in order (fuzzy)
  let qi = 0;
  for (let i = 0; i < n.length && qi < q.length; i++) {
    if (n[i] === q[qi]) qi++;
  }
  return qi === q.length;
}

function fuzzyScore(query, name) {
  const q = query.toLowerCase();
  const n = name.toLowerCase();
  if (n.startsWith(q)) return 0;
  if (n.includes(q)) return 1;
  return 2;
}

// Combobox: text input + dropdown suggestions. Calls onChange(id) when a
// product is selected. products: [{id, name}]
function ProductCombobox({ products, value, onChange, placeholder = 'Search product…', required }) {
  const [inputVal, setInputVal] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const containerRef = useRef(null);
  const inputRef = useRef(null);

  // Sync display value when value is set externally (e.g. reset)
  useEffect(() => {
    if (!value) { setInputVal(''); return; }
    const p = products.find((p) => String(p.id) === String(value));
    if (p) setInputVal(p.name);
  }, [value, products]);

  const suggestions = inputVal.length === 0
    ? products.slice(0, 8)
    : products
        .filter((p) => fuzzyMatch(inputVal, p.name))
        .sort((a, b) => fuzzyScore(inputVal, a.name) - fuzzyScore(inputVal, b.name))
        .slice(0, 8);

  const select = (p) => {
    setInputVal(p.name);
    onChange(String(p.id));
    setOpen(false);
  };

  const handleInput = (e) => {
    setInputVal(e.target.value);
    onChange(''); // clear selection when user edits
    setActiveIdx(0);
    setOpen(true);
  };

  const handleKeyDown = (e) => {
    if (!open) { if (e.key === 'ArrowDown' || e.key === 'Enter') setOpen(true); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => Math.min(i + 1, suggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (suggestions[activeIdx]) select(suggestions[activeIdx]); }
    else if (e.key === 'Escape') setOpen(false);
  };

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        value={inputVal}
        onChange={handleInput}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        required={required}
        autoComplete="off"
        className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
      />
      {/* Hidden input to hold the actual ID for form required validation */}
      <input type="hidden" value={value} required={required} />
      {open && suggestions.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full bg-gray-700 border border-gray-600 rounded-lg shadow-xl max-h-52 overflow-y-auto text-sm">
          {suggestions.map((p, i) => (
            <li
              key={p.id}
              onMouseDown={(e) => { e.preventDefault(); select(p); }}
              className={`px-3 py-2 cursor-pointer truncate ${
                i === activeIdx ? 'bg-emerald-600 text-white' : 'text-gray-100 hover:bg-gray-600'
              }`}
            >
              {p.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ── Main component ──────────────────────────────────────────────────────── */

export default function Stock() {
  const [stock, setStock] = useState([]);
  const [units, setUnits] = useState([]);
  const [locations, setLocations] = useState([]);
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Modal state
  const [modal, setModal] = useState(null); // {type, productId, productName}
  const [formAmt, setFormAmt] = useState(1);
  const [formLoc, setFormLoc] = useState('');
  const [formBB, setFormBB] = useState('');
  const [formToLoc, setFormToLoc] = useState('');
  const [formFromLoc, setFormFromLoc] = useState('');
  const [formProduct, setFormProduct] = useState('');
  const [submitting, setSubmitting] = useState(false);

  /* ── Data loading ──────────────────────────────────────────────────────── */

  const reload = useCallback(async () => {
    try {
      const [sRes, uRes, lRes, pRes] = await Promise.all([
        getStock(), getUnits(), getLocations(), getProducts(),
      ]);
      setStock(sRes.data);
      setUnits(uRes.data);
      setLocations(lRes.data);
      setProducts(pRes.data);
      setError('');
    } catch (e) {
      setError('Failed to load stock data');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const unitMap = Object.fromEntries(units.map((u) => [u.id, u]));
  const locationMap = Object.fromEntries(locations.map((l) => [l.id, l]));

  /* ── Detail expansion ──────────────────────────────────────────────────── */

  const toggleExpand = async (pid) => {
    if (expanded === pid) { setExpanded(null); return; }
    setExpanded(pid);
    try {
      const { data } = await getProductStock(pid);
      setEntries(data);
    } catch { setEntries([]); }
  };

  const handleDeleteEntry = async (id) => {
    if (!confirm('Delete this stock entry?')) return;
    try {
      await deleteStockEntry(id);
      setEntries((prev) => prev.filter((e) => e.id !== id));
      await reload();
    } catch (e) {
      console.error(e);
    }
  };

  /* ── Modal helpers ─────────────────────────────────────────────────────── */

  const openModal = (type, pid, pname) => {
    setModal({ type, productId: pid, productName: pname });
    setFormAmt(1);
    setFormLoc('');
    setFormBB('');
    setFormFromLoc('');
    setFormToLoc('');
    setFormProduct(pid || '');
  };
  const closeModal = () => setModal(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const pid = modal.productId || formProduct;
      switch (modal.type) {
        case 'add': {
          const payload = { product_id: Number(pid), amount: Number(formAmt) };
          if (formLoc) payload.location_id = Number(formLoc);
          if (formBB) payload.best_before_date = formBB;
          await addStock(payload);
          break;
        }
        case 'consume':
          await consumeStock({ product_id: Number(pid), amount: Number(formAmt) });
          break;
        case 'open':
          await openStock({ product_id: Number(pid), amount: Number(formAmt) });
          break;
        case 'transfer':
          await transferStock({
            product_id: Number(pid),
            amount: Number(formAmt),
            from_location_id: Number(formFromLoc),
            to_location_id: Number(formToLoc),
          });
          break;
      }
      closeModal();
      await reload();
      if (expanded) {
        try { const { data } = await getProductStock(expanded); setEntries(data); } catch {}
      }
    } catch (err) {
      alert(err?.response?.data?.detail || 'Operation failed');
    } finally {
      setSubmitting(false);
    }
  };

  /* ── Filtered & sorted list ────────────────────────────────────────────── */

  const filtered = stock
    .filter((s) => {
      const name = s.product_name || s.product?.name || '';
      return name.toLowerCase().includes(search.toLowerCase());
    })
    .sort((a, b) => (a.product_name || a.product?.name || '').localeCompare(b.product_name || b.product?.name || '', 'fi'));

  /* ── Render helpers ────────────────────────────────────────────────────── */

  const unitAbbr = (item) => {
    const uid = item.product?.unit_id ?? item.unit_id;
    const u = unitMap[uid];
    return u?.abbreviation || u?.name || '';
  };

  const currentStockForModal = () => {
    if (!modal?.productId) return null;
    return stock.find((s) => (s.product_id ?? s.product?.id) === modal.productId);
  };

  /* ── Loading / error states ────────────────────────────────────────────── */

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && <div className="bg-red-500/20 text-red-400 px-4 py-2 rounded">{error}</div>}

      {/* ── Top bar ────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 items-center">
        <input
          type="text"
          placeholder="🔍 Search products…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px] focus:outline-none focus:ring-2 focus:ring-emerald-500"
        />
        <button
          onClick={() => openModal('add', null, null)}
          className="bg-emerald-600 text-white px-4 py-2 rounded-xl text-sm font-medium hover:bg-emerald-700 transition-colors"
        >
          ➕ Add to Stock
        </button>
        <button
          onClick={() => openModal('transfer', null, null)}
          className="bg-gray-700 text-gray-300 px-4 py-2 rounded-xl text-sm font-medium hover:bg-gray-600 transition-colors"
        >
          🔄 Transfer
        </button>
      </div>

      {/* ── Summary count ──────────────────────────────────────────────────── */}
      <p className="text-xs text-gray-400">
        {filtered.length} products in stock
      </p>

      {/* ── Product cards ──────────────────────────────────────────────────── */}
      {filtered.length === 0 && (
        <p className="text-gray-400 text-center py-8">No results</p>
      )}

      <div className="space-y-2">
        {filtered.map((item) => {
          const pid = item.product_id ?? item.product?.id;
          const pname = item.product_name || item.product?.name || `#${pid}`;
          const amt = item.amount ?? 0;
          const opened = item.amount_opened ?? 0;
          const minStock = item.min_stock_amount ?? item.product?.min_stock_amount ?? 0;
          const picFile = item.product?.picture_filename;
          const isExpanded = expanded === pid;

          return (
            <div key={pid} className="bg-gray-800 rounded-xl border border-gray-700 shadow">
              {/* ── Card row ───────────────────────────────────────────────── */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-700/50 transition-colors"
                onClick={() => toggleExpand(pid)}
              >
                {/* Thumbnail */}
                {picFile ? (
                  <img
                    src={productImageUrl(picFile)}
                    alt=""
                    className="w-10 h-10 rounded object-cover shrink-0"
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                ) : (
                  <div className="w-10 h-10 rounded bg-gray-700 flex items-center justify-center text-gray-500 shrink-0">📦</div>
                )}

                {/* Name + badges */}
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate text-gray-100">{pname}</p>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <span className={`text-sm font-semibold ${stockLevelClass(amt, minStock)}`}>
                      {amt} {unitAbbr(item)}
                    </span>
                    {opened > 0 && (
                      <span className="text-xs bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded">
                        {opened} opened
                      </span>
                    )}
                    {minStock > 0 && amt < minStock && (
                      <span className="text-xs bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded font-medium">
                        Min {minStock}
                      </span>
                    )}
                  </div>
                </div>

                {/* Quick actions */}
                <div className="flex gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => openModal('add', pid, pname)}
                    className="p-1.5 rounded hover:bg-emerald-500/20 text-emerald-400 transition-colors"
                    title="Add"
                  >➕</button>
                  <button
                    onClick={() => openModal('consume', pid, pname)}
                    className="p-1.5 rounded hover:bg-orange-500/20 text-orange-400 transition-colors"
                    title="Consume"
                  >➖</button>
                  <button
                    onClick={() => openModal('open', pid, pname)}
                    className="p-1.5 rounded hover:bg-blue-500/20 text-blue-400 transition-colors"
                    title="Open"
                  >📂</button>
                </div>

                {/* Expand chevron */}
                <span className={`text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>▾</span>
              </div>

              {/* ── Detail view ─────────────────────────────────────────────── */}
              {isExpanded && (
                <div className="border-t border-gray-700 px-4 py-3 bg-gray-700/30">
                  {entries.length === 0 ? (
                    <p className="text-sm text-gray-400">No stock entries</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-xs text-gray-400 uppercase">
                            <th className="pb-1 pr-3">Amount</th>
                            <th className="pb-1 pr-3">Opened</th>
                            <th className="pb-1 pr-3">Location</th>
                            <th className="pb-1 pr-3">Best Before</th>
                            <th className="pb-1 pr-3">Purchased</th>
                            <th className="pb-1"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {entries.map((entry) => {
                            const eu = unitMap[entry.unit_id];
                            const loc = locationMap[entry.location_id];
                            return (
                              <tr key={entry.id} className={`border-t border-gray-700 ${bbClass(entry.best_before_date)}`}>
                                <td className="py-1.5 pr-3 text-gray-100">{entry.amount} {eu?.abbreviation || eu?.name || ''}</td>
                                <td className="py-1.5 pr-3 text-gray-300">{entry.amount_opened || 0}</td>
                                <td className="py-1.5 pr-3 text-gray-300">{loc?.name || '–'}</td>
                                <td className="py-1.5 pr-3 text-gray-300">{fmtDate(entry.best_before_date)}</td>
                                <td className="py-1.5 pr-3 text-gray-300">{fmtDate(entry.purchased_date)}</td>
                                <td className="py-1.5 text-right">
                                  <button
                                    onClick={() => handleDeleteEntry(entry.id)}
                                    className="text-red-500 hover:text-red-700 text-xs"
                                    title="Delete"
                                  >🗑️</button>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Modals ─────────────────────────────────────────────────────────── */}
      {modal?.type === 'add' && (
        <Modal title={modal.productName ? `Add – ${modal.productName}` : 'Add to Stock'} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {!modal.productId && (
              <label className="block">
                <span className="text-sm text-gray-400">Product</span>
                <ProductCombobox
                  products={products}
                  value={formProduct}
                  onChange={setFormProduct}
                  required
                />
              </label>
            )}
            <label className="block">
              <span className="text-sm text-gray-400">Amount</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">Location</span>
              <select
                value={formLoc}
                onChange={(e) => setFormLoc(e.target.value)}
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="">Default location</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">Best Before</span>
              <input
                type="date" value={formBB}
                onChange={(e) => setFormBB(e.target.value)}
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-emerald-600 text-white py-2 rounded-xl text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Adding…' : 'Add'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'consume' && (
        <Modal title={`Consume – ${modal.productName}`} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {(() => { const cs = currentStockForModal(); return cs ? (
              <p className="text-sm text-gray-400">In stock: <span className="font-semibold">{cs.amount}</span></p>
            ) : null; })()}
            <label className="block">
              <span className="text-sm text-gray-400">Amount</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-orange-600 text-white py-2 rounded-xl text-sm font-medium hover:bg-orange-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Consuming…' : 'Consume'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'open' && (
        <Modal title={`Open – ${modal.productName}`} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            <label className="block">
              <span className="text-sm text-gray-400">Amount</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-emerald-600 text-white py-2 rounded-xl text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Opening…' : 'Open'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'transfer' && (
        <Modal title={modal.productName ? `Transfer – ${modal.productName}` : 'Transfer Product'} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {!modal.productId && (
              <label className="block">
                <span className="text-sm text-gray-400">Product</span>
                <ProductCombobox
                  products={stock.map((s) => ({
                    id: s.product_id ?? s.product?.id,
                    name: s.product_name || s.product?.name,
                  }))}
                  value={formProduct}
                  onChange={setFormProduct}
                  required
                />
              </label>
            )}
            <label className="block">
              <span className="text-sm text-gray-400">Amount</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">From</span>
              <select
                value={formFromLoc}
                onChange={(e) => setFormFromLoc(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="">Select location…</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-sm text-gray-400">To</span>
              <select
                value={formToLoc}
                onChange={(e) => setFormToLoc(e.target.value)}
                required
                className="mt-1 block w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="">Select location…</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-gray-600 text-white py-2 rounded-xl text-sm font-medium hover:bg-gray-500 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Transferring…' : 'Transfer'}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
