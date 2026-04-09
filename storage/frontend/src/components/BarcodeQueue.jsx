import { useState, useEffect, useCallback } from 'react';
import {
  getBarcodeQueue,
  addToBarcodeQueue,
  deleteBarcodeQueue,
} from '../api';

const STATUS_TABS = [
  { key: null, label: 'All' },
  { key: 'pending', label: 'Pending' },
  { key: 'done', label: 'Done' },
  { key: 'error', label: 'Error' },
];

const STATUS_BADGE = {
  pending: 'bg-yellow-500/20 text-yellow-400',
  done: 'bg-emerald-500/20 text-emerald-400',
  error: 'bg-red-500/20 text-red-400',
};

const STATUS_LABEL = {
  pending: 'Pending',
  done: 'Done',
  error: 'Error',
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
    return new Date(iso).toLocaleString('en-GB');
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-bold text-gray-100">📱 Barcode Queue</h2>
        <span className="bg-emerald-500/20 text-emerald-400 text-xs font-medium px-2.5 py-0.5 rounded-full">
          {items.length}
        </span>
      </div>

      {/* Status filter tabs */}
      <div className="flex gap-1">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key ?? 'all'}
            onClick={() => setStatusFilter(tab.key)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
              statusFilter === tab.key
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Manual submit form */}
      <form onSubmit={handleAdd} className="bg-gray-800 rounded-xl border border-gray-700 p-4">
        <h3 className="text-sm font-semibold text-gray-100 mb-3">Add barcode to queue</h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-gray-400 mb-1">Barcode</label>
            <input
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              placeholder="6410405204806"
              className="w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              required
            />
          </div>
          <div className="flex-1 min-w-[150px]">
            <label className="block text-xs text-gray-400 mb-1">Source</label>
            <input
              type="text"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="manual"
              className="w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !barcode.trim()}
            className="bg-emerald-600 text-white px-4 py-2 rounded-xl text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? 'Adding…' : 'Add to Queue'}
          </button>
        </div>
      </form>

      {/* Queue table */}
      {loading ? (
        <div className="text-center py-8 text-gray-400">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-center py-8 text-gray-400">Queue is empty</div>
      ) : (
        <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-700/50 text-left text-xs text-gray-400 uppercase tracking-wide">
                <th className="px-4 py-3">Barcode</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Product ID</th>
                <th className="px-4 py-3">Error</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {items.map((item) => (
                <tr key={item.id} className="hover:bg-gray-700/50 transition-colors text-gray-100">
                  <td className="px-4 py-3 font-mono text-sm">{item.barcode}</td>
                  <td className="px-4 py-3 text-gray-400">{item.source || '–'}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${
                        STATUS_BADGE[item.status] ?? 'bg-gray-700 text-gray-400'
                      }`}
                    >
                      {STATUS_LABEL[item.status] ?? item.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400">
                    {item.status === 'done' && item.result_product_id != null
                      ? item.result_product_id
                      : '–'}
                  </td>
                  <td className="px-4 py-3 text-red-400 text-xs max-w-[200px] truncate" title={item.error_message}>
                    {item.status === 'error' && item.error_message ? item.error_message : '–'}
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                    {fmtDate(item.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="text-red-400 hover:text-red-300 text-xs font-medium transition-colors"
                      title="Delete"
                    >
                      Delete
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
