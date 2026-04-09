import { useState, useEffect, useCallback } from 'react';
import {
  getBarcodeQueue,
  addToBarcodeQueue,
  deleteBarcodeQueue,
} from '../api';

const STATUS_TABS = [
  { key: null, label: 'Kaikki' },
  { key: 'pending', label: 'Odottaa' },
  { key: 'done', label: 'Valmis' },
  { key: 'error', label: 'Virhe' },
];

const STATUS_BADGE = {
  pending: 'bg-yellow-100 text-yellow-800',
  done: 'bg-green-100 text-green-800',
  error: 'bg-red-100 text-red-800',
};

const STATUS_LABEL = {
  pending: 'Odottaa',
  done: 'Valmis',
  error: 'Virhe',
};

export default function BarcodeQueue() {
  const [items, setItems] = useState([]);
  const [statusFilter, setStatusFilter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [barcode, setBarcode] = useState('');
  const [source, setSource] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = statusFilter ? { status: statusFilter } : {};
      const { data } = await getBarcodeQueue(params);
      setItems(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to load barcode queue', err);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!barcode.trim()) return;
    setSubmitting(true);
    try {
      await addToBarcodeQueue({ barcode: barcode.trim(), source: source.trim() || undefined });
      setBarcode('');
      setSource('');
      await load();
    } catch (err) {
      console.error('Failed to add barcode', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteBarcodeQueue(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      console.error('Failed to delete queue item', err);
    }
  };

  const fmtDate = (iso) => {
    if (!iso) return '–';
    return new Date(iso).toLocaleString('fi-FI');
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-bold">📱 Viivakoodijono</h2>
        <span className="bg-blue-100 text-blue-800 text-xs font-medium px-2.5 py-0.5 rounded-full">
          {items.length}
        </span>
      </div>

      {/* Status filter tabs */}
      <div className="flex gap-1 border-b">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key ?? 'all'}
            onClick={() => setStatusFilter(tab.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              statusFilter === tab.key
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Manual submit form */}
      <form onSubmit={handleAdd} className="bg-white rounded-lg border p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Lisää viivakoodi jonoon</h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-gray-500 mb-1">Viivakoodi</label>
            <input
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              placeholder="6410405204806"
              className="w-full border rounded px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-blue-300 focus:outline-none"
              required
            />
          </div>
          <div className="flex-1 min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">Lähde</label>
            <input
              type="text"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="manual"
              className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !barcode.trim()}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? 'Lisätään…' : 'Lisää jonoon'}
          </button>
        </div>
      </form>

      {/* Queue table */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">Ladataan…</div>
      ) : items.length === 0 ? (
        <div className="text-center py-8 text-gray-400">Jono on tyhjä</div>
      ) : (
        <div className="bg-white rounded-lg border overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-xs text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-3">Viivakoodi</th>
                <th className="px-4 py-3">Lähde</th>
                <th className="px-4 py-3">Tila</th>
                <th className="px-4 py-3">Tuote-ID</th>
                <th className="px-4 py-3">Virhe</th>
                <th className="px-4 py-3">Luotu</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3 font-mono text-sm">{item.barcode}</td>
                  <td className="px-4 py-3 text-gray-600">{item.source || '–'}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${
                        STATUS_BADGE[item.status] ?? 'bg-gray-100 text-gray-800'
                      }`}
                    >
                      {STATUS_LABEL[item.status] ?? item.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {item.status === 'done' && item.result_product_id != null
                      ? item.result_product_id
                      : '–'}
                  </td>
                  <td className="px-4 py-3 text-red-600 text-xs max-w-[200px] truncate" title={item.error_message}>
                    {item.status === 'error' && item.error_message ? item.error_message : '–'}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {fmtDate(item.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="text-red-500 hover:text-red-700 text-xs font-medium transition-colors"
                      title="Poista"
                    >
                      Poista
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
