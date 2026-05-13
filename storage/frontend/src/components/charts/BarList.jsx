export default function BarList({ items, max, color = 'var(--brand-cobalt)', formatValue, emptyLabel = 'No data', onClick }) {
  if (!items || items.length === 0) {
    return <div className="text-sm text-gray-500 py-6 text-center">{emptyLabel}</div>;
  }
  const peak = max ?? Math.max(...items.map((i) => Number(i.value) || 0), 1);
  return (
    <ul className="space-y-2">
      {items.map((item, idx) => {
        const v = Number(item.value) || 0;
        const pct = peak > 0 ? Math.max(2, (v / peak) * 100) : 0;
        return (
          <li
            key={item.key ?? idx}
            onClick={onClick ? () => onClick(item) : undefined}
            className={`group ${onClick ? 'cursor-pointer' : ''}`}
          >
            <div className="flex items-baseline justify-between text-sm mb-1">
              <span className="text-gray-200 truncate pr-2">{item.label}</span>
              <span className="text-gray-400 tabular-nums shrink-0">
                {formatValue ? formatValue(v) : v}
              </span>
            </div>
            <div className="h-2 bg-gray-700/60 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all group-hover:brightness-110"
                style={{ width: `${pct}%`, background: color }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
