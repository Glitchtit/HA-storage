import { useState, useEffect, useCallback } from 'react';
import { getLocations, createLocation, deleteLocation } from '../api';

export default function Locations() {
  const [locations, setLocations] = useState([]);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getLocations();
      setLocations(data);
    } catch {
      setError('Tietojen lataus epäonnistui');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (e) => {
    e.preventDefault();
    setError('');
    try {
      await createLocation({ name, description });
      setName('');
      setDescription('');
      await load();
    } catch {
      setError('Sijainnin lisäys epäonnistui');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Poistetaanko sijainti?')) return;
    setError('');
    try {
      await deleteLocation(id);
      await load();
    } catch {
      setError('Sijaintia käytetään tuotteissa eikä sitä voi poistaa');
    }
  };

  return (
    <div className="space-y-4 max-w-3xl">
      {error && (
        <div className="bg-red-50 border border-red-300 text-red-700 px-4 py-2 rounded">
          {error}
        </div>
      )}

      <h2 className="text-lg font-semibold">📍 Sijainnit</h2>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border border-gray-200 rounded">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left px-3 py-2">Nimi</th>
              <th className="text-left px-3 py-2">Kuvaus</th>
              <th className="px-3 py-2 w-20" />
            </tr>
          </thead>
          <tbody>
            {locations.map((loc) => (
              <tr key={loc.id} className="border-t border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2">{loc.name}</td>
                <td className="px-3 py-2 text-gray-500">{loc.description || '—'}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => handleDelete(loc.id)}
                    className="text-red-500 hover:text-red-700 text-xs"
                  >
                    Poista
                  </button>
                </td>
              </tr>
            ))}
            {locations.length === 0 && (
              <tr>
                <td colSpan={3} className="px-3 py-4 text-gray-400 text-center">
                  Ei sijainteja
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <form onSubmit={handleAdd} className="flex flex-wrap gap-2 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Nimi</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="border rounded px-2 py-1 text-sm w-44"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Kuvaus</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="border rounded px-2 py-1 text-sm w-56"
          />
        </div>
        <button
          type="submit"
          className="bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600"
        >
          Lisää sijainti
        </button>
      </form>
    </div>
  );
}
