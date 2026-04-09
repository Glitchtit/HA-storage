import { useState, useEffect, useCallback } from 'react';
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
  if (d < 0) return 'bg-red-100 text-red-800';
  if (d <= 3) return 'bg-orange-100 text-orange-800';
  if (d <= 7) return 'bg-yellow-50 text-yellow-800';
  return '';
}

function stockLevelClass(amount, min) {
  if (min > 0 && amount < min) return 'text-red-600';
  if (min > 0 && amount <= min * 1.25) return 'text-yellow-600';
  return 'text-green-600';
}

/* ── Modal shell ─────────────────────────────────────────────────────────── */

function Modal({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="font-semibold">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
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
      setError('Varastotietojen lataus epäonnistui');
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
    if (!confirm('Poistetaanko varastomerkintä?')) return;
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
      alert(err?.response?.data?.detail || 'Toiminto epäonnistui');
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
        <div className="animate-spin h-8 w-8 border-4 border-blue-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && <div className="bg-red-50 text-red-700 px-4 py-2 rounded">{error}</div>}

      {/* ── Top bar ────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 items-center">
        <input
          type="text"
          placeholder="🔍 Hae tuotetta…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px] focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <button
          onClick={() => openModal('add', null, null)}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          ➕ Lisää varastoon
        </button>
        <button
          onClick={() => openModal('transfer', null, null)}
          className="bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
        >
          🔄 Siirrä
        </button>
      </div>

      {/* ── Summary count ──────────────────────────────────────────────────── */}
      <p className="text-xs text-gray-400">
        {filtered.length} tuotetta varastossa
      </p>

      {/* ── Product cards ──────────────────────────────────────────────────── */}
      {filtered.length === 0 && (
        <p className="text-gray-400 text-center py-8">Ei tuloksia</p>
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
            <div key={pid} className="bg-white rounded-lg border shadow-sm">
              {/* ── Card row ───────────────────────────────────────────────── */}
              <div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors"
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
                  <div className="w-10 h-10 rounded bg-gray-100 flex items-center justify-center text-gray-300 shrink-0">📦</div>
                )}

                {/* Name + badges */}
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate">{pname}</p>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <span className={`text-sm font-semibold ${stockLevelClass(amt, minStock)}`}>
                      {amt} {unitAbbr(item)}
                    </span>
                    {opened > 0 && (
                      <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                        {opened} avattu
                      </span>
                    )}
                    {minStock > 0 && amt < minStock && (
                      <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-medium">
                        Minimi {minStock}
                      </span>
                    )}
                  </div>
                </div>

                {/* Quick actions */}
                <div className="flex gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => openModal('add', pid, pname)}
                    className="p-1.5 rounded hover:bg-green-100 text-green-700 transition-colors"
                    title="Lisää"
                  >➕</button>
                  <button
                    onClick={() => openModal('consume', pid, pname)}
                    className="p-1.5 rounded hover:bg-orange-100 text-orange-700 transition-colors"
                    title="Kuluta"
                  >➖</button>
                  <button
                    onClick={() => openModal('open', pid, pname)}
                    className="p-1.5 rounded hover:bg-blue-100 text-blue-700 transition-colors"
                    title="Avaa"
                  >📂</button>
                </div>

                {/* Expand chevron */}
                <span className={`text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>▾</span>
              </div>

              {/* ── Detail view ─────────────────────────────────────────────── */}
              {isExpanded && (
                <div className="border-t px-4 py-3 bg-gray-50">
                  {entries.length === 0 ? (
                    <p className="text-sm text-gray-400">Ei varastomerkintöjä</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-xs text-gray-500 uppercase">
                            <th className="pb-1 pr-3">Määrä</th>
                            <th className="pb-1 pr-3">Avattu</th>
                            <th className="pb-1 pr-3">Sijainti</th>
                            <th className="pb-1 pr-3">Parasta ennen</th>
                            <th className="pb-1 pr-3">Ostettu</th>
                            <th className="pb-1"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {entries.map((entry) => {
                            const eu = unitMap[entry.unit_id];
                            const loc = locationMap[entry.location_id];
                            return (
                              <tr key={entry.id} className={`border-t ${bbClass(entry.best_before_date)}`}>
                                <td className="py-1.5 pr-3">{entry.amount} {eu?.abbreviation || eu?.name || ''}</td>
                                <td className="py-1.5 pr-3">{entry.amount_opened || 0}</td>
                                <td className="py-1.5 pr-3">{loc?.name || '–'}</td>
                                <td className="py-1.5 pr-3">{fmtDate(entry.best_before_date)}</td>
                                <td className="py-1.5 pr-3">{fmtDate(entry.purchased_date)}</td>
                                <td className="py-1.5 text-right">
                                  <button
                                    onClick={() => handleDeleteEntry(entry.id)}
                                    className="text-red-500 hover:text-red-700 text-xs"
                                    title="Poista"
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
        <Modal title={modal.productName ? `Lisää – ${modal.productName}` : 'Lisää varastoon'} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {!modal.productId && (
              <label className="block">
                <span className="text-sm text-gray-600">Tuote</span>
                <select
                  value={formProduct}
                  onChange={(e) => setFormProduct(e.target.value)}
                  required
                  className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                >
                  <option value="">Valitse tuote…</option>
                  {products.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="block">
              <span className="text-sm text-gray-600">Määrä</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <label className="block">
              <span className="text-sm text-gray-600">Sijainti</span>
              <select
                value={formLoc}
                onChange={(e) => setFormLoc(e.target.value)}
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="">Oletussijainti</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-sm text-gray-600">Parasta ennen</span>
              <input
                type="date" value={formBB}
                onChange={(e) => setFormBB(e.target.value)}
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Lisätään…' : 'Lisää'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'consume' && (
        <Modal title={`Kuluta – ${modal.productName}`} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {(() => { const cs = currentStockForModal(); return cs ? (
              <p className="text-sm text-gray-500">Varastossa: <span className="font-semibold">{cs.amount}</span></p>
            ) : null; })()}
            <label className="block">
              <span className="text-sm text-gray-600">Määrä</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-orange-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-orange-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Kulutetaan…' : 'Kuluta'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'open' && (
        <Modal title={`Avaa – ${modal.productName}`} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            <label className="block">
              <span className="text-sm text-gray-600">Määrä</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Avataan…' : 'Avaa'}
            </button>
          </form>
        </Modal>
      )}

      {modal?.type === 'transfer' && (
        <Modal title={modal.productName ? `Siirrä – ${modal.productName}` : 'Siirrä tuote'} onClose={closeModal}>
          <form onSubmit={handleSubmit} className="space-y-3">
            {!modal.productId && (
              <label className="block">
                <span className="text-sm text-gray-600">Tuote</span>
                <select
                  value={formProduct}
                  onChange={(e) => setFormProduct(e.target.value)}
                  required
                  className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                >
                  <option value="">Valitse tuote…</option>
                  {stock.map((s) => {
                    const sid = s.product_id ?? s.product?.id;
                    return <option key={sid} value={sid}>{s.product_name || s.product?.name}</option>;
                  })}
                </select>
              </label>
            )}
            <label className="block">
              <span className="text-sm text-gray-600">Määrä</span>
              <input
                type="number" min="0.01" step="any" value={formAmt}
                onChange={(e) => setFormAmt(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <label className="block">
              <span className="text-sm text-gray-600">Mistä</span>
              <select
                value={formFromLoc}
                onChange={(e) => setFormFromLoc(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="">Valitse sijainti…</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-sm text-gray-600">Minne</span>
              <select
                value={formToLoc}
                onChange={(e) => setFormToLoc(e.target.value)}
                required
                className="mt-1 block w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="">Valitse sijainti…</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>{l.name}</option>
                ))}
              </select>
            </label>
            <button
              type="submit" disabled={submitting}
              className="w-full bg-gray-700 text-white py-2 rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-50 transition-colors"
            >
              {submitting ? 'Siirretään…' : 'Siirrä'}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
