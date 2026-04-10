import { useState, useEffect, useRef } from 'react';
import { getHealth, getAiConfig, setConfig, migrateFromGrocy, scraperDiscover, scraperTask, factoryReset } from '../api';

export default function Settings() {
  // Database info
  const [health, setHealth] = useState(null);
  // AI config
  const [aiProvider, setAiProvider] = useState('gemini');
  const [aiKey, setAiKey] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('');
  const [ollamaModel, setOllamaModel] = useState('');
  const [editingAi, setEditingAi] = useState(false);
  const [editProvider, setEditProvider] = useState('gemini');
  const [aiKeyInput, setAiKeyInput] = useState('');
  const [aiModelInput, setAiModelInput] = useState('');
  const [ollamaUrlInput, setOllamaUrlInput] = useState('');
  const [ollamaModelInput, setOllamaModelInput] = useState('');
  const [savingAi, setSavingAi] = useState(false);
  // Grocy import
  const [grocyUrl, setGrocyUrl] = useState('');
  const [grocyApiKey, setGrocyApiKey] = useState('');
  const [importing, setImporting] = useState(false);
  const [importPhase, setImportPhase] = useState('');
  const [importResult, setImportResult] = useState(null);
  const [discoverLogs, setDiscoverLogs] = useState([]);
  const pollRef = useRef(null);

  // Factory reset
  const [resetConfirming, setResetConfirming] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState(null);

  const handleReset = async () => {
    setResetting(true);
    setResetResult(null);
    try {
      await factoryReset();
      setResetResult({ success: true });
      setTimeout(() => window.location.reload(), 2000);
    } catch (err) {
      setResetResult({ error: err.response?.data?.detail ?? err.message ?? 'Reset failed' });
    } finally {
      setResetting(false);
      setResetConfirming(false);
    }
  };

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [healthRes, aiRes] = await Promise.all([getHealth(), getAiConfig()]);
        if (cancelled) return;
        setHealth(healthRes.data);
        const d = aiRes.data;
        setAiProvider(d.provider ?? 'gemini');
        setAiKey(d.api_key ?? '');
        setAiModel(d.model ?? '');
        setOllamaUrl(d.ollama_url ?? '');
        setOllamaModel(d.ollama_model ?? '');
      } catch (err) {
        console.error('Failed to load settings', err);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const maskKey = (key) => {
    if (!key) return '–';
    if (key.length <= 4) return '••••';
    return '••••••••' + key.slice(-4);
  };

  const handleSaveAi = async () => {
    setSavingAi(true);
    try {
      await setConfig('ai_provider', editProvider);
      setAiProvider(editProvider);
      if (editProvider === 'gemini') {
        if (aiKeyInput.trim()) {
          await setConfig('gemini_api_key', aiKeyInput.trim());
          setAiKey(aiKeyInput.trim());
        }
        if (aiModelInput.trim()) {
          await setConfig('gemini_model', aiModelInput.trim());
          setAiModel(aiModelInput.trim());
        }
      } else {
        if (ollamaUrlInput.trim()) {
          await setConfig('ollama_url', ollamaUrlInput.trim());
          setOllamaUrl(ollamaUrlInput.trim());
        }
        if (ollamaModelInput.trim()) {
          await setConfig('ollama_model', ollamaModelInput.trim());
          setOllamaModel(ollamaModelInput.trim());
        }
      }
      setEditingAi(false);
      setAiKeyInput('');
      setAiModelInput('');
      setOllamaUrlInput('');
      setOllamaModelInput('');
    } catch (err) {
      console.error('Failed to save AI settings', err);
    } finally {
      setSavingAi(false);
    }
  };

  const handleImport = async () => {
    if (!grocyUrl.trim() || !grocyApiKey.trim()) return;
    setImporting(true);
    setImportResult(null);
    setDiscoverLogs([]);

    // Phase 1: Queue barcodes
    setImportPhase('Fetching barcodes from Grocy…');
    let queueResult;
    try {
      const { data } = await migrateFromGrocy({
        grocy_url: grocyUrl.trim(),
        api_key: grocyApiKey.trim(),
      });
      queueResult = data;
      setImportResult(data);
    } catch (err) {
      setImportResult({
        error: err.response?.data?.detail ?? err.message ?? 'Unknown error',
      });
      setImporting(false);
      setImportPhase('');
      return;
    }

    // Phase 2: Trigger scraper discover if we queued any barcodes
    if ((queueResult.barcodes_queued ?? 0) > 0) {
      setImportPhase('Creating products from barcodes…');
      try {
        const { data: taskData } = await scraperDiscover();
        const taskId = taskData.task_id;

        if (taskId) {
          // Poll scraper task until done
          await new Promise((resolve) => {
            pollRef.current = setInterval(async () => {
              try {
                const { data: status } = await scraperTask(taskId);
                if (status.logs) setDiscoverLogs(status.logs);
                if (status.status === 'done') {
                  clearInterval(pollRef.current);
                  pollRef.current = null;
                  setImportResult((prev) => ({
                    ...prev,
                    discover_done: true,
                    discover_success: status.success !== false,
                  }));
                  resolve();
                }
              } catch {
                // Keep polling on transient errors
              }
            }, 3000);
          });
        }
      } catch (err) {
        setImportResult((prev) => ({
          ...prev,
          discover_error: err.response?.data?.error ?? err.message ?? 'Scraper not reachable',
        }));
      }
    }

    setImporting(false);
    setImportPhase('');
  };

  return (
    <div className="space-y-6 max-w-3xl mx-auto">
      <h2 className="text-xl font-bold">⚙️ Settings</h2>

      {/* Database info card */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 space-y-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Database</h3>
        {health ? (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <span className="text-gray-400 block text-xs">Version</span>
              <span className="font-medium">{health.version ?? '–'}</span>
            </div>
            <div>
              <span className="text-gray-400 block text-xs">Tables</span>
              <span className="font-medium">
                {Array.isArray(health.db_tables) ? health.db_tables.length : health.db_tables ?? '–'}
              </span>
            </div>
            <div>
              <span className="text-gray-400 block text-xs">Status</span>
              <span className="inline-flex items-center gap-1.5 font-medium">
                <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                {health.status ?? 'ok'}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">Loading…</p>
        )}
      </div>

      {/* AI configuration card */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">AI Configuration</h3>

        {!editingAi ? (
          <>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-400 block text-xs">Provider</span>
                <span className="font-medium capitalize">{aiProvider}</span>
              </div>
              {aiProvider === 'gemini' ? (
                <>
                  <div>
                    <span className="text-gray-400 block text-xs">API Key</span>
                    <span className="font-mono text-sm">{maskKey(aiKey)}</span>
                  </div>
                  <div>
                    <span className="text-gray-400 block text-xs">Model</span>
                    <span className="font-medium">{aiModel || '–'}</span>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <span className="text-gray-400 block text-xs">Ollama URL</span>
                    <span className="font-mono text-sm">{ollamaUrl || '–'}</span>
                  </div>
                  <div>
                    <span className="text-gray-400 block text-xs">Model</span>
                    <span className="font-medium">{ollamaModel || '–'}</span>
                  </div>
                </>
              )}
            </div>
            <button
              onClick={() => {
                setEditingAi(true);
                setEditProvider(aiProvider);
                setAiKeyInput('');
                setAiModelInput(aiModel);
                setOllamaUrlInput(ollamaUrl);
                setOllamaModelInput(ollamaModel);
              }}
              className="text-sm text-emerald-400 hover:text-emerald-300 font-medium transition-colors"
            >
              Edit
            </button>
          </>
        ) : (
          <div className="space-y-3">
            {/* Provider selector */}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Provider</label>
              <div className="flex gap-2">
                {['gemini', 'ollama'].map((p) => (
                  <button
                    key={p}
                    onClick={() => setEditProvider(p)}
                    className={`px-3 py-1.5 rounded text-sm font-medium transition-colors capitalize ${
                      editProvider === p
                        ? 'bg-emerald-600 text-white'
                        : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>

            {editProvider === 'gemini' ? (
              <>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Gemini API Key</label>
                  <input
                    type="password"
                    value={aiKeyInput}
                    onChange={(e) => setAiKeyInput(e.target.value)}
                    placeholder="New API key"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm font-mono text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Model</label>
                  <input
                    type="text"
                    value={aiModelInput}
                    onChange={(e) => setAiModelInput(e.target.value)}
                    placeholder="gemini-2.0-flash"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>
              </>
            ) : (
              <>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Ollama URL</label>
                  <input
                    type="text"
                    value={ollamaUrlInput}
                    onChange={(e) => setOllamaUrlInput(e.target.value)}
                    placeholder="http://192.168.1.100:11434"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm font-mono text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Model</label>
                  <input
                    type="text"
                    value={ollamaModelInput}
                    onChange={(e) => setOllamaModelInput(e.target.value)}
                    placeholder="llama3"
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>
              </>
            )}

            <div className="flex gap-2">
              <button
                onClick={handleSaveAi}
                disabled={savingAi}
                className="bg-emerald-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {savingAi ? 'Saving…' : 'Save'}
              </button>
              <button
                onClick={() => setEditingAi(false)}
                className="px-4 py-2 rounded text-sm font-medium text-gray-400 hover:bg-gray-700 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Grocy import card */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Import from Grocy</h3>

        <p className="text-sm text-gray-400">
          Imports barcodes and stock from Grocy, then automatically creates products
          with images, AI grouping, and conversions via the Scraper.
        </p>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Grocy URL</label>
            <input
              type="url"
              value={grocyUrl}
              onChange={(e) => setGrocyUrl(e.target.value)}
              placeholder="http://192.168.1.100:9283"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">API Key</label>
            <input
              type="password"
              value={grocyApiKey}
              onChange={(e) => setGrocyApiKey(e.target.value)}
              placeholder="Grocy API key"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm font-mono text-gray-100 placeholder-gray-500 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
            />
          </div>
          <button
            onClick={handleImport}
            disabled={importing || !grocyUrl.trim() || !grocyApiKey.trim()}
            className="bg-emerald-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {importing ? 'Importing…' : 'Import from Grocy'}
          </button>
        </div>

        {/* Progress */}
        {importing && importPhase && (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <svg className="animate-spin h-4 w-4 text-emerald-400" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
            </svg>
            {importPhase}
          </div>
        )}

        {/* Discover logs */}
        {discoverLogs.length > 0 && (
          <div className="bg-gray-900 rounded px-3 py-2 text-xs font-mono text-gray-400 max-h-48 overflow-y-auto">
            {discoverLogs.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        )}

        {/* Results */}
        {importResult && !importing && (
          <div className="mt-4 space-y-3">
            {importResult.error ? (
              <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
                Error: {importResult.error}
              </div>
            ) : (
              <div className="bg-emerald-500/20 border border-emerald-500/30 rounded-lg px-4 py-3 text-sm text-emerald-400 space-y-1">
                <p>✅ {importResult.barcodes_queued ?? 0} barcode(s) imported from Grocy</p>
                {(importResult.barcodes_skipped ?? 0) > 0 && (
                  <p className="text-gray-400">{importResult.barcodes_skipped} already known — skipped</p>
                )}
                {importResult.discover_done && importResult.discover_success && (
                  <p>✅ Products created with images and AI optimization</p>
                )}
                {importResult.discover_done && !importResult.discover_success && (
                  <p className="text-yellow-400">⚠️ Discover completed with some errors — check scraper logs</p>
                )}
                {importResult.discover_error && (
                  <p className="text-yellow-400">⚠️ Could not reach Scraper: {importResult.discover_error}</p>
                )}
              </div>
            )}

            {Array.isArray(importResult.errors) && importResult.errors.length > 0 && (
              <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 space-y-1">
                <h4 className="text-sm font-medium text-red-400">Errors ({importResult.errors.length})</h4>
                <ul className="text-xs text-red-400 list-disc list-inside max-h-40 overflow-y-auto">
                  {importResult.errors.map((err, i) => (
                    <li key={i}>{typeof err === 'string' ? err : JSON.stringify(err)}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
      {/* Danger Zone card */}
      <div className="bg-gray-800 rounded-lg border border-red-800/60 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-red-400 uppercase tracking-wide">Danger Zone</h3>

        <p className="text-sm text-gray-400">
          Factory reset deletes all products, stock, barcodes, recipes, and uploaded images.
          Standard units, locations, and conversions are re-seeded automatically.
        </p>

        {!resetConfirming && !resetResult && (
          <button
            onClick={() => setResetConfirming(true)}
            className="bg-red-700 text-white px-4 py-2 rounded text-sm font-medium hover:bg-red-600 transition-colors"
          >
            Factory Reset
          </button>
        )}

        {resetConfirming && (
          <div className="space-y-3">
            <p className="text-sm font-medium text-red-400">
              ⚠️ This will permanently delete everything. Are you sure?
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleReset}
                disabled={resetting}
                className="bg-red-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {resetting ? 'Resetting…' : 'Yes, wipe everything'}
              </button>
              <button
                onClick={() => setResetConfirming(false)}
                disabled={resetting}
                className="px-4 py-2 rounded text-sm font-medium text-gray-400 hover:bg-gray-700 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {resetResult?.success && (
          <div className="bg-emerald-500/20 border border-emerald-500/30 rounded-lg px-4 py-3 text-sm text-emerald-400">
            ✅ Database reset complete. Reloading…
          </div>
        )}
        {resetResult?.error && (
          <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
            Error: {resetResult.error}
          </div>
        )}
      </div>
    </div>
  );
}
