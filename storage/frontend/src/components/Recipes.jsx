import { useState, useEffect, useCallback } from 'react';
import {
  getRecipes,
  getRecipe,
  deleteRecipe,
  recipeToShopping,
  recipeImageUrl,
} from '../api';

/* ── Stock status helpers ──────────────────────────────────────────────── */

function StockBadge({ amount, needed }) {
  if (needed <= 0) return <span className="text-emerald-400">✅</span>;
  if (amount <= 0) return <span className="text-red-400">❌</span>;
  if (amount >= needed) return <span className="text-emerald-400">✅</span>;
  return <span className="text-yellow-400">⚠️</span>;
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US');
}

/* ── Recipe card (grid item) ───────────────────────────────────────────── */

function RecipeCard({ recipe, onClick }) {
  return (
    <button
      onClick={onClick}
      className="bg-gray-800 rounded-2xl shadow-lg hover:ring-2 hover:ring-emerald-400 transition text-left flex flex-col overflow-hidden w-full"
    >
      {recipe.picture_filename ? (
        <img
          src={recipeImageUrl(recipe.picture_filename)}
          alt={recipe.name}
          className="w-full h-40 object-cover"
        />
      ) : (
        <div className="w-full h-40 bg-gray-700 flex items-center justify-center text-5xl text-gray-500">
          🍽️
        </div>
      )}

      <div className="p-4 flex flex-col gap-1 flex-1">
        <h3 className="font-semibold text-gray-100 line-clamp-2">{recipe.name}</h3>

        <div className="flex items-center gap-3 text-xs text-gray-400 mt-auto pt-2">
          <span>🍴 {recipe.servings ?? '—'} servings</span>
          <span>📅 {formatDate(recipe.created_at)}</span>
        </div>

        {recipe.source_url && (
          <a
            href={recipe.source_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-xs text-blue-400 hover:underline flex items-center gap-1 mt-1 truncate"
          >
            🔗 Source
          </a>
        )}
      </div>
    </button>
  );
}

/* ── Recipe detail modal ───────────────────────────────────────────────── */

function RecipeDetail({ recipeId, onClose, onDeleted }) {
  const [recipe, setRecipe] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [shopping, setShopping] = useState(false);
  const [shoppingDone, setShoppingDone] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRecipe(recipeId)
      .then(({ data }) => { if (!cancelled) setRecipe(data); })
      .catch((err) => { if (!cancelled) setError(err.message ?? 'Error loading recipe'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [recipeId]);

  const handleShopping = async () => {
    setShopping(true);
    try {
      await recipeToShopping(recipeId);
      setShoppingDone(true);
      setTimeout(() => setShoppingDone(false), 3000);
    } catch {
      alert('Failed to add to shopping list');
    } finally {
      setShopping(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteRecipe(recipeId);
      onDeleted();
    } catch {
      alert('Failed to delete recipe');
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 overflow-auto p-4">
      <div className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-2xl my-8 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="font-bold text-lg truncate">
            {recipe?.name ?? 'Recipe'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-xl leading-none">
            ✕
          </button>
        </div>

        {loading && (
          <div className="p-8 text-center text-gray-400">
            <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3" />
            Loading…
          </div>
        )}

        {error && (
          <div className="p-8 text-center text-red-400">{error}</div>
        )}

        {recipe && !loading && (
          <div className="divide-y divide-gray-700">
            {/* Image + info */}
            <div className="p-5">
              {recipe.picture_filename && (
                <img
                  src={recipeImageUrl(recipe.picture_filename)}
                  alt={recipe.name}
                  className="w-full h-56 object-cover rounded-lg mb-4"
                />
              )}

              {recipe.description && (
                <p className="text-gray-400 text-sm mb-3 whitespace-pre-line">
                  {recipe.description}
                </p>
              )}

              <div className="flex flex-wrap gap-4 text-sm text-gray-400">
                <span>🍴 {recipe.servings ?? '—'} servings</span>
                <span>📅 {formatDate(recipe.created_at)}</span>
                {recipe.source_url && (
                  <a
                    href={recipe.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline flex items-center gap-1"
                  >
                    🔗 Source
                  </a>
                )}
              </div>
            </div>

            {/* Ingredients */}
            <div className="p-5">
              <h3 className="font-semibold text-gray-100 mb-3">
                Ingredients ({recipe.ingredients?.length ?? 0})
              </h3>

              {recipe.ingredients?.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-400 border-b border-gray-700">
                        <th className="pb-2 pr-3">Product</th>
                        <th className="pb-2 pr-3">Amount</th>
                        <th className="pb-2 pr-3 text-center">Stock</th>
                        <th className="pb-2">Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recipe.ingredients
                        .slice()
                        .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
                        .map((ing) => (
                          <tr key={ing.id} className="border-b border-gray-700 last:border-0">
                            <td className="py-2 pr-3 text-gray-100">
                              {ing.product_name ?? `#${ing.product_id}`}
                            </td>
                            <td className="py-2 pr-3 text-gray-400 whitespace-nowrap">
                              {ing.amount != null ? ing.amount : '—'}{' '}
                              {ing.unit_abbreviation ?? ''}
                            </td>
                            <td className="py-2 pr-3 text-center">
                              <StockBadge
                                amount={ing.stock_amount ?? 0}
                                needed={ing.amount ?? 0}
                              />
                            </td>
                            <td className="py-2 text-gray-500 text-xs">
                              {ing.note ?? ''}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-gray-400 text-sm">No ingredients.</p>
              )}
            </div>

            {/* Actions */}
            <div className="p-5 flex flex-wrap gap-3">
              <button
                onClick={handleShopping}
                disabled={shopping}
                className="px-4 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 disabled:opacity-50 text-sm flex items-center gap-2"
              >
                🛒 {shopping ? 'Adding…' : shoppingDone ? 'Added!' : 'Add to shopping list'}
              </button>

              {!confirmDelete ? (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="px-4 py-2 bg-red-600/10 text-red-400 rounded-xl hover:bg-red-600/20 text-sm flex items-center gap-2"
                >
                  🗑️ Delete recipe
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-red-400">Delete?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="px-3 py-1.5 bg-red-600 text-white rounded-xl hover:bg-red-700 disabled:opacity-50 text-sm"
                  >
                    {deleting ? 'Deleting…' : 'Yes'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Recipes component ────────────────────────────────────────────── */

export default function Recipes() {
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState(null);

  const fetchRecipes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await getRecipes();
      setRecipes(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message ?? 'Failed to load recipes');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRecipes(); }, [fetchRecipes]);

  const filtered = recipes.filter((r) =>
    r.name?.toLowerCase().includes(search.toLowerCase()),
  );

  const handleDeleted = () => {
    setSelectedId(null);
    fetchRecipes();
  };

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">🔍</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search recipes…"
            className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
          />
        </div>
        <span className="text-sm text-gray-400">
          {filtered.length} / {recipes.length} recipes
        </span>
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-center py-12 text-gray-400">
          <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3" />
          Loading recipes…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-600/10 text-red-400 rounded-lg p-4 text-sm">
          {error}
          <button onClick={fetchRecipes} className="ml-3 underline">
            Try again
          </button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && filtered.length === 0 && (
        <div className="text-center py-12 text-gray-400">
          {recipes.length === 0
            ? 'No recipes yet.'
            : 'No results found.'}
        </div>
      )}

      {/* Recipe grid */}
      {!loading && filtered.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((r) => (
            <RecipeCard
              key={r.id}
              recipe={r}
              onClick={() => setSelectedId(r.id)}
            />
          ))}
        </div>
      )}

      {/* Detail modal */}
      {selectedId != null && (
        <RecipeDetail
          recipeId={selectedId}
          onClose={() => setSelectedId(null)}
          onDeleted={handleDeleted}
        />
      )}
    </div>
  );
}
