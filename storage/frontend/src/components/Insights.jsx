import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  getProducts,
  getTopConsumed,
  getTopPurchased,
  getSpoilage,
  getStatsTimeline,
  getStatsWaste,
  getStatsRunouts,
  getStockEntries,
  getStatsStockValue,
  getStatsPurchaseCosts,
} from '../api';
import BarList from './charts/BarList';
import Sparkline from './charts/Sparkline';
import Timeline from './charts/Timeline';

const WINDOWS = [
  { id: 7, label: '7d' },
  { id: 30, label: '30d' },
  { id: 90, label: '90d' },
  { id: 365, label: '1y' },
];

function fmtEur(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return new Intl.NumberFormat('fi-FI', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 }).format(v);
}

function fmtNum(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  return Number.isInteger(n) ? n.toString() : n.toFixed(1);
}

function Card({ title, subtitle, right, children }) {
  return (
    <section className="bg-gray-800 rounded-xl p-4 shadow">
      <header className="flex items-baseline justify-between mb-3 gap-2">
        <div>
          <h2 className="text-sm font-semibold text-gray-200">{title}</h2>
          {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
        </div>
        {right}
      </header>
      {children}
    </section>
  );
}

export default function Insights() {
  const [days, setDays] = useState(30);
  const [products, setProducts] = useState([]);
  const [topConsumed, setTopConsumed] = useState([]);
  const [topPurchased, setTopPurchased] = useState([]);
  const [spoilage, setSpoilage] = useState([]);
  const [spoilageTimeline, setSpoilageTimeline] = useState([]);
  const [waste, setWaste] = useState(null);
  const [wasteUnavailable, setWasteUnavailable] = useState(false);
  const [runouts, setRunouts] = useState([]);
  const [runoutsUnavailable, setRunoutsUnavailable] = useState(false);
  const [expiringSoon, setExpiringSoon] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [productTimeline, setProductTimeline] = useState([]);
  const [loading, setLoading] = useState(true);

  // Finances
  const [stockValue, setStockValue] = useState(null);
  const [stockValueUnavailable, setStockValueUnavailable] = useState(false);
  const [costs, setCosts] = useState(null);
  const [costsUnavailable, setCostsUnavailable] = useState(false);
  const [costMonth, setCostMonth] = useState(() => {
    const d = new Date();
    return { year: d.getFullYear(), month: d.getMonth() + 1 };
  });

  const fetchCore = useCallback(async () => {
    setLoading(true);
    const [productsRes, tc, tp, sp, st, expRes] = await Promise.all([
      getProducts().catch(() => ({ data: [] })),
      getTopConsumed({ days, limit: 10 }).catch(() => ({ data: [] })),
      getTopPurchased({ days, limit: 10 }).catch(() => ({ data: [] })),
      getSpoilage({ days, limit: 10 }).catch(() => ({ data: [] })),
      getStatsTimeline({ days, event_type: 'spoil' }).catch(() => ({ data: [] })),
      getStockEntries({ expiring_within_days: 7 }).catch(() => ({ data: [] })),
    ]);
    setProducts(productsRes.data ?? []);
    setTopConsumed(tc.data ?? []);
    setTopPurchased(tp.data ?? []);
    setSpoilage(sp.data ?? []);
    setSpoilageTimeline(st.data ?? []);
    setExpiringSoon(Array.isArray(expRes.data) ? expRes.data : []);

    const wasteRes = await getStatsWaste({ days }).catch((err) => ({ error: err }));
    if (wasteRes.error || !wasteRes.data) {
      setWasteUnavailable(true);
      setWaste(null);
    } else {
      setWasteUnavailable(false);
      setWaste(wasteRes.data);
    }

    const runoutsRes = await getStatsRunouts({ horizon: 14 }).catch((err) => ({ error: err }));
    if (runoutsRes.error || !runoutsRes.data) {
      setRunoutsUnavailable(true);
      setRunouts([]);
    } else {
      setRunoutsUnavailable(false);
      setRunouts(runoutsRes.data?.runouts ?? runoutsRes.data ?? []);
    }
    setLoading(false);
  }, [days]);

  useEffect(() => { fetchCore(); }, [fetchCore]);

  useEffect(() => {
    if (selectedProduct == null) {
      setProductTimeline([]);
      return;
    }
    getStatsTimeline({ days, product_id: selectedProduct })
      .then((r) => setProductTimeline(r.data ?? []))
      .catch(() => setProductTimeline([]));
  }, [selectedProduct, days]);

  useEffect(() => {
    getStatsStockValue()
      .then((r) => { setStockValue(r.data); setStockValueUnavailable(false); })
      .catch(() => setStockValueUnavailable(true));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getStatsPurchaseCosts({ year: costMonth.year, month: costMonth.month })
      .then((r) => { if (!cancelled) { setCosts(r.data); setCostsUnavailable(false); } })
      .catch(() => { if (!cancelled) setCostsUnavailable(true); });
    return () => { cancelled = true; };
  }, [costMonth]);

  const consumedItems = useMemo(
    () => topConsumed.map((r) => ({ key: r.product_id, label: r.product_name, value: r.total_amount })),
    [topConsumed]
  );
  const purchasedItems = useMemo(
    () => topPurchased.map((r) => ({ key: r.product_id, label: r.product_name, value: r.total_amount })),
    [topPurchased]
  );
  const spoilageItems = useMemo(
    () => spoilage.map((r) => ({ key: r.product_id, label: r.product_name, value: r.total_amount })),
    [spoilage]
  );

  const wasteByProduct = useMemo(
    () => (waste?.by_product ?? []).slice(0, 10).map((r) => ({
      key: r.product_id,
      label: r.product_name,
      value: r.value,
    })),
    [waste]
  );
  const wasteByLocation = useMemo(
    () => (waste?.by_location ?? []).map((r) => ({
      key: r.location_id ?? r.location_name,
      label: r.location_name ?? 'Unknown',
      value: r.value,
    })),
    [waste]
  );
  const wasteSeries = useMemo(
    () => (waste?.series ?? []).map((p) => ({ day: p.week, value: p.value })),
    [waste]
  );

  const stockValueGroups = useMemo(
    () => (stockValue?.by_group ?? []).map((g) => ({
      key: g.group_id ?? 'ungrouped', label: g.group_name, value: g.value,
    })),
    [stockValue]
  );
  const costProducts = useMemo(
    () => (costs?.by_product ?? []).map((p) => ({
      key: p.product_id, label: p.product_name, value: p.value,
    })),
    [costs]
  );
  const costSeries = useMemo(
    () => (costs?.series ?? []).map((p) => ({ day: p.month, value: p.value })),
    [costs]
  );

  const today = new Date();
  const isCurrentMonth =
    costMonth.year === today.getFullYear() && costMonth.month === today.getMonth() + 1;
  const shiftMonth = (delta) => {
    setCostMonth(({ year, month }) => {
      let m = month + delta;
      let y = year;
      if (m === 0) { m = 12; y -= 1; }
      if (m === 13) { m = 1; y += 1; }
      return { year: y, month: m };
    });
  };
  const monthLabel = new Date(costMonth.year, costMonth.month - 1, 1)
    .toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });

  return (
    <div className="space-y-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-bold">📈 Insights</h1>
      </div>

      {/* Finances */}
      <div className="grid md:grid-cols-2 gap-4">
        <Card title="🏦 Stock value" subtitle="What everything on hand is worth right now.">
          {stockValueUnavailable ? (
            <p className="text-sm text-gray-500">/api/stats/stock-value not available on this backend yet.</p>
          ) : stockValue ? (
            <div>
              <p className="text-3xl font-bold text-brand-cobalt">{fmtEur(stockValue.total_value)}</p>
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-2">By product group</p>
                <BarList
                  items={stockValueGroups}
                  color="var(--brand-cobalt)"
                  formatValue={fmtEur}
                  emptyLabel="No priced stock."
                />
              </div>
              {stockValue.unpriced_amount > 0 && (
                <p className="text-xs text-gray-500 mt-2">
                  {fmtNum(stockValue.unpriced_amount)} units have no price and are excluded.
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading…</p>
          )}
        </Card>
        <Card
          title="🧾 Monthly costs"
          subtitle="Spend from recorded purchases."
          right={
            <div className="flex items-center gap-1 text-sm">
              <button
                onClick={() => shiftMonth(-1)}
                className="px-2 py-0.5 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-700"
                aria-label="Previous month"
              >
                ‹
              </button>
              <span className="text-gray-200 tabular-nums whitespace-nowrap">{monthLabel}</span>
              <button
                onClick={() => shiftMonth(1)}
                disabled={isCurrentMonth}
                className="px-2 py-0.5 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-700 disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400"
                aria-label="Next month"
              >
                ›
              </button>
            </div>
          }
        >
          {costsUnavailable ? (
            <p className="text-sm text-gray-500">/api/stats/purchase-costs not available on this backend yet.</p>
          ) : costs ? (
            <div>
              <p className="text-3xl font-bold text-brand-orange">{fmtEur(costs.total_value)}</p>
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-1">12-month trend</p>
                <Sparkline data={costSeries} color="var(--brand-orange)" formatValue={fmtEur} />
              </div>
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-2">Top products</p>
                <BarList
                  items={costProducts}
                  color="var(--brand-orange)"
                  formatValue={fmtEur}
                  onClick={(it) => setSelectedProduct(it.key)}
                  emptyLabel="No purchases this month."
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading…</p>
          )}
        </Card>
      </div>

      {/* Waste headline */}
      <Card
        title="💸 Waste this window"
        subtitle={wasteUnavailable
          ? 'Add unit prices to products to enable monetary waste tracking.'
          : `Spoiled value across the last ${days} days.`}
        right={
          <div className="inline-flex rounded-lg bg-gray-900/60 p-1 shrink-0">
            {WINDOWS.map((w) => (
              <button
                key={w.id}
                onClick={() => setDays(w.id)}
                className={`px-3 py-1 text-sm rounded-md transition-colors ${
                  days === w.id
                    ? 'bg-brand-cobalt text-white'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
        }
      >
        {wasteUnavailable ? (
          <p className="text-sm text-gray-500">/api/stats/waste not available on this backend yet.</p>
        ) : waste ? (
          <div className="grid md:grid-cols-3 gap-4">
            <div>
              <p className="text-3xl font-bold text-brand-orange">{fmtEur(waste.total_value)}</p>
              <p className="text-xs text-gray-500 mt-1">
                {waste.total_value > 0
                  ? `Across ${wasteByProduct.length} product${wasteByProduct.length === 1 ? '' : 's'}.`
                  : 'No tracked spoilage in this window. Nice.'}
              </p>
              <div className="mt-3">
                <p className="text-xs text-gray-500 mb-1">Weekly trend</p>
                <Sparkline
                  data={wasteSeries}
                  color="var(--brand-orange)"
                  formatValue={fmtEur}
                />
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-2">By product</p>
              <BarList items={wasteByProduct} color="var(--brand-orange)" formatValue={fmtEur} emptyLabel="No spoilage" />
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-2">By location</p>
              <BarList items={wasteByLocation} color="var(--brand-cobalt)" formatValue={fmtEur} emptyLabel="No spoilage" />
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">{loading ? 'Loading…' : 'No data.'}</p>
        )}
      </Card>

      {/* Predictive + Expiring */}
      <div className="grid md:grid-cols-2 gap-4">
        <Card
          title="⏳ Will run out next 14 days"
          subtitle={runoutsUnavailable ? 'Backend endpoint not available yet.' : 'Based on consumption velocity.'}
        >
          {runoutsUnavailable ? (
            <p className="text-sm text-gray-500">/api/stats/runouts not available on this backend yet.</p>
          ) : runouts.length === 0 ? (
            <p className="text-sm text-gray-500">Nothing predicted to deplete soon.</p>
          ) : (
            <ul className="space-y-2">
              {runouts.slice(0, 10).map((r) => (
                <li
                  key={r.product_id}
                  className="flex items-baseline justify-between text-sm cursor-pointer hover:bg-gray-700/40 rounded px-2 py-1 -mx-2"
                  onClick={() => setSelectedProduct(r.product_id)}
                >
                  <span className="text-gray-200 truncate pr-2">{r.product_name}</span>
                  <span className="text-brand-orange tabular-nums shrink-0">
                    {r.days_to_runout != null ? `${fmtNum(r.days_to_runout)} d` : '—'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card
          title="🥬 Expiring this week"
          subtitle="Lots whose best-before is within 7 days (or already past)."
        >
          {expiringSoon.length === 0 ? (
            <p className="text-sm text-gray-500">Nothing expiring soon. 👍</p>
          ) : (
            <ul className="space-y-2 max-h-72 overflow-auto">
              {expiringSoon.slice(0, 15).map((lot) => {
                const dl = lot.best_before_date
                  ? Math.ceil(
                      (new Date(lot.best_before_date + 'T00:00:00') - Date.now()) / 86_400_000,
                    )
                  : null;
                const tone = dl == null ? 'text-gray-400' : dl < 0 ? 'text-red-400' : dl <= 2 ? 'text-brand-orange' : 'text-yellow-300';
                return (
                  <li
                    key={lot.id}
                    className="flex items-baseline justify-between text-sm cursor-pointer hover:bg-gray-700/40 rounded px-2 py-1 -mx-2"
                    onClick={() => setSelectedProduct(lot.product_id)}
                  >
                    <span className="text-gray-200 truncate pr-2">
                      {lot.product_name} <span className="text-xs text-gray-500">· {fmtNum(lot.amount)}</span>
                    </span>
                    <span className={`tabular-nums shrink-0 ${tone}`}>
                      {dl == null ? '—' : dl < 0 ? `${Math.abs(dl)}d ago` : `${dl}d`}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      {/* Top consumed + purchased */}
      <div className="grid md:grid-cols-2 gap-4">
        <Card title="🥄 Top consumed" subtitle={`Last ${days} days, by amount.`}>
          <BarList
            items={consumedItems}
            color="var(--brand-cobalt)"
            formatValue={fmtNum}
            onClick={(it) => setSelectedProduct(it.key)}
            emptyLabel="No consumption recorded."
          />
        </Card>
        <Card title="🛒 Top purchased" subtitle={`Last ${days} days, by amount.`}>
          <BarList
            items={purchasedItems}
            color="var(--brand-orange)"
            formatValue={fmtNum}
            onClick={(it) => setSelectedProduct(it.key)}
            emptyLabel="No purchases recorded."
          />
        </Card>
      </div>

      {/* Spoilage breakdown */}
      <Card title="🗑️ Spoilage" subtitle={`Units spoiled in the last ${days} days.`}>
        <div className="grid md:grid-cols-2 gap-6">
          <BarList
            items={spoilageItems}
            color="var(--danger)"
            formatValue={fmtNum}
            onClick={(it) => setSelectedProduct(it.key)}
            emptyLabel="Nothing spoiled in this window. 👍"
          />
          <div>
            <p className="text-xs text-gray-500 mb-1">Daily trend</p>
            <Sparkline data={spoilageTimeline} valueKey="amount" color="var(--danger)" formatValue={fmtNum} />
          </div>
        </div>
      </Card>

      {/* Product timeline drill-down */}
      <Card
        title="🔍 Product timeline"
        subtitle="Inspect a single product's events over the window."
        right={
          <select
            className="bg-gray-700 text-gray-100 rounded-md px-2 py-1 text-sm border border-gray-600 max-w-xs"
            value={selectedProduct ?? ''}
            onChange={(e) => setSelectedProduct(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">— pick a product —</option>
            {products.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        }
      >
        {selectedProduct == null ? (
          <p className="text-sm text-gray-500">Pick a product (or click anywhere above) to see its event timeline.</p>
        ) : (
          <Timeline data={productTimeline} />
        )}
      </Card>
    </div>
  );
}
