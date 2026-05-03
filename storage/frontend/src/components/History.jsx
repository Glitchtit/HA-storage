import { useState, useEffect, useCallback, useMemo } from 'react';
import { getHistory, getProducts, deleteHistoryEntry } from '../api';

const EVENT_META = {
  purchase: { label: 'Purchase', emoji: '🛒', cls: 'bg-emerald-500/20 text-emerald-400' },
  consume: { label: 'Consume', emoji: '🍴', cls: 'bg-blue-500/20 text-blue-400' },
  open: { label: 'Open', emoji: '📂', cls: 'bg-purple-500/20 text-purple-400' },
  transfer: { label: 'Transfer', emoji: '🔀', cls: 'bg-yellow-500/20 text-yellow-400' },
  spoil: { label: 'Spoil', emoji: '🗑️', cls: 'bg-red-500/20 text-red-400' },
};

function formatDateTime(s) {
  if (!s) return '—';
  const d = new Date(s.replace(' ', 'T') + 'Z');
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString('fi-FI');
}

export default function History() {
  const [events, setEvents] = useState([]);
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [productId, setProductId] = useState('');
  const [eventType, setEventType] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = { limit: 500 };
      if (productId) params.product_id = productId;
      if (eventType) params.event_type = eventType;
      if (since) params.since = since;
      if (until) params.until = until;
      const [hist, prods] = await Promise.all([
        getHistory(params),
        products.length ? Promise.resolve({ data: products }) : getProducts(),
      ]);
      setEvents(hist.data);
      if (!products.length) setProducts(prods.data);
    } catch (e) {
      setError('Failed to load history');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, eventType, since, until]);

  useEffect(() => { load(); }, [load]);

  const productMap = useMemo(() => {
    const m = new Map();
    products.forEach((p) => m.set(p.id, p.name));
    return m;
  }, [products]);

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this history entry?')) return;
    try {
      await deleteHistoryEntry(id);
      await load();
    } catch {
      setError('Failed to delete entry');
    }
  };

  return (
    <div className="space-y-4 max-w-6xl mx-auto">
      <div className="bg-gray-800 rounded-lg shadow p-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <label className="text-xs text-gray-400">Product</label>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
            >
              <option value="">All products</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-400">Event type</label>
            <select
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
            >
              <option value="">All events</option>
              {Object.entries(EVENT_META).map(([k, v]) => (
                <option key={k} value={k}>{v.emoji} {v.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-400">Since</label>
            <input
              type="date"
              value={since}
              onChange={(e) => setSince(e.target.value)}
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
            />
          </div>
          <div>
            <label className="text-xs text-gray-400">Until</label>
            <input
              type="date"
              value={until}
              onChange={(e) => setUntil(e.target.value)}
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
            />
          </div>
        </div>
        {(productId || eventType || since || until) && (
          <button
            onClick={() => { setProductId(''); setEventType(''); setSince(''); setUntil(''); }}
            className="mt-3 text-xs text-gray-400 hover:text-gray-200"
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12">
          <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3" />
          <p className="text-gray-500">Loading...</p>
        </div>
      ) : events.length === 0 ? (
        <div className="bg-gray-800 rounded-lg p-6 text-center text-gray-500 text-sm">
          No events match the selected filters.
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg shadow overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-800/50 text-gray-400 text-xs uppercase">
              <tr>
                <th className="text-left px-4 py-2 font-medium">When</th>
                <th className="text-left px-4 py-2 font-medium">Event</th>
                <th className="text-left px-4 py-2 font-medium">Product</th>
                <th className="text-right px-4 py-2 font-medium">Amount</th>
                <th className="text-left px-4 py-2 font-medium">Note</th>
                <th className="px-2 py-2 w-10" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {events.map((e) => {
                const meta = EVENT_META[e.event_type] ?? {
                  label: e.event_type, emoji: '•', cls: 'bg-gray-500/20 text-gray-400',
                };
                const name = e.product_name ?? productMap.get(e.product_id) ?? `#${e.product_id}`;
                return (
                  <tr key={e.id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-2 text-gray-400 whitespace-nowrap">
                      {formatDateTime(e.created_at)}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${meta.cls}`}>
                        {meta.emoji} {meta.label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-100 font-medium">{name}</td>
                    <td className="px-4 py-2 text-right text-gray-100 font-mono">{e.amount}</td>
                    <td className="px-4 py-2 text-gray-400">{e.note || '—'}</td>
                    <td className="px-2 py-2 text-right">
                      <button
                        onClick={() => handleDelete(e.id)}
                        className="text-gray-500 hover:text-red-400 text-xs"
                        title="Delete entry"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
