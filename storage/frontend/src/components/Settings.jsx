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
        error: err.response?.data?.detail ?? err.message ?? 'Tuntematon virhe',
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
      <h2 className="text-xl font-bold">⚙️ Asetukset</h2>

      {/* Database info card */}
      <div className="bg-white rounded-lg border p-5 space-y-3">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Tietokanta</h3>
        {health ? (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <span className="text-gray-500 block text-xs">Versio</span>
              <span className="font-medium">{health.version ?? '–'}</span>
            </div>
            <div>
              <span className="text-gray-500 block text-xs">Taulut</span>
              <span className="font-medium">
                {Array.isArray(health.db_tables) ? health.db_tables.length : health.db_tables ?? '–'}
              </span>
            </div>
            <div>
              <span className="text-gray-500 block text-xs">Tila</span>
              <span className="inline-flex items-center gap-1.5 font-medium">
                <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                {health.status ?? 'ok'}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-gray-400 text-sm">Ladataan…</p>
        )}
      </div>

      {/* AI configuration card */}
      <div className="bg-white rounded-lg border p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">AI-asetukset</h3>

        {!editingAi ? (
          <>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500 block text-xs">API-avain</span>
                <span className="font-mono text-sm">{maskKey(aiKey)}</span>
              </div>
              <div>
                <span className="text-gray-500 block text-xs">Malli</span>
                <span className="font-medium">{aiModel || '–'}</span>
              </div>
            </div>
            <button
              onClick={() => {
                setEditingAi(true);
                setAiKeyInput('');
                setAiModelInput(aiModel);
              }}
              className="text-sm text-blue-600 hover:text-blue-800 font-medium transition-colors"
            >
              Muokkaa
            </button>
          </>
        ) : (
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Gemini API-avain</label>
              <input
                type="password"
                value={aiKeyInput}
                onChange={(e) => setAiKeyInput(e.target.value)}
                placeholder="Uusi API-avain"
                className="w-full border rounded px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-blue-300 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Malli</label>
              <input
                type="text"
                value={aiModelInput}
                onChange={(e) => setAiModelInput(e.target.value)}
                placeholder="gemini-2.0-flash"
                className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSaveAi}
                disabled={savingAi || (!aiKeyInput.trim() && !aiModelInput.trim())}
                className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {savingAi ? 'Tallennetaan…' : 'Tallenna'}
              </button>
              <button
                onClick={() => setEditingAi(false)}
                className="px-4 py-2 rounded text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
              >
                Peruuta
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Grocy migration card */}
      <div className="bg-white rounded-lg border p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Grocy-migraatio</h3>

        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-sm text-amber-800">
          ⚠️ Tämä ylikirjoittaa nykyisen tietokannan
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Grocy URL</label>
            <input
              type="url"
              value={grocyUrl}
              onChange={(e) => setGrocyUrl(e.target.value)}
              placeholder="http://192.168.1.100:9283"
              className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">API-avain</label>
            <input
              type="password"
              value={grocyApiKey}
              onChange={(e) => setGrocyApiKey(e.target.value)}
              placeholder="Grocy API-avain"
              className="w-full border rounded px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-blue-300 focus:outline-none"
            />
          </div>
          <button
            onClick={handleMigration}
            disabled={migrating || !grocyUrl.trim() || !grocyApiKey.trim()}
            className="bg-amber-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {migrating ? 'Migratoidaan…' : 'Aloita migraatio'}
          </button>
        </div>

        {/* Migration results */}
        {migrationResult && (
          <div className="mt-4 space-y-3">
            {migrationResult.error ? (
              <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-800">
                Virhe: {migrationResult.error}
              </div>
            ) : (
              <>
                <h4 className="text-sm font-medium text-gray-700">Tulokset</h4>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {resultEntries.map(([key, value]) => (
                    <div key={key} className="bg-gray-50 rounded px-3 py-2 text-sm">
                      <span className="text-gray-500 text-xs block capitalize">
                        {key.replace(/_/g, ' ')}
                      </span>
                      <span className="font-medium">{value}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {Array.isArray(migrationResult.errors) && migrationResult.errors.length > 0 && (
              <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 space-y-1">
                <h4 className="text-sm font-medium text-red-800">Virheet ({migrationResult.errors.length})</h4>
                <ul className="text-xs text-red-700 list-disc list-inside max-h-40 overflow-y-auto">
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
