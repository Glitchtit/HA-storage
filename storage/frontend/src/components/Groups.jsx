import { useState, useEffect, useCallback } from 'react';
import { getProductGroups, createProductGroup, deleteProductGroup } from '../api';

export default function Groups() {
  const [groups, setGroups] = useState([]);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getProductGroups();
      setGroups(data);
    } catch {
      setError('Failed to load data');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (e) => {
    e.preventDefault();
    setError('');
    try {
      await createProductGroup({ name, description });
      setName('');
      setDescription('');
      await load();
    } catch {
      setError('Failed to add product group');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this product group?')) return;
    setError('');
    try {
      await deleteProductGroup(id);
      await load();
    } catch {
      setError('Product group is used by products and cannot be deleted');
    }
  };

  return (
    <div className="space-y-4 max-w-3xl">
      {error && (
        <div className="bg-red-500/20 border border-red-500/30 text-red-400 px-4 py-2 rounded-lg">
          {error}
        </div>
      )}

      <h2 className="text-lg font-semibold text-gray-100">🏷️ Product Groups</h2>

      <div className="overflow-x-auto">
        <table className="w-full text-sm bg-gray-800 border border-gray-700 rounded-xl overflow-hidden">
          <thead className="bg-gray-700/50">
            <tr>
              <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Name</th>
              <th className="text-left px-3 py-2 text-gray-400 text-xs uppercase">Description</th>
              <th className="px-3 py-2 w-20" />
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id} className="border-b border-gray-700 hover:bg-gray-700/50 text-gray-100">
                <td className="px-3 py-2">{g.name}</td>
                <td className="px-3 py-2 text-gray-400">{g.description || '—'}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => handleDelete(g.id)}
                    className="text-red-400 hover:text-red-300 text-xs"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {groups.length === 0 && (
              <tr>
                <td colSpan={3} className="px-3 py-4 text-gray-400 text-center">
                  No product groups
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <form onSubmit={handleAdd} className="flex flex-wrap gap-2 items-end">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-44"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Description</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none rounded-lg px-2 py-1 text-sm w-56"
          />
        </div>
        <button
          type="submit"
          className="bg-emerald-600 text-white px-3 py-1 rounded-xl text-sm hover:bg-emerald-700"
        >
          Add Group
        </button>
      </form>
    </div>
  );
}
