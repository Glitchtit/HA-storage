import { useState, useEffect, useCallback } from 'react';
import {
  getProducts,
  getProduct,
  createProduct,
  updateProduct,
  deleteProduct,
  createBarcode,
  deleteBarcode,
  getProductGroups,
  getLocations,
  getUnits,
  productImageUrl,
} from '../api';

/* ── helpers ────────────────────────────────────────────────────────────── */

const EMPTY_FORM = {
  name: '',
  description: '',
  parent_id: null,
  location_id: null,
  product_group_id: null,
  unit_id: null,
  default_best_before_days: 0,
  min_stock_amount: 0,
};

const lookup = (list, id) => list.find((i) => i.id === id);

/* ── Modal shell ────────────────────────────────────────────────────────── */

function Modal({ children, onClose }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

/* ── Confirm dialog ─────────────────────────────────────────────────────── */

function Confirm({ message, onYes, onNo }) {
  return (
    <Modal onClose={onNo}>
      <div className="p-6">
        <p className="mb-6 text-gray-400">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onNo}
            className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-400 hover:text-gray-200 hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            onClick={onYes}
            className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700"
          >
            Delete
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ── Product form (shared by create & edit) ─────────────────────────────── */

function ProductForm({ form, setForm, groups, locations, units, products }) {
  const field = (key, val) => setForm((f) => ({ ...f, [key]: val }));

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {/* Name */}
      <div className="sm:col-span-2">
        <label className="block text-sm font-medium text-gray-400 mb-1">Name *</label>
        <input
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 focus:outline-none"
          value={form.name}
          onChange={(e) => field('name', e.target.value)}
          placeholder="Product name"
        />
      </div>

      {/* Description */}
      <div className="sm:col-span-2">
        <label className="block text-sm font-medium text-gray-400 mb-1">Description</label>
        <textarea
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 focus:outline-none"
          rows={2}
          value={form.description ?? ''}
          onChange={(e) => field('description', e.target.value)}
          placeholder="Optional description"
        />
      </div>

      {/* Parent product */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Parent Product</label>
        <select
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={form.parent_id ?? ''}
          onChange={(e) => field('parent_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— no parent —</option>
          {products.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </div>

      {/* Group */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Group</label>
        <select
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={form.product_group_id ?? ''}
          onChange={(e) => field('product_group_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— no group —</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
        </select>
      </div>

      {/* Location */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Location</label>
        <select
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={form.location_id ?? ''}
          onChange={(e) => field('location_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— no location —</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>
      </div>

      {/* Unit */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Unit</label>
        <select
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={form.unit_id ?? ''}
          onChange={(e) => field('unit_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— no unit —</option>
          {units.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}{u.abbreviation ? ` (${u.abbreviation})` : ''}
            </option>
          ))}
        </select>
      </div>

      {/* Best before days */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Best Before (days)</label>
        <input
          type="number"
          min={0}
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 focus:outline-none"
          value={form.default_best_before_days ?? 0}
          onChange={(e) => field('default_best_before_days', Number(e.target.value))}
        />
      </div>

      {/* Min stock */}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">Min Stock</label>
        <input
          type="number"
          min={0}
          step="any"
          className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-emerald-500 focus:outline-none"
          value={form.min_stock_amount ?? 0}
          onChange={(e) => field('min_stock_amount', Number(e.target.value))}
        />
      </div>
    </div>
  );
}

/* ── Create modal ───────────────────────────────────────────────────────── */

function CreateModal({ onClose, onCreated, groups, locations, units, products }) {
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      await createProduct(form);
      onCreated();
      onClose();
    } catch (err) {
      console.error('Create product failed', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal onClose={onClose}>
      <div className="p-6">
        <h2 className="text-lg font-semibold text-gray-100 mb-4">➕ New Product</h2>
        <ProductForm
          form={form}
          setForm={setForm}
          groups={groups}
          locations={locations}
          units={units}
          products={products}
        />
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-400 hover:text-gray-200 hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !form.name.trim()}
            className="px-4 py-2 text-sm rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Create Product'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

/* ── Barcode section inside detail panel ────────────────────────────────── */

function BarcodeSection({ barcodes, productId, units, onChanged }) {
  const [newBarcode, setNewBarcode] = useState('');
  const [packSize, setPackSize] = useState(1);
  const [packUnitId, setPackUnitId] = useState('');
  const [adding, setAdding] = useState(false);

  const handleAdd = async () => {
    if (!newBarcode.trim()) return;
    setAdding(true);
    try {
      await createBarcode({
        barcode: newBarcode.trim(),
        product_id: productId,
        pack_size: packSize,
        pack_unit_id: packUnitId ? Number(packUnitId) : null,
      });
      setNewBarcode('');
      setPackSize(1);
      setPackUnitId('');
      onChanged();
    } catch (err) {
      console.error('Add barcode failed', err);
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteBarcode(id);
      onChanged();
    } catch (err) {
      console.error('Delete barcode failed', err);
    }
  };

  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-400 mb-2">Barcodes</h4>
      {barcodes.length > 0 ? (
        <ul className="space-y-1 mb-3">
          {barcodes.map((bc) => {
            const packUnit = bc.pack_unit_id ? lookup(units, bc.pack_unit_id) : null;
            return (
              <li key={bc.id} className="flex items-center justify-between bg-gray-700/50 rounded px-3 py-1.5 text-sm">
                <span className="font-mono text-gray-100">{bc.barcode}</span>
                <span className="text-gray-500 text-xs mx-2">
                  {bc.pack_size > 1 && `${bc.pack_size}×`}
                  {packUnit ? packUnit.abbreviation || packUnit.name : ''}
                </span>
                <button
                  onClick={() => handleDelete(bc.id)}
                  className="text-red-400 hover:text-red-600 text-xs"
                  title="Delete"
                >
                  🗑️
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-xs text-gray-400 mb-3">No barcodes</p>
      )}

      {/* Add form */}
      <div className="flex gap-2 items-end flex-wrap">
        <div className="flex-1 min-w-[140px]">
          <label className="block text-xs text-gray-500 mb-0.5">Barcode</label>
          <input
            className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded px-2 py-1 text-sm"
            value={newBarcode}
            onChange={(e) => setNewBarcode(e.target.value)}
            placeholder="Barcode"
          />
        </div>
        <div className="w-20">
          <label className="block text-xs text-gray-500 mb-0.5">Pack Size</label>
          <input
            type="number"
            min={1}
            className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded px-2 py-1 text-sm"
            value={packSize}
            onChange={(e) => setPackSize(Number(e.target.value))}
          />
        </div>
        <div className="w-28">
          <label className="block text-xs text-gray-500 mb-0.5">Unit</label>
          <select
            className="w-full bg-gray-700 border border-gray-600 text-gray-100 rounded px-2 py-1 text-sm"
            value={packUnitId}
            onChange={(e) => setPackUnitId(e.target.value)}
          >
            <option value="">—</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.abbreviation || u.name}
              </option>
            ))}
          </select>
        </div>
        <button
          onClick={handleAdd}
          disabled={adding || !newBarcode.trim()}
          className="px-3 py-1 text-sm rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          Add
        </button>
      </div>
    </div>
  );
}

/* ── Detail / edit panel ────────────────────────────────────────────────── */

function DetailPanel({ product, groups, locations, units, products, onClose, onSaved }) {
  const [detail, setDetail] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await getProduct(product.id);
      setDetail(data);
      setForm({
        name: data.name,
        description: data.description ?? '',
        parent_id: data.parent_id,
        location_id: data.location_id,
        product_group_id: data.product_group_id,
        unit_id: data.unit_id,
        default_best_before_days: data.default_best_before_days ?? 0,
        min_stock_amount: data.min_stock_amount ?? 0,
      });
    } catch (err) {
      console.error('Load product failed', err);
    }
  }, [product.id]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateProduct(product.id, form);
      setEditing(false);
      onSaved();
      load();
    } catch (err) {
      console.error('Update product failed', err);
    } finally {
      setSaving(false);
    }
  };

  if (!detail || !form) {
    return (
      <tr>
        <td colSpan={8} className="px-4 py-6 text-center text-gray-400 text-sm">
          Loading…
        </td>
      </tr>
    );
  }

  const group = detail.product_group_id ? lookup(groups, detail.product_group_id) : null;
  const loc = detail.location_id ? lookup(locations, detail.location_id) : null;
  const unit = detail.unit_id ? lookup(units, detail.unit_id) : null;
  const parentProductOptions = products.filter((p) => p.id !== product.id);

  return (
    <tr>
      <td colSpan={8} className="px-0 py-0">
        <div className="bg-gray-700/30 border-t border-b border-gray-700 px-6 py-5">
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-4">
              {detail.picture_filename ? (
                <img
                  src={productImageUrl(detail.picture_filename)}
                  alt={detail.name}
                  className="w-16 h-16 rounded-lg object-cover border border-gray-700"
                />
              ) : (
                <div className="w-16 h-16 rounded-lg bg-gray-700 flex items-center justify-center text-2xl">
                  📦
                </div>
              )}
              <div>
                <h3 className="font-semibold text-lg text-gray-100">{detail.name}</h3>
                {detail.description && (
                  <p className="text-sm text-gray-400">{detail.description}</p>
                )}
                <div className="flex gap-4 mt-1 text-xs text-gray-400">
                  {group && <span>Group: {group.name}</span>}
                  {loc && <span>Location: {loc.name}</span>}
                  {unit && <span>Unit: {unit.abbreviation || unit.name}</span>}
                </div>
              </div>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-xl">✕</button>
          </div>

          {/* Stock info */}
          <div className="flex gap-6 mb-4 text-sm text-gray-300">
            <span>
              In stock: <strong>{detail.stock_amount ?? 0}</strong>
              {unit && ` ${unit.abbreviation || unit.name}`}
            </span>
            {(detail.stock_opened ?? 0) > 0 && (
              <span>Opened: <strong>{detail.stock_opened}</strong></span>
            )}
            <span>Best before: <strong>{detail.default_best_before_days ?? 0}</strong> days</span>
            <span>Min: <strong>{detail.min_stock_amount ?? 0}</strong></span>
          </div>

          {/* Edit form */}
          {editing ? (
            <div className="bg-gray-800 rounded-lg p-4 mb-4 border border-gray-600">
              <ProductForm
                form={form}
                setForm={setForm}
                groups={groups}
                locations={locations}
                units={units}
                products={parentProductOptions}
              />
              <div className="flex justify-end gap-3 mt-4">
                <button
                  onClick={() => { setEditing(false); load(); }}
                  className="px-4 py-2 text-sm rounded-lg border border-gray-600 text-gray-400 hover:text-gray-200 hover:bg-gray-700"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || !form.name.trim()}
                  className="px-4 py-2 text-sm rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setEditing(true)}
              className="text-sm text-emerald-400 hover:text-emerald-300 mb-4 inline-block"
            >
              ✏️ Edit details
            </button>
          )}

          {/* Barcodes */}
          <div className="bg-gray-800 rounded-lg p-4 mb-4 border border-gray-600">
            <BarcodeSection
              barcodes={detail.barcodes ?? []}
              productId={product.id}
              units={units}
              onChanged={load}
            />
          </div>

          {/* Children */}
          {detail.children && detail.children.length > 0 && (
            <div className="bg-gray-800 rounded-lg p-4 border border-gray-600">
              <h4 className="text-sm font-semibold text-gray-400 mb-2">
                Sub-products ({detail.children.length})
              </h4>
              <ul className="space-y-1">
                {detail.children.map((c) => (
                  <li key={c.id} className="flex items-center gap-2 text-sm text-gray-300 py-1">
                    <span className="text-gray-500">└</span>
                    {c.picture_filename ? (
                      <img
                        src={productImageUrl(c.picture_filename)}
                        alt={c.name}
                        className="w-6 h-6 rounded object-cover"
                      />
                    ) : (
                      <span className="text-base">📦</span>
                    )}
                    <span>{c.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

/* ── Product image thumbnail ────────────────────────────────────────────── */

function Thumb({ filename }) {
  if (!filename) {
    return (
      <div className="w-10 h-10 rounded bg-gray-700 flex items-center justify-center text-lg shrink-0 text-gray-500">
        📦
      </div>
    );
  }
  return (
    <img
      src={productImageUrl(filename)}
      alt=""
      className="w-10 h-10 rounded object-cover shrink-0 border border-gray-700"
      loading="lazy"
    />
  );
}

/* ── Main component ─────────────────────────────────────────────────────── */

export default function Products() {
  /* ─ state ─ */
  const [products, setProducts] = useState([]);
  const [groups, setGroups] = useState([]);
  const [locations, setLocations] = useState([]);
  const [units, setUnits] = useState([]);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState('');
  const [filterGroup, setFilterGroup] = useState('');
  const [filterLocation, setFilterLocation] = useState('');
  const [topOnly, setTopOnly] = useState(false);

  const [expandedId, setExpandedId] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [error, setError] = useState(null);

  /* ─ data loading ─ */
  const loadRef = useCallback(async () => {
    try {
      const [g, l, u] = await Promise.all([
        getProductGroups(),
        getLocations(),
        getUnits(),
      ]);
      setGroups(g.data);
      setLocations(l.data);
      setUnits(u.data);
    } catch (err) {
      console.error('Load reference data failed', err);
    }
  }, []);

  const loadProducts = useCallback(async () => {
    try {
      const params = {};
      if (topOnly) params.parent_id = 'null';
      if (filterGroup) params.group_id = filterGroup;
      const { data } = await getProducts(params);
      setProducts(data);
    } catch (err) {
      console.error('Load products failed', err);
    } finally {
      setLoading(false);
    }
  }, [topOnly, filterGroup]);

  useEffect(() => { loadRef(); }, [loadRef]);
  useEffect(() => { loadProducts(); }, [loadProducts]);

  /* ─ derived data ─ */
  const filtered = products.filter((p) => {
    if (search) {
      const q = search.toLowerCase();
      if (!p.name.toLowerCase().includes(q)) return false;
    }
    if (filterLocation && p.location_id !== Number(filterLocation)) return false;
    return true;
  });

  // Build parent → children map for tree display
  const childrenMap = {};
  for (const p of filtered) {
    if (p.parent_id) {
      (childrenMap[p.parent_id] ||= []).push(p);
    }
  }

  const topLevel = filtered.filter((p) => !p.parent_id);

  // Flatten into display rows: parent, then indented children
  const displayRows = [];
  for (const p of topLevel) {
    displayRows.push({ ...p, indent: 0 });
    const children = childrenMap[p.id];
    if (children) {
      for (const c of children) {
        displayRows.push({ ...c, indent: 1 });
      }
    }
  }
  // Also show orphan children (parent not in current filtered set)
  const shownIds = new Set(displayRows.map((r) => r.id));
  for (const p of filtered) {
    if (!shownIds.has(p.id)) {
      displayRows.push({ ...p, indent: 1 });
    }
  }

  /* ─ handlers ─ */
  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      await deleteProduct(confirmDelete.id);
      setConfirmDelete(null);
      setExpandedId(null);
      loadProducts();
    } catch (err) {
      console.error('Delete product failed', err);
      setConfirmDelete(null);
      setError(err.response?.data?.detail || 'Failed to delete product');
    }
  };

  const groupName = (id) => lookup(groups, id)?.name ?? '';
  const locName = (id) => lookup(locations, id)?.name ?? '';
  const unitAbbr = (id) => {
    const u = lookup(units, id);
    return u ? (u.abbreviation || u.name) : '';
  };

  /* ─ render ─ */
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin h-8 w-8 border-4 border-emerald-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <h2 className="text-xl font-bold text-gray-100">📦 Products</h2>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="🔍 Search products…"
            className="bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-2 text-sm w-56 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 text-sm rounded-xl bg-emerald-600 text-white hover:bg-emerald-700 whitespace-nowrap"
          >
            ➕ Add Product
          </button>
        </div>
      </div>

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          className="bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={filterGroup}
          onChange={(e) => setFilterGroup(e.target.value)}
        >
          <option value="">All groups</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
        </select>

        <select
          className="bg-gray-700 border border-gray-600 text-gray-100 rounded-lg px-3 py-2 text-sm"
          value={filterLocation}
          onChange={(e) => setFilterLocation(e.target.value)}
        >
          <option value="">All locations</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>

        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={topOnly}
            onChange={(e) => setTopOnly(e.target.checked)}
            className="rounded border-gray-600 text-emerald-600 focus:ring-emerald-500 bg-gray-700"
          />
          Top-level products only
        </label>

        <span className="text-xs text-gray-400 ml-auto">
          {filtered.length} products
        </span>
      </div>

      {/* Products table */}
      <div className="bg-gray-800 rounded-xl shadow overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-700/50 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">
                <th className="px-4 py-3 w-14">Image</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3 hidden md:table-cell">Group</th>
                <th className="px-4 py-3 hidden md:table-cell">Location</th>
                <th className="px-4 py-3 hidden sm:table-cell">Unit</th>
                <th className="px-4 py-3 text-right">Stock</th>
                <th className="px-4 py-3 text-right hidden lg:table-cell">Best Before</th>
                <th className="px-4 py-3 w-24 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {displayRows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-gray-400">
                    No products
                  </td>
                </tr>
              )}
              {displayRows.map((p, idx) => (
                <ProductRow
                  key={p.id}
                  product={p}
                  idx={idx}
                  expandedId={expandedId}
                  setExpandedId={setExpandedId}
                  setConfirmDelete={setConfirmDelete}
                  groupName={groupName}
                  locName={locName}
                  unitAbbr={unitAbbr}
                  groups={groups}
                  locations={locations}
                  units={units}
                  products={products}
                  onSaved={loadProducts}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create modal */}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={loadProducts}
          groups={groups}
          locations={locations}
          units={units}
          products={products}
        />
      )}

      {/* Confirm delete */}
      {confirmDelete && (
        <Confirm
          message={`Are you sure you want to delete "${confirmDelete.name}"? This will also remove stock entries and barcodes.`}
          onYes={handleDelete}
          onNo={() => setConfirmDelete(null)}
        />
      )}

      {/* Error toast */}
      {error && (
        <div className="fixed bottom-4 right-4 z-50 bg-red-900/90 text-red-100 px-4 py-3 rounded-lg shadow-lg flex items-center gap-3 max-w-md">
          <span className="text-sm">{error}</span>
          <button onClick={() => setError(null)} className="text-red-300 hover:text-white font-bold">✕</button>
        </div>
      )}
    </div>
  );
}

/* ── Product table row (+ expandable detail) ────────────────────────────── */

function ProductRow({
  product: p,
  idx,
  expandedId,
  setExpandedId,
  setConfirmDelete,
  groupName,
  locName,
  unitAbbr,
  groups,
  locations,
  units,
  products,
  onSaved,
}) {
  const isExpanded = expandedId === p.id;
  const bgClass = idx % 2 === 0 ? 'bg-gray-800' : 'bg-gray-800/50';

  return (
    <>
      <tr
        className={`${bgClass} hover:bg-gray-700/50 transition-colors ${
          isExpanded ? 'bg-gray-700/30' : ''
        }`}
      >
        {/* Image */}
        <td className="px-4 py-2">
          <div style={{ paddingLeft: p.indent ? 24 : 0 }}>
            <Thumb filename={p.picture_filename} />
          </div>
        </td>

        {/* Name */}
        <td className="px-4 py-2">
          <button
            onClick={() => setExpandedId(isExpanded ? null : p.id)}
            className="text-left font-medium text-gray-100 hover:text-emerald-400 transition-colors"
          >
            {p.indent > 0 && <span className="text-gray-500 mr-1">└</span>}
            {p.name}
          </button>
        </td>

        {/* Group */}
        <td className="px-4 py-2 text-gray-400 hidden md:table-cell">
          {groupName(p.product_group_id)}
        </td>

        {/* Location */}
        <td className="px-4 py-2 text-gray-400 hidden md:table-cell">
          {locName(p.location_id)}
        </td>

        {/* Unit */}
        <td className="px-4 py-2 text-gray-400 hidden sm:table-cell">
          {unitAbbr(p.unit_id)}
        </td>

        {/* Stock */}
        <td className="px-4 py-2 text-right font-medium text-gray-100">
          {p.stock_amount ?? '–'}
        </td>

        {/* Best before (days) */}
        <td className="px-4 py-2 text-right text-gray-400 hidden lg:table-cell">
          {p.default_best_before_days ? `${p.default_best_before_days} days` : '–'}
        </td>

        {/* Actions */}
        <td className="px-4 py-2 text-right">
          <div className="flex items-center justify-end gap-1">
            <button
              onClick={() => setExpandedId(isExpanded ? null : p.id)}
              className="p-1.5 rounded hover:bg-emerald-500/20 text-emerald-400"
              title="Edit"
            >
              ✏️
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setConfirmDelete(p); }}
              className="p-1.5 rounded hover:bg-red-500/20 text-red-400"
              title="Delete"
            >
              🗑️
            </button>
          </div>
        </td>
      </tr>

      {/* Detail panel */}
      {isExpanded && (
        <DetailPanel
          product={p}
          groups={groups}
          locations={locations}
          units={units}
          products={products}
          onClose={() => setExpandedId(null)}
          onSaved={onSaved}
        />
      )}
    </>
  );
}
