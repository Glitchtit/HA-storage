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
      setError('Tietojen lataus epäonnistui');
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
      setError('Yksikön lisäys epäonnistui');
    }
  };

  const handleDeleteUnit = async (id) => {
    if (!window.confirm('Poistetaanko yksikkö?')) return;
    setError('');
    try {
      await deleteUnit(id);
      await load();
    } catch {
      setError('Yksikköä käytetään tuotteissa eikä sitä voi poistaa');
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
      setError('Muunnoksen lisäys epäonnistui');
    }
  };

  const handleDeleteConversion = async (id) => {
    if (!window.confirm('Poistetaanko muunnos?')) return;
    setError('');
    try {
      await deleteConversion(id);
      await load();
    } catch {
      setError('Muunnoksen poisto epäonnistui');
    }
  };

  const unitName = (id) => units.find((u) => u.id === id)?.name ?? `#${id}`;
  const productName = (id) => products.find((p) => p.id === id)?.name ?? `#${id}`;

  return (
    <div className="space-y-8 max-w-4xl">
      {error && (
        <div className="bg-red-50 border border-red-300 text-red-700 px-4 py-2 rounded">
          {error}
        </div>
      )}

      {/* ── Units ──────────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">📏 Yksiköt</h2>

        <div className="overflow-x-auto">
          <table className="w-full text-sm border border-gray-200 rounded">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-3 py-2">Nimi</th>
                <th className="text-left px-3 py-2">Lyhenne</th>
                <th className="text-left px-3 py-2">Monikko</th>
                <th className="px-3 py-2 w-20" />
              </tr>
            </thead>
            <tbody>
              {units.map((u) => (
                <tr key={u.id} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-2">{u.name}</td>
                  <td className="px-3 py-2">{u.abbreviation}</td>
                  <td className="px-3 py-2">{u.name_plural}</td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleDeleteUnit(u.id)}
                      className="text-red-500 hover:text-red-700 text-xs"
                    >
                      Poista
                    </button>
                  </td>
                </tr>
              ))}
              {units.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-gray-400 text-center">
                    Ei yksiköitä
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <form onSubmit={handleAddUnit} className="mt-3 flex flex-wrap gap-2 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Nimi</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="border rounded px-2 py-1 text-sm w-36"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Lyhenne</label>
            <input
              value={abbreviation}
              onChange={(e) => setAbbreviation(e.target.value)}
              className="border rounded px-2 py-1 text-sm w-24"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Monikko</label>
            <input
              value={namePlural}
              onChange={(e) => setNamePlural(e.target.value)}
              className="border rounded px-2 py-1 text-sm w-36"
            />
          </div>
          <button
            type="submit"
            className="bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600"
          >
            Lisää yksikkö
          </button>
        </form>
      </section>

      {/* ── Conversions ────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold mb-3">🔄 Muunnokset</h2>

        <div className="overflow-x-auto">
          <table className="w-full text-sm border border-gray-200 rounded">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-3 py-2">Yksiköstä</th>
                <th className="text-left px-3 py-2">Yksikköön</th>
                <th className="text-left px-3 py-2">Kerroin</th>
                <th className="text-left px-3 py-2">Tuote</th>
                <th className="px-3 py-2 w-20" />
              </tr>
            </thead>
            <tbody>
              {conversions.map((c) => (
                <tr key={c.id} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-2">{unitName(c.from_unit_id)}</td>
                  <td className="px-3 py-2">{unitName(c.to_unit_id)}</td>
                  <td className="px-3 py-2">{c.factor}</td>
                  <td className="px-3 py-2 text-gray-500">
                    {c.product_id ? productName(c.product_id) : '—'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => handleDeleteConversion(c.id)}
                      className="text-red-500 hover:text-red-700 text-xs"
                    >
                      Poista
                    </button>
                  </td>
                </tr>
              ))}
              {conversions.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-gray-400 text-center">
                    Ei muunnoksia
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <form onSubmit={handleAddConversion} className="mt-3 flex flex-wrap gap-2 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Yksiköstä</label>
            <select
              value={fromUnitId}
              onChange={(e) => setFromUnitId(e.target.value)}
              required
              className="border rounded px-2 py-1 text-sm w-36"
            >
              <option value="">Valitse…</option>
              {units.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Yksikköön</label>
            <select
              value={toUnitId}
              onChange={(e) => setToUnitId(e.target.value)}
              required
              className="border rounded px-2 py-1 text-sm w-36"
            >
              <option value="">Valitse…</option>
              {units.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Kerroin</label>
            <input
              type="number"
              step="any"
              value={factor}
              onChange={(e) => setFactor(e.target.value)}
              required
              className="border rounded px-2 py-1 text-sm w-24"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Tuote (valinnainen)</label>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              className="border rounded px-2 py-1 text-sm w-44"
            >
              <option value="">Yleinen</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            className="bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600"
          >
            Lisää muunnos
          </button>
        </form>
      </section>
    </div>
  );
}
