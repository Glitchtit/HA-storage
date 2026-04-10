import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  getShoppingList,
  addShoppingItem,
  updateShoppingItem,
  deleteShoppingItem,
  clearDoneShopping,
  getProducts,
  getUnits,
  getRecipes,
} from '../api';

export default function ShoppingList() {
  const [items, setItems] = useState([]);
  const [products, setProducts] = useState([]);
  const [units, setUnits] = useState([]);
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Add-form state
  const [form, setForm] = useState({ product_id: '', amount: '', unit_id: '', note: '' });
  const [productSearch, setProductSearch] = useState('');
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Lookup maps
  const productMap = useMemo(() => Object.fromEntries(products.map((p) => [p.id, p])), [products]);
  const unitMap = useMemo(() => Object.fromEntries(units.map((u) => [u.id, u])), [units]);
  const recipeMap = useMemo(() => Object.fromEntries(recipes.map((r) => [r.id, r])), [recipes]);

  const fetchAll = useCallback(async () => {
    try {
      const [shopRes, prodRes, unitRes, recRes] = await Promise.all([
        getShoppingList(),
        getProducts(),
        getUnits(),
        getRecipes(),
      ]);
      setItems(shopRes.data);
      setProducts(prodRes.data);
      setUnits(unitRes.data);
      setRecipes(recRes.data);
    } catch (e) {
      setError('Failed to load data');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Close product dropdown on outside click
  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Split into active / done
  const activeItems = useMemo(() => items.filter((i) => !i.done), [items]);
  const doneItems = useMemo(() => items.filter((i) => i.done), [items]);
  const doneCount = doneItems.length;

  // Filtered products for dropdown
  const filteredProducts = useMemo(() => {
    const q = productSearch.toLowerCase().trim();
    if (!q) return products;
    return products.filter((p) => p.name?.toLowerCase().includes(q));
  }, [products, productSearch]);

  // Auto-fill unit when product changes
  const selectProduct = (product) => {
    setForm((f) => ({
      ...f,
      product_id: product.id,
      unit_id: product.qu_id_purchase || product.qu_id_stock || '',
    }));
    setProductSearch(product.name);
    setDropdownOpen(false);
  };

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!form.product_id) return;
    setSubmitting(true);
    try {
      const payload = {
        product_id: Number(form.product_id),
        amount: Number(form.amount) || 1,
        ...(form.unit_id ? { unit_id: Number(form.unit_id) } : {}),
        ...(form.note ? { note: form.note } : {}),
      };
      await addShoppingItem(payload);
      setForm({ product_id: '', amount: '', unit_id: '', note: '' });
      setProductSearch('');
      setShowForm(false);
      await fetchAll();
    } catch (e) {
      setError('Failed to add item');
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  const toggleDone = async (item) => {
    try {
      await updateShoppingItem(item.id, { done: !item.done });
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, done: !i.done } : i)));
    } catch (e) {
      setError('Update failed');
      console.error(e);
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteShoppingItem(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (e) {
      setError('Delete failed');
      console.error(e);
    }
  };

  const handleClearDone = async () => {
    if (!doneCount) return;
    try {
      await clearDoneShopping();
      setItems((prev) => prev.filter((i) => !i.done));
    } catch (e) {
      setError('Failed to clear done items');
      console.error(e);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Error banner */}
      {error && (
        <div className="mb-4 p-3 bg-red-600/10 text-red-400 rounded-lg flex items-center justify-between text-sm">
          <span>{error}</span>
          <button onClick={() => setError('')} className="ml-2 font-bold hover:text-red-300">✕</button>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h2 className="text-xl font-bold">🛒 Shopping List</h2>
        <span className="bg-emerald-600/20 text-emerald-400 text-xs font-semibold px-2 py-0.5 rounded-full">
          {items.length}
        </span>
        <div className="flex-1" />
        <button
          onClick={() => setShowForm((v) => !v)}
          className="px-3 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition-colors"
        >
          ➕ Add
        </button>
        {doneCount > 0 && (
          <button
            onClick={handleClearDone}
            className="px-3 py-1.5 text-sm bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 transition-colors"
          >
            🧹 Clear done ({doneCount})
          </button>
        )}
      </div>

      {/* Add item form */}
      {showForm && (
        <form onSubmit={handleAdd} className="mb-6 p-4 bg-gray-800 border border-gray-700 rounded-xl shadow-lg space-y-3">
          <h3 className="font-semibold text-sm text-gray-100">Add product to list</h3>

          {/* Product dropdown */}
          <div className="relative" ref={dropdownRef}>
            <label className="block text-xs text-gray-400 mb-1">Product *</label>
            <input
              type="text"
              placeholder="Search product…"
              value={productSearch}
              onChange={(e) => {
                setProductSearch(e.target.value);
                setDropdownOpen(true);
                if (!e.target.value) setForm((f) => ({ ...f, product_id: '', unit_id: '' }));
              }}
              onFocus={() => setDropdownOpen(true)}
              className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
            {dropdownOpen && filteredProducts.length > 0 && (
              <ul className="absolute z-10 mt-1 w-full bg-gray-800 border border-gray-700 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                {filteredProducts.slice(0, 50).map((p) => (
                  <li
                    key={p.id}
                    onClick={() => selectProduct(p)}
                    className="px-3 py-2 text-sm text-gray-100 hover:bg-gray-700 cursor-pointer"
                  >
                    {p.name}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            {/* Amount */}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Amount</label>
              <input
                type="number"
                min="0"
                step="any"
                placeholder="1"
                value={form.amount}
                onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              />
            </div>

            {/* Unit (read-only, auto from product) */}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Unit</label>
              <input
                type="text"
                readOnly
                value={form.unit_id && unitMap[form.unit_id] ? unitMap[form.unit_id].name : '—'}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-400"
              />
            </div>

            {/* Note */}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Note</label>
              <input
                type="text"
                placeholder="Optional"
                value={form.note}
                onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
                className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => { setShowForm(false); setProductSearch(''); setForm({ product_id: '', amount: '', unit_id: '', note: '' }); }}
              className="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!form.product_id || submitting}
              className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? 'Adding…' : 'Add'}
            </button>
          </div>
        </form>
      )}

      {/* Empty state */}
      {items.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <div className="text-5xl mb-3">🛒</div>
          <p className="text-lg">Shopping list is empty</p>
        </div>
      )}

      {/* Active items */}
      {activeItems.length > 0 && (
        <ul className="space-y-1 mb-4">
          {activeItems.map((item) => (
            <ShoppingRow
              key={item.id}
              item={item}
              productMap={productMap}
              unitMap={unitMap}
              recipeMap={recipeMap}
              onToggle={toggleDone}
              onDelete={handleDelete}
            />
          ))}
        </ul>
      )}

      {/* Done items */}
      {doneItems.length > 0 && (
        <>
          <p className="text-xs text-gray-400 uppercase tracking-wide mb-1 mt-6">
            Done ({doneCount})
          </p>
          <ul className="space-y-1">
            {doneItems.map((item) => (
              <ShoppingRow
                key={item.id}
                item={item}
                productMap={productMap}
                unitMap={unitMap}
                recipeMap={recipeMap}
                onToggle={toggleDone}
                onDelete={handleDelete}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ShoppingRow({ item, productMap, unitMap, recipeMap, onToggle, onDelete }) {
  const product = productMap[item.product_id];
  const unit = item.unit_id ? unitMap[item.unit_id] : null;
  const recipe = item.recipe_id ? recipeMap[item.recipe_id] : null;
  const done = item.done;

  return (
    <li
      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors ${
        done ? 'bg-gray-800/50 border-gray-700' : 'bg-gray-800 border-gray-700 hover:border-gray-600'
      }`}
    >
      {/* Checkbox */}
      <input
        type="checkbox"
        checked={!!done}
        onChange={() => onToggle(item)}
        className="h-4 w-4 rounded accent-emerald-500 focus:ring-emerald-500 cursor-pointer shrink-0"
      />

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-sm font-medium truncate ${done ? 'line-through text-gray-500' : 'text-gray-100'}`}>
            {product?.name ?? `Product #${item.product_id}`}
          </span>

          <span className={`text-xs ${done ? 'text-gray-600' : 'text-gray-400'}`}>
            {item.amount ?? 1}{unit ? ` ${unit.name}` : ''}
          </span>

          {recipe && (
            <span className="inline-flex items-center text-[11px] px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400 leading-tight">
              🍽️ {recipe.name}
            </span>
          )}

          {item.auto_added && (
            <span className="inline-flex items-center text-[11px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-400 leading-tight" title="Auto-added by stock tracking">
              🤖
            </span>
          )}
        </div>

        {item.note && (
          <p className={`text-xs mt-0.5 ${done ? 'text-gray-600' : 'text-gray-400'}`}>
            {item.note}
          </p>
        )}
      </div>

      {/* Delete */}
      <button
        onClick={() => onDelete(item.id)}
        className="text-gray-500 hover:text-red-400 transition-colors shrink-0"
        title="Delete"
      >
        🗑️
      </button>
    </li>
  );
}
