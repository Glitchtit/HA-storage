import { useState, useEffect } from 'react';
import { getHealth } from './api';
import Dashboard from './components/Dashboard';
import Products from './components/Products';
import Stock from './components/Stock';
import Recipes from './components/Recipes';
import ShoppingList from './components/ShoppingList';
import Units from './components/Units';
import Locations from './components/Locations';
import Groups from './components/Groups';
import BarcodeQueue from './components/BarcodeQueue';
import Settings from './components/Settings';

const TABS = [
  { id: 'dashboard', label: '📊 Yleiskatsaus', component: Dashboard },
  { id: 'products', label: '📦 Tuotteet', component: Products },
  { id: 'stock', label: '🏪 Varasto', component: Stock },
  { id: 'recipes', label: '🍽️ Reseptit', component: Recipes },
  { id: 'shopping', label: '🛒 Ostoslista', component: ShoppingList },
  { id: 'units', label: '📏 Yksiköt', component: Units },
  { id: 'locations', label: '📍 Sijainnit', component: Locations },
  { id: 'groups', label: '🏷️ Ryhmät', component: Groups },
  { id: 'barcodes', label: '📱 Viivakoodit', component: BarcodeQueue },
  { id: 'settings', label: '⚙️ Asetukset', component: Settings },
];

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState('');

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const { data } = await getHealth();
        if (!cancelled) {
          setVersion(data.version);
          setReady(true);
        }
      } catch {
        if (!cancelled) setTimeout(check, 3000);
      }
    };
    check();
    return () => { cancelled = true; };
  }, []);

  if (!ready) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <div className="animate-spin h-10 w-10 border-4 border-blue-500 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-gray-500">Odotetaan Storage-palvelua…</p>
        </div>
      </div>
    );
  }

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? Dashboard;

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b px-4 py-2 flex items-center justify-between shrink-0">
        <h1 className="text-lg font-bold">🗄️ Storage</h1>
        <span className="text-xs text-gray-400">v{version}</span>
      </header>

      {/* Tab bar */}
      <nav className="bg-white border-b overflow-x-auto shrink-0">
        <div className="flex min-w-max px-2">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </nav>

      {/* Content */}
      <main className="flex-1 overflow-auto p-4">
        <ActiveComponent />
      </main>
    </div>
  );
}
