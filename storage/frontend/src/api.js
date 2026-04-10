import axios from 'axios';

const INGRESS_PATH =
  document.querySelector('meta[name="ingress-path"]')?.content ?? '';

const api = axios.create({ baseURL: `${INGRESS_PATH}/api` });

// ── Products ──────────────────────────────────────────────────────────────
export const getProducts = (params) => api.get('/products', { params });
export const getProduct = (id) => api.get(`/products/${id}`);
export const getProductByBarcode = (bc) => api.get(`/products/by-barcode/${bc}`);
export const createProduct = (data) => api.post('/products', data);
export const updateProduct = (id, data) => api.put(`/products/${id}`, data);
export const deleteProduct = (id) => api.delete(`/products/${id}`);

// ── Stock ─────────────────────────────────────────────────────────────────
export const getStock = () => api.get('/stock');
export const getProductStock = (id) => api.get(`/stock/product/${id}`);
export const addStock = (data) => api.post('/stock/add', data);
export const consumeStock = (data) => api.post('/stock/consume', data);
export const openStock = (data) => api.post('/stock/open', data);
export const transferStock = (data) => api.post('/stock/transfer', data);
export const deleteStockEntry = (id) => api.delete(`/stock/${id}`);

// ── Barcodes ──────────────────────────────────────────────────────────────
export const getBarcodes = () => api.get('/barcodes');
export const createBarcode = (data) => api.post('/barcodes', data);
export const updateBarcode = (id, data) => api.put(`/barcodes/${id}`, data);
export const deleteBarcode = (id) => api.delete(`/barcodes/${id}`);

// ── Units & Conversions ──────────────────────────────────────────────────
export const getUnits = () => api.get('/units');
export const createUnit = (data) => api.post('/units', data);
export const deleteUnit = (id) => api.delete(`/units/${id}`);
export const getConversions = (params) => api.get('/conversions', { params });
export const createConversion = (data) => api.post('/conversions', data);
export const deleteConversion = (id) => api.delete(`/conversions/${id}`);
export const resolveConversion = (params) =>
  api.get('/conversions/resolve', { params });

// ── Locations ─────────────────────────────────────────────────────────────
export const getLocations = () => api.get('/locations');
export const createLocation = (data) => api.post('/locations', data);
export const deleteLocation = (id) => api.delete(`/locations/${id}`);

// ── Product Groups ────────────────────────────────────────────────────────
export const getProductGroups = () => api.get('/product-groups');
export const createProductGroup = (data) => api.post('/product-groups', data);
export const deleteProductGroup = (id) => api.delete(`/product-groups/${id}`);

// ── Recipes ───────────────────────────────────────────────────────────────
export const getRecipes = () => api.get('/recipes');
export const getRecipe = (id) => api.get(`/recipes/${id}`);
export const createRecipe = (data) => api.post('/recipes', data);
export const updateRecipe = (id, data) => api.put(`/recipes/${id}`, data);
export const deleteRecipe = (id) => api.delete(`/recipes/${id}`);
export const addIngredient = (recipeId, data) =>
  api.post(`/recipes/${recipeId}/ingredients`, data);
export const updateIngredient = (recipeId, ingId, data) =>
  api.put(`/recipes/${recipeId}/ingredients/${ingId}`, data);
export const recipeToShopping = (id) =>
  api.post(`/recipes/${id}/to-shopping`);

// ── Shopping List ─────────────────────────────────────────────────────────
export const getShoppingList = () => api.get('/shopping-list');
export const addShoppingItem = (data) => api.post('/shopping-list', data);
export const updateShoppingItem = (id, data) =>
  api.put(`/shopping-list/${id}`, data);
export const deleteShoppingItem = (id) => api.delete(`/shopping-list/${id}`);
export const clearDoneShopping = () => api.delete('/shopping-list/done');

// ── Barcode Queue ─────────────────────────────────────────────────────────
export const getBarcodeQueue = (params) =>
  api.get('/barcode-queue', { params });
export const addToBarcodeQueue = (data) => api.post('/barcode-queue', data);
export const updateBarcodeQueue = (id, data) =>
  api.put(`/barcode-queue/${id}`, data);
export const deleteBarcodeQueue = (id) => api.delete(`/barcode-queue/${id}`);

// ── Config ────────────────────────────────────────────────────────────────
export const getConfig = () => api.get('/config');
export const setConfig = (key, value) =>
  api.put(`/config/${key}`, { value });
export const getAiKey = () => api.get('/config/ai-key');

// ── Health ────────────────────────────────────────────────────────────────
export const getHealth = () => api.get('/health');

// ── Files ─────────────────────────────────────────────────────────────────
export const productImageUrl = (filename) =>
  `${INGRESS_PATH}/api/files/products/${filename}`;
export const recipeImageUrl = (filename) =>
  `${INGRESS_PATH}/api/files/recipes/${filename}`;

// ── Migration ─────────────────────────────────────────────────────────────
export const migrateFromGrocy = (data) => api.post('/migrate/grocy', data);

// ── Scraper (proxied via /api/scraper/) ───────────────────────────────────
const scraper = axios.create({ baseURL: `${INGRESS_PATH}/api/scraper` });
export const scraperDiscover = () => scraper.post('/discover');
export const scraperTask = (id) => scraper.get(`/task/${id}`);

export default api;
