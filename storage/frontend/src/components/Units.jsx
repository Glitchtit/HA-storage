import { useState, useEffect, useCallback } from 'react';
import {
  getUnits,
  createUnit,
  deleteUnit,
  getConversions,
  createConversion,
  deleteConversion,
  getProducts,
} from '../api';

export default function Units() {
  const [units, setUnits] = useState([]);
  const [conversions, setConversions] = useState([]);
  const [products, setProducts] = useState([]);
  const [error, setError] = useState('');

  // Unit form
  const [name, setName] = useState('');
  const [abbreviation, setAbbreviation] = useState('');
  const [namePlural, setNamePlural] = useState('');

  // Conversion form
  const [fromUnitId, setFromUnitId] = useState('');
  const [toUnitId, setToUnitId] = useState('');
  const [factor, setFactor] = useState('');
  const [productId, setProductId] = useState('');

  const load = useCallback(async () => {
    try {
      const [u, c, p] = await Promise.all([
        getUnits(),
        getConversions(),
        getProducts(),
      ]);
      setUnits(u.data);
      setConversions(c.data);
      setProducts(p.data);
    } catch {
      setError('Failed to load data');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAddUnit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      await createUnit({ name, abbreviation, name_plural: namePlural });
      setName('');
      setAbbreviation('');
      setNamePlural('');
      await load();
    } catch {
      setError('Failed to add unit');
    }
  };

  const handleDeleteUnit = async (id) => {
    if (!window.confirm('Delete this unit?')) return;
    setError('');
    try {
      await deleteUnit(id);
      await load();
    } catch {
      setError('Unit is used by products and cannot be deleted');
    }
  };

  const handleAddConversion = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const data = {
        from_unit_id: Number(fromUnitId),
        to_unit_id: Number(toUnitId),
        factor: Number(factor),
      };
      if (productId) data.product_id = Number(productId);
      await createConversion(data);
      setFromUnitId('');
      setToUnitId('');
      setFactor('');
      setProductId('');
      await load();
    } catch {
      setError('Failed to add conversion');
    }
  };

  const handleDeleteConversion = async (id) => {
    if (!window.confirm('Delete this conversion?')) return;
    setError('');
    try {
      await deleteConversion(id);
      await load();
    } catch {
      setError('Failed to delete conversion');
    }
  };

  const unitName = (id) => units.find((u) => u.id === id)?.name ?? `#${id}`;
  const productName = (id) => products.find((p) => p.id === id)?.name ?? `#${id}`;

  return (
    <div className="space-y-8 max-w-4xl">
      {error && (
        <div className="bg-red-500/20 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg">
          {error}
        </div>
      )}

      {/* ── Units ──────────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold mb-3 text-gray-100">📏 Units</h2>

        <div className="overflow-x-auto">
          <table className="w-full text-sm bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Name</th>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Abbreviation</th>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Plural</th>
                <th className="px-3 py-2 w-20" />
              </tr>
            </thead>
            <tbody>
              {units.map((u) => (
                <tr key={u.id} className="border-b border-gray-700 hover:bg-gray-700/50 text-gray-100">
                  <td className="px-3 py-2">{u.name}</td>
                  <td className="px-3 py-2">{u.abbreviation}</td>
                  <td className="px-3 py-2">{u.name_plural}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleDeleteUnit(u.id)}
                      className="text-red-400 hover:text-red-300 text-xs"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {units.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-gray-400 text-center">
                    No units
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <form onSubmit={handleAddUnit} className="mt-3 flex flex-wrap gap-2 items-end">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-36"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Abbreviation</label>
            <input
              value={abbreviation}
              onChange={(e) => setAbbreviation(e.target.value)}
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-24"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Plural</label>
            <input
              value={namePlural}
              onChange={(e) => setNamePlural(e.target.value)}
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-36"
            />
          </div>
          <button
            type="submit"
            className="bg-emerald-600 text-white px-3 py-1 rounded-xl text-sm hover:bg-emerald-700"
          >
            Add Unit
          </button>
        </form>
      </section>

      {/* ── Conversions ────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold mb-3 text-gray-100">🔄 Conversions</h2>

        <div className="overflow-x-auto">
          <table className="w-full text-sm bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
            <thead className="bg-gray-700/50">
              <tr>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">From Unit</th>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">To Unit</th>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Factor</th>
                <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Product</th>
                <th className="px-3 py-2 w-20" />
              </tr>
            </thead>
            <tbody>
              {conversions.map((c) => (
                <tr key={c.id} className="border-b border-gray-700 hover:bg-gray-700/50 text-gray-100">
                  <td className="px-3 py-2">{unitName(c.from_unit_id)}</td>
                  <td className="px-3 py-2">{unitName(c.to_unit_id)}</td>
                  <td className="px-3 py-2">{c.factor}</td>
                  <td className="px-3 py-2 text-gray-400">
                    {c.product_id ? productName(c.product_id) : '—'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleDeleteConversion(c.id)}
                      className="text-red-400 hover:text-red-300 text-xs"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {conversions.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-gray-400 text-center">
                    No conversions
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <form onSubmit={handleAddConversion} className="mt-3 flex flex-wrap gap-2 items-end">
          <div>
            <label className="block text-xs text-gray-400 mb-1">From Unit</label>
            <select
              value={fromUnitId}
              onChange={(e) => setFromUnitId(e.target.value)}
              required
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-36"
            >
              <option value="">Select…</option>
              {units.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">To Unit</label>
            <select
              value={toUnitId}
              onChange={(e) => setToUnitId(e.target.value)}
              required
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-36"
            >
              <option value="">Select…</option>
              {units.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Factor</label>
            <input
              type="number"
              step="any"
              value={factor}
              onChange={(e) => setFactor(e.target.value)}
              required
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-24"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Product (optional)</label>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-44"
            >
              <option value="">Global</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            className="bg-emerald-600 text-white px-3 py-1 rounded-xl text-sm hover:bg-emerald-700"
          >
            Add Conversion
          </button>
        </form>
      </section>
    </div>
  );
}
