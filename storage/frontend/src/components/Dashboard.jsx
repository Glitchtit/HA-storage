import { useState, useEffect, useCallback } from 'react';
import {
  getProducts,
  getStock,
  getRecipes,
  getShoppingList,
  getBarcodeQueue,
  getTopConsumed,
  getHistory,
} from '../api';

function StatCard({ icon, label, value, accent = 'blue' }) {
  const colors = {
    blue: 'text-emerald-400 bg-emerald-500/20',
    green: 'text-green-400 bg-green-500/20',
    purple: 'text-purple-400 bg-purple-500/20',
    orange: 'text-orange-400 bg-orange-500/20',
  };
  return (
    <div className="bg-gray-800 rounded-lg shadow p-4 flex items-center gap-4">
      <div className={`text-2xl w-12 h-12 rounded-lg flex items-center justify-center ${colors[accent] ?? colors.blue}`}>
        {icon}
      </div>
      <div>
        <p className="text-sm text-gray-400">{label}</p>
        <p className="text-2xl font-bold text-gray-100">{value ?? '—'}</p>
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
  const [topConsumed, setTopConsumed] = useState([]);
  const [recentPurchases, setRecentPurchases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const [productsRes, stockRes, recipesRes, shoppingRes, barcodeRes,
             topConsumedRes, recentPurchasesRes] =
        await Promise.all([
          getProducts(),
          getStock(),
          getRecipes(),
          getShoppingList(),
          getBarcodeQueue({ status: 'pending' }),
          getTopConsumed({ days: 30, limit: 5 }).catch(() => ({ data: [] })),
          getHistory({ event_type: 'purchase', limit: 5 }).catch(() => ({ data: [] })),
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
      setTopConsumed(Array.isArray(topConsumedRes.data) ? topConsumedRes.data : []);
      setRecentPurchases(Array.isArray(recentPurchasesRes.data) ? recentPurchasesRes.data : []);
    } catch (err) {
      console.error('Dashboard fetch error:', err);
      setError('Failed to load data.');
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
          <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3" />
          <p className="text-gray-500">Loading...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-gray-800 border border-red-500/30 rounded-lg p-4 text-red-400 text-center">
        <p>{error}</p>
        <button
          onClick={fetchData}
          className="mt-2 px-4 py-1.5 bg-red-600 text-white rounded hover:bg-red-700 text-sm"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon="📦" label="Products" value={stats?.products} accent="blue" />
        <StatCard icon="🏪" label="In Stock" value={stats?.inStock} accent="green" />
        <StatCard icon="🍽️" label="Recipes" value={stats?.recipes} accent="purple" />
        <StatCard
          icon="🛒"
          label="Shopping List"
          value={stats?.shopping}
          accent="orange"
        />
      </div>

      {/* Pending barcodes badge */}
      {pendingBarcodes > 0 && (
        <div className="bg-emerald-500/20 border border-emerald-500/30 rounded-lg p-4 flex items-center gap-3">
          <span className="text-xl">📱</span>
          <div className="flex-1">
            <p className="font-medium text-emerald-400">Pending Barcodes</p>
            <p className="text-sm text-emerald-400/70">
              {pendingBarcodes} barcodes awaiting identification
            </p>
          </div>
          <span className="bg-emerald-600 text-white text-sm font-bold px-2.5 py-0.5 rounded-full">
            {pendingBarcodes}
          </span>
        </div>
      )}

      {/* Low stock alerts */}
      <section>
        <h2 className="text-lg font-semibold text-gray-100 mb-3">
          ⚠️ Low Stock Alerts
        </h2>
        {lowStock.length === 0 ? (
          <div className="bg-emerald-500/20 border border-emerald-500/30 rounded-lg p-4 text-emerald-400 text-sm">
            All products are sufficiently stocked 👍
          </div>
        ) : (
          <div className="bg-gray-800 rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800/50 text-gray-400 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Product</th>
                  <th className="text-right px-4 py-2 font-medium">Amount</th>
                  <th className="text-right px-4 py-2 font-medium">Minimum</th>
                  <th className="text-right px-4 py-2 font-medium">Deficit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {lowStock.map((item) => {
                  const deficit = item.min_stock_amount - item.amount;
                  const critical = item.amount === 0;
                  return (
                    <tr
                      key={item.product_id}
                      className={critical ? 'bg-red-500/10' : 'bg-orange-500/10'}
                    >
                      <td className="px-4 py-2 font-medium text-gray-100">
                        {item.product_name ?? `#${item.product_id}`}
                      </td>
                      <td className={`px-4 py-2 text-right font-bold ${critical ? 'text-red-400' : 'text-orange-400'}`}>
                        {item.amount}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">
                        {item.min_stock_amount}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${critical ? 'bg-red-500/20 text-red-400' : 'bg-orange-500/20 text-orange-400'}`}>
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
        <h2 className="text-lg font-semibold text-gray-100 mb-3">
          🕐 Expiring Soon
        </h2>
        {expiring.length === 0 ? (
          <div className="bg-emerald-500/20 border border-emerald-500/30 rounded-lg p-4 text-emerald-400 text-sm">
            No products expiring within the next 7 days 👍
          </div>
        ) : (
          <div className="bg-gray-800 rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800/50 text-gray-400 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Product</th>
                  <th className="text-right px-4 py-2 font-medium">Best Before</th>
                  <th className="text-right px-4 py-2 font-medium">Days Left</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {expiring.map((item) => {
                  const expired = item._days <= 0;
                  return (
                    <tr
                      key={`${item.product_id}-${item._bbd}`}
                      className={expired ? 'bg-red-500/10' : 'bg-yellow-500/10'}
                    >
                      <td className="px-4 py-2 font-medium text-gray-100">
                        {item.product_name ?? `#${item.product_id}`}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">
                        {formatDate(item._bbd)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${expired ? 'bg-red-500/20 text-red-400' : 'bg-yellow-500/20 text-yellow-400'}`}>
                          {expired ? `Expired ${Math.abs(item._days)}d ago` : `${item._days}d`}
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

      {/* Top consumed (30d) */}
      <section>
        <h2 className="text-lg font-semibold text-gray-100 mb-3">
          🍴 Top Consumed (30d)
        </h2>
        {topConsumed.length === 0 ? (
          <div className="bg-gray-800 rounded-lg p-4 text-gray-500 text-sm">
            No consume events in the last 30 days.
          </div>
        ) : (
          <div className="bg-gray-800 rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800/50 text-gray-400 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Product</th>
                  <th className="text-right px-4 py-2 font-medium">Amount</th>
                  <th className="text-right px-4 py-2 font-medium">Events</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {topConsumed.map((item) => {
                  const max = topConsumed[0]?.total_amount || 1;
                  const pct = Math.max(4, Math.round((item.total_amount / max) * 100));
                  return (
                    <tr key={item.product_id}>
                      <td className="px-4 py-2 text-gray-100">
                        <div className="font-medium">{item.product_name}</div>
                        <div className="mt-1 h-1.5 rounded-full bg-gray-700 overflow-hidden">
                          <div
                            className="h-full bg-blue-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </td>
                      <td className="px-4 py-2 text-right text-gray-100 font-mono">
                        {item.total_amount}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">
                        {item.event_count}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Recent purchases */}
      <section>
        <h2 className="text-lg font-semibold text-gray-100 mb-3">
          🛒 Recent Purchases
        </h2>
        {recentPurchases.length === 0 ? (
          <div className="bg-gray-800 rounded-lg p-4 text-gray-500 text-sm">
            No purchases recorded yet.
          </div>
        ) : (
          <div className="bg-gray-800 rounded-lg shadow overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-800/50 text-gray-400 text-xs uppercase">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">When</th>
                  <th className="text-left px-4 py-2 font-medium">Product</th>
                  <th className="text-right px-4 py-2 font-medium">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {recentPurchases.map((e) => (
                  <tr key={e.id}>
                    <td className="px-4 py-2 text-gray-400 whitespace-nowrap">
                      {(e.created_at || '').slice(0, 16).replace('T', ' ')}
                    </td>
                    <td className="px-4 py-2 text-gray-100">{e.product_name}</td>
                    <td className="px-4 py-2 text-right text-gray-100 font-mono">{e.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
