import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';

export default function Timeline({ data, height = 220, color = 'var(--brand-cobalt)', valueKey = 'amount', labelKey = 'day' }) {
  if (!data || data.length === 0) {
    return <div className="h-56 flex items-center justify-center text-sm text-gray-500">No events in window</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="#374151" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey={labelKey} stroke="#9ca3af" fontSize={11} />
        <YAxis stroke="#9ca3af" fontSize={11} width={40} />
        <Tooltip
          contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#d1d5db' }}
        />
        <Line
          type="monotone"
          dataKey={valueKey}
          stroke={color}
          strokeWidth={2}
          dot={{ fill: color, r: 3 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
