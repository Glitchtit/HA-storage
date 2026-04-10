import { useState, useEffect, useRef } from 'react';
import { startOptimize, getOptimizeStatus } from '../api';

const POLL_MS = 2000;

export default function Optimize() {
  const [status, setStatus] = useState(null); // null | 'running' | 'done' | 'error'
  const [taskId, setTaskId] = useState(null);
  const [logs, setLogs] = useState([]);
  const [updated, setUpdated] = useState(0);
  const [mode, setMode] = useState('full'); // 'full' | 'incomplete'
  const [error, setError] = useState('');
  const logRef = useRef(null);
  const pollRef = useRef(null);

  useEffect(() => {
    return () => clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

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
      } catch {
        // ignore transient poll errors
      }
    }, POLL_MS);
  };

  const handleStart = async () => {
    setError('');
    setLogs([]);
    setUpdated(0);
    setStatus('running');
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
          <h2 className="text-xl font-semibold text-white">🤖 AI Optimize</h2>
          <p className="text-sm text-gray-400 mt-1">
            Assigns locations, best-before dates, and groups all products using AI.
          </p>
        </div>
        <button
          onClick={handleStart}
          disabled={isRunning}
          className={`px-5 py-2.5 rounded-lg font-medium text-sm transition-colors ${
            isRunning
              ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-500 text-white'
          }`}
        >
          {isRunning ? '⏳ Running…' : '▶ Start Optimize'}
        </button>
      </div>

      {/* Info banner */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-sm text-gray-300 space-y-1">
        <div className="font-medium text-white">What this does</div>
        <ul className="list-disc list-inside space-y-0.5 text-gray-400">
          <li>Phase 1 — AI assigns product categories and groups similar items under parent products</li>
          <li>Phase 2 — AI assigns storage locations, best-before days, and normalises multi-packs</li>
        </ul>
        <p className="text-yellow-400 text-xs pt-1">
          ⚠ Full optimize rewrites all parent-product groupings from scratch. Configure your AI
          provider in Settings before running.
        </p>
      </div>

      {/* Status / result */}
      {status === 'done' && (
        <div className="bg-green-900/40 border border-green-700 rounded-lg p-3 text-green-300 text-sm">
          ✅ Optimize complete — <strong>{updated}</strong> field(s) updated.
        </div>
      )}
      {status === 'error' && (
        <div className="bg-red-900/40 border border-red-700 rounded-lg p-3 text-red-300 text-sm">
          ❌ Optimize failed.{error ? ` ${error}` : ''}
        </div>
      )}

      {/* Log output */}
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
                    : line.includes('complete') || line.includes('✅')
                    ? 'text-green-400'
                    : 'text-gray-300'
                }
              >
                {line}
              </div>
            ))}
            {isRunning && (
              <div className="text-blue-400 animate-pulse">● running…</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
