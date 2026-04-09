import { useState, useEffect } from 'react';
import { getHealth, getAiKey, setConfig, migrateFromGrocy } from '../api';

export default function Settings() {
  // Database info
  const [health, setHealth] = useState(null);
  // AI config
  const [aiKey, setAiKey] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [editingAi, setEditingAi] = useState(false);
  const [aiKeyInput, setAiKeyInput] = useState('');
  const [aiModelInput, setAiModelInput] = useState('');
  const [savingAi, setSavingAi] = useState(false);
  // Grocy migration
  const [grocyUrl, setGrocyUrl] = useState('');
  const [grocyApiKey, setGrocyApiKey] = useState('');
  const [migrating, setMigrating] = useState(false);
  const [migrationResult, setMigrationResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [healthRes, aiRes] = await Promise.all([getHealth(), getAiKey()]);
        if (cancelled) return;
        setHealth(healthRes.data);
        setAiKey(aiRes.data.api_key ?? '');
        setAiModel(aiRes.data.model ?? '');
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
      if (aiKeyInput.trim()) {
        await setConfig('gemini_api_key', aiKeyInput.trim());
        setAiKey(aiKeyInput.trim());
      }
      if (aiModelInput.trim()) {
        await setConfig('gemini_model', aiModelInput.trim());
        setAiModel(aiModelInput.trim());
      }
      setEditingAi(false);
      setAiKeyInput('');
      setAiModelInput('');
    } catch (err) {
      console.error('Failed to save AI settings', err);
    } finally {
      setSavingAi(false);
    }
  };

  const handleMigration = async () => {
    if (!grocyUrl.trim() || !grocyApiKey.trim()) return;
    setMigrating(true);
    setMigrationResult(null);
    try {
      const { data } = await migrateFromGrocy({
        grocy_url: grocyUrl.trim(),
        api_key: grocyApiKey.trim(),
      });
      setMigrationResult(data);
    } catch (err) {
      setMigrationResult({
        error: err.response?.data?.detail ?? err.message ?? 'Unknown error',
      });
    } finally {
      setMigrating(false);
    }
  };

  const resultEntries = migrationResult
    ? Object.entries(migrationResult).filter(
        ([k]) => !['error', 'errors'].includes(k)
      )
    : [];

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
                <span className="text-gray-400 block text-xs">API Key</span>
                <span className="font-mono text-sm">{maskKey(aiKey)}</span>
              </div>
              <div>
                <span className="text-gray-400 block text-xs">Model</span>
                <span className="font-medium">{aiModel || '–'}</span>
              </div>
            </div>
            <button
              onClick={() => {
                setEditingAi(true);
                setAiKeyInput('');
                setAiModelInput(aiModel);
              }}
              className="text-sm text-emerald-400 hover:text-emerald-300 font-medium transition-colors"
            >
              Edit
            </button>
          </>
        ) : (
          <div className="space-y-3">
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
            <div className="flex gap-2">
              <button
                onClick={handleSaveAi}
                disabled={savingAi || (!aiKeyInput.trim() && !aiModelInput.trim())}
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

      {/* Grocy migration card */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Grocy Migration</h3>

        <div className="bg-yellow-500/20 border border-yellow-500/30 rounded-lg px-4 py-3 text-sm text-yellow-400">
          ⚠️ This will overwrite the current database
        </div>

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
            onClick={handleMigration}
            disabled={migrating || !grocyUrl.trim() || !grocyApiKey.trim()}
            className="bg-red-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {migrating ? 'Migrating…' : 'Start Migration'}
          </button>
        </div>

        {/* Migration results */}
        {migrationResult && (
          <div className="mt-4 space-y-3">
            {migrationResult.error ? (
              <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400">
                Error: {migrationResult.error}
              </div>
            ) : (
              <>
                <h4 className="text-sm font-medium text-gray-300">Results</h4>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {resultEntries.map(([key, value]) => (
                    <div key={key} className="bg-gray-700 rounded px-3 py-2 text-sm">
                      <span className="text-gray-400 text-xs block capitalize">
                        {key.replace(/_/g, ' ')}
                      </span>
                      <span className="font-medium">{value}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {Array.isArray(migrationResult.errors) && migrationResult.errors.length > 0 && (
              <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 space-y-1">
                <h4 className="text-sm font-medium text-red-400">Errors ({migrationResult.errors.length})</h4>
                <ul className="text-xs text-red-400 list-disc list-inside max-h-40 overflow-y-auto">
                  {migrationResult.errors.map((err, i) => (
                    <li key={i}>{typeof err === 'string' ? err : JSON.stringify(err)}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
