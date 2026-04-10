import { useState, useEffect, useRef } from 'react';
import { startOptimize, getOptimizeStatus, getOptimizeCategories, setOptimizeCategories } from '../api';

const POLL_MS = 2000;

export default function Optimize() {
  const [status, setStatus] = useState(null);
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  const [updated, setUpdated] = useState(0);
  const [error, setError] = useState('');
  const logRef = useRef(null);
  const pollRef = useRef(null);

  const [categories, setCategories] = useState([]);
  const [catInput, setCatInput] = useState('');
  const [catSaving, setCatSaving] = useState(false);

  useEffect(() => {
    getOptimizeCategories()
      .then(({ data }) => setCategories(data.categories || []))
      .catch(() => {});
    return () => clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const saveCategories = async (newCats) => {
    setCatSaving(true);
    try {
      const { data } = await setOptimizeCategories(newCats);
      setCategories(data.categories || newCats);
    } catch {
      // swallow
    } finally {
      setCatSaving(false);
    }
  };

  const handleAddCategory = () => {
    const val = catInput.trim();
    if (!val || categories.includes(val)) { setCatInput(''); return; }
    saveCategories([...categories, val]);
    setCatInput('');
  };

  const handleRemoveCategory = (cat) => saveCategories(categories.filter((c) => c !== cat));

  const startPolling = (id) => {
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await getOptimizeStatus(id);
        setLogs(data.logs || []);
        setUpdated(data.updated || 0);
        if (data.status !== 'running') {
          setStatus(data.status);
          clearInterval(pollRef.current);
        }
      } catch { /* ignore */ }
    }, POLL_MS);
  };

  const handleStart = async () => {
    setError(''); setLogs([]); setUpdated(0); setStatus('running');
    try {
      const { data } = await startOptimize();
      setTaskId(data.task_id);
      startPolling(data.task_id);
    } catch (err) {
      setStatus('error');
      setError(err?.response?.data?.detail || err.message || 'Failed to start optimize');
    }
  };

  const isRunning = status === 'running';

  return (
    <div className="p-4 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">AI Optimize</h2>
          <p className="text-sm text-gray-400 mt-1">
            Assigns locations, best-before dates, and groups all products using AI.
          </p>
        </div>
        <button
          onClick={handleStart}
          disabled={isRunning}
          className={"px-5 py-2.5 rounded-lg font-medium text-sm transition-colors " +
            (isRunning ? "bg-gray-700 text-gray-500 cursor-not-allowed" : "bg-blue-600 hover:bg-blue-500 text-white")}
        >
          {isRunning ? "Running..." : "Start Optimize"}
        </button>
      </div>

      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-sm text-gray-300 space-y-1">
        <div className="font-medium text-white">What this does</div>
        <ul className="list-disc list-inside space-y-0.5 text-gray-400">
          <li>Phase 1 - AI assigns product categories and groups similar items under parent products</li>
          <li>Phase 2 - AI assigns storage locations, best-before days, and normalises multi-packs</li>
        </ul>
        <p className="text-yellow-400 text-xs pt-1">
          Full optimize rewrites all parent-product groupings from scratch. Configure your AI provider in Settings first.
        </p>
      </div>

      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 space-y-3">
        <div>
          <div className="font-medium text-white text-sm">Enforced Categories</div>
          <p className="text-xs text-gray-400 mt-0.5">
            The AI will strongly prefer these categories and create them as product groups even if no products are assigned. Use Finnish names.
          </p>
        </div>
        {categories.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {categories.map((cat) => (
              <span key={cat} className="inline-flex items-center gap-1.5 bg-gray-700 text-gray-200 text-sm px-3 py-1 rounded-full">
                {cat}
                <button
                  onClick={() => handleRemoveCategory(cat)}
                  className="text-gray-400 hover:text-red-400 transition-colors leading-none"
                  title="Remove"
                >x</button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <input
            type="text"
            value={catInput}
            onChange={(e) => setCatInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAddCategory(); }}
            placeholder="e.g. Maitotuotteet"
            className="flex-1 bg-gray-900 border border-gray-600 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={handleAddCategory}
            disabled={catSaving || !catInput.trim()}
            className="px-4 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-sm rounded-lg transition-colors"
          >
            {catSaving ? "..." : "Add"}
          </button>
        </div>
      </div>

      {status === 'done' && (
        <div className="bg-green-900/40 border border-green-700 rounded-lg p-3 text-green-300 text-sm">
          Optimize complete - <strong>{updated}</strong> field(s) updated.
        </div>
      )}
      {status === 'error' && (
        <div className="bg-red-900/40 border border-red-700 rounded-lg p-3 text-red-300 text-sm">
          Optimize failed.{error ? " " + error : ""}
        </div>
      )}

      {(logs.length > 0 || isRunning) && (
        <div className="space-y-1">
          <div className="text-xs text-gray-500 uppercase tracking-wide">Log output</div>
          <div
            ref={logRef}
            className="bg-gray-950 border border-gray-800 rounded-lg p-3 h-96 overflow-y-auto font-mono text-xs text-gray-300 space-y-0.5"
          >
            {logs.map((line, i) => (
              <div
                key={i}
                className={
                  line.startsWith('ERROR') || line.startsWith('  !')
                    ? 'text-red-400'
                    : line.startsWith('WARNING') || line.includes('unreachable')
                    ? 'text-yellow-400'
                    : line.includes('complete')
                    ? 'text-green-400'
                    : line.startsWith('  ↳')
                    ? 'text-cyan-500'
                    : 'text-gray-300'
                }
              >{line}</div>
            ))}
            {isRunning && <div className="text-blue-400 animate-pulse">running...</div>}
          </div>
        </div>
      )}
    </div>
  );
}
