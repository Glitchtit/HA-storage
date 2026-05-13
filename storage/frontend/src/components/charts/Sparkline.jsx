import { ResponsiveContainer, AreaChart, Area, Tooltip, XAxis, YAxis } from 'recharts';

export default function Sparkline({ data, valueKey = 'value', labelKey = 'day', color = 'var(--brand-cobalt)', height = 80, formatValue }) {
  if (!data || data.length === 0) {
    return <div className="h-20 flex items-center justify-center text-xs text-gray-500">No data</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`spark-${color.replace(/[^a-z0-9]/gi, '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.6} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey={labelKey} hide />
        <YAxis hide />
        <Tooltip
          contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
          formatter={(v) => (formatValue ? formatValue(v) : v)}
        />
        <Area
          type="monotone"
          dataKey={valueKey}
          stroke={color}
          strokeWidth={2}
          fill={`url(#spark-${color.replace(/[^a-z0-9]/gi, '')})`}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
