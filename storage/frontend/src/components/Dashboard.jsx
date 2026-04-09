import { useState, useEffect, useCallback } from 'react';
import {
  getProducts,
  getStock,
  getRecipes,
  getShoppingList,
  getBarcodeQueue,
} from '../api';

function StatCard({ icon, label, value, accent = 'blue' }) {
  const colors = {
    blue: 'text-blue-600 bg-blue-50',
    green: 'text-green-600 bg-green-50',
    purple: 'text-purple-600 bg-purple-50',
    orange: 'text-orange-600 bg-orange-50',
  };
  return (
    <div className="bg-white rounded-lg shadow p-4 flex items-center gap-4">
      <div className={`text-2xl w-12 h-12 rounded-lg flex items-center justify-center ${colors[accent] ?? colors.blue}`}>
        {icon}
      </div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{value ?? '—'}</p>
      </div>
    </div>
  );
}

function daysUntil(dateStr) {
  if (!dateStr || dateStr === '2999-12-31') return Infinity;
  const target = new Date(dateStr + 'T00:00:00');
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.ceil((target - now) / 86_400_000);
}

function formatDate(dateStr) {
  if (!dateStr || dateStr === '2999-12-31') return '—';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('fi-FI');
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [lowStock, setLowStock] = useState([]);
  const [expiring, setExpiring] = useState([]);
  const [pendingBarcodes, setPendingBarcodes] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const [productsRes, stockRes, recipesRes, shoppingRes, barcodeRes] =
        await Promise.all([
          getProducts(),
          getStock(),
          getRecipes(),
          getShoppingList(),
          getBarcodeQueue({ status: 'pending' }),
        ]);

      const products = productsRes.data;
      const stock = stockRes.data;
      const recipes = recipesRes.data;
      const shopping = shoppingRes.data;
      const barcodes = barcodeRes.data;

      // Stats
      const inStockCount = Array.isArray(stock)
        ? stock.filter((s) => s.amount > 0).length
        : 0;
      const pendingShoppingCount = Array.isArray(shopping)
        ? shopping.filter((s) => !s.done).length
        : 0;

      setStats({
        products: Array.isArray(products) ? products.length : 0,
        inStock: inStockCount,
        recipes: Array.isArray(recipes) ? recipes.length : 0,
        shopping: pendingShoppingCount,
      });

      // Low stock: amount < min_stock_amount where min_stock_amount > 0
      if (Array.isArray(stock)) {
        const low = stock
          .filter((s) => s.min_stock_amount > 0 && s.amount < s.min_stock_amount)
          .sort((a, b) => a.amount / a.min_stock_amount - b.amount / b.min_stock_amount);
        setLowStock(low);
      }

      // Expiring soon: stock entries with best_before_date within 7 days
      if (Array.isArray(stock)) {
        const soon = stock
          .filter((s) => {
            const bbd = s.best_before_date ?? s.product?.best_before_date;
            if (!bbd || bbd === '2999-12-31') return false;
            return daysUntil(bbd) <= 7;
          })
          .map((s) => {
            const bbd = s.best_before_date ?? s.product?.best_before_date;
            return { ...s, _bbd: bbd, _days: daysUntil(bbd) };
          })
          .sort((a, b) => a._days - b._days);
        setExpiring(soon);
      }

      setPendingBarcodes(Array.isArray(barcodes) ? barcodes.length : 0);
    } catch (err) {
      console.error('Dashboard fetch error:', err);
      setError('Tietojen lataus epäonnistui.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-3" />
          <p className="text-gray-500">Ladataan...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-center">
        <p>{error}</p>
        <button
          onClick={fetchData}
          className="mt-2 px-4 py-1.5 bg-red-600 text-white rounded hover:bg-red-700 text-sm"
        >
          Yritä uudelleen
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon="📦" label="Tuotteet" value={stats?.products} accent="blue" />
        <StatCard icon="🏪" label="Varastossa" value={stats?.inStock} accent="green" />
        <StatCard icon="🍽️" label="Reseptit" value={stats?.recipes} accent="purple" />
        <StatCard
          icon="🛒"
          label="Ostoslista"
          value={stats?.shopping}
          accent="orange"
        />
      </div>

      {/* Pending barcodes badge */}
      {pendingBarcodes > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 flex items-center gap-3">
          <span className="text-xl">📱</span>
          <div className="flex-1">
            <p className="font-medium text-blue-900">Odottavia viivakoodeja</p>
            <p className="text-sm text-blue-700">
              {pendingBarcodes} viivakoodia odottaa tunnistusta
            </p>
          </div>
          <span className="bg-blue-600 text-white text-sm font-bold px-2.5 py-0.5 rounded-full">
            {pendingBarcodes}
          </span>
        </div>
      )}

      {/* Low stock alerts */}
      <section>
        <h2 className="text-lg font-semibold text-gray-900 mb-3">
          ⚠️ Vähissä olevat tuotteet
        </h2>
        {lowStock.length === 0 ? (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-green-700 text-sm">
            Kaikki tuotteet ovat riittävällä tasolla 👍
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-orange-50 text-orange-800">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Tuote</th>
                  <th className="text-right px-4 py-2 font-medium">Määrä</th>
                  <th className="text-right px-4 py-2 font-medium">Minimi</th>
                  <th className="text-right px-4 py-2 font-medium">Puuttuu</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {lowStock.map((item) => {
                  const deficit = item.min_stock_amount - item.amount;
                  const critical = item.amount === 0;
                  return (
                    <tr
                      key={item.product_id}
                      className={critical ? 'bg-red-50' : 'bg-orange-50/30'}
                    >
                      <td className="px-4 py-2 font-medium text-gray-900">
                        {item.product_name ?? `#${item.product_id}`}
                      </td>
                      <td className={`px-4 py-2 text-right font-bold ${critical ? 'text-red-600' : 'text-orange-600'}`}>
                        {item.amount}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-500">
                        {item.min_stock_amount}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${critical ? 'bg-red-100 text-red-700' : 'bg-orange-100 text-orange-700'}`}>
                          −{deficit}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Expiring soon */}
      <section>
        <h2 className="text-lg font-semibold text-gray-900 mb-3">
          🕐 Vanhenemassa pian
        </h2>
        {expiring.length === 0 ? (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-green-700 text-sm">
            Ei vanhentuvia tuotteita seuraavan 7 päivän aikana 👍
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-yellow-50 text-yellow-800">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Tuote</th>
                  <th className="text-right px-4 py-2 font-medium">Parasta ennen</th>
                  <th className="text-right px-4 py-2 font-medium">Päiviä jäljellä</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {expiring.map((item) => {
                  const expired = item._days <= 0;
                  return (
                    <tr
                      key={`${item.product_id}-${item._bbd}`}
                      className={expired ? 'bg-red-50' : 'bg-yellow-50/30'}
                    >
                      <td className="px-4 py-2 font-medium text-gray-900">
                        {item.product_name ?? `#${item.product_id}`}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-600">
                        {formatDate(item._bbd)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${expired ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700'}`}>
                          {expired ? `Vanhentunut ${Math.abs(item._days)} pv sitten` : `${item._days} pv`}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
