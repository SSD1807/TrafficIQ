import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { chartTheme, tooltipStyle } from './ChartContainer';

const { colors } = chartTheme;
const labelColor: Record<string, string> = { Low: colors.secondary, Medium: colors.warning, High: colors.danger };

/** Horizontal bar chart: one value per category, optionally coloured per row. */
export function HorizontalBars({
  data, valueLabel, height = 280, domain, colorFor, leftMargin = 90,
}: {
  data: { name: string; value: number }[];
  valueLabel: string;
  height?: number;
  domain?: [number, number];
  colorFor?: (name: string, value: number) => string;
  leftMargin?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart layout="vertical" data={data} margin={{ top: 10, right: 20, left: leftMargin, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} horizontal={false} />
        <XAxis type="number" stroke={colors.axis} fontSize={11} tickLine={false} axisLine={false} domain={domain} />
        <YAxis type="category" dataKey="name" stroke={colors.axis} fontSize={10} tickLine={false} axisLine={false} width={leftMargin + 10} />
        <Tooltip {...tooltipStyle} />
        <Bar dataKey="value" name={valueLabel} radius={[0, 4, 4, 0]}>
          {data.map((d, i) => <Cell key={i} fill={colorFor ? colorFor(d.name, d.value) : colors.primary} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Class probability bars (Low/Medium/High), 0..1. */
export function ProbabilityBars({ probabilities, height = 200 }: { probabilities: Record<string, number>; height?: number }) {
  const data = Object.entries(probabilities).map(([name, value]) => ({ name, value: +(value * 100).toFixed(2) }));
  return (
    <HorizontalBars
      data={data} valueLabel="Probability %" height={height} domain={[0, 100]}
      colorFor={(n) => labelColor[n] ?? colors.primary} leftMargin={40}
    />
  );
}

/** Vertical grouped columns: x category, several series. */
export function GroupedColumns({
  data, series, height = 280, yLabel,
}: {
  data: Record<string, string | number>[];
  series: { key: string; name: string; color?: string }[];
  height?: number;
  yLabel?: string;
}) {
  const palette = [colors.primary, colors.warning, colors.secondary, colors.danger];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} vertical={false} />
        <XAxis dataKey="x" stroke={colors.axis} fontSize={11} tickLine={false} axisLine={false} />
        <YAxis stroke={colors.axis} fontSize={11} tickLine={false} axisLine={false} label={yLabel ? { value: yLabel, angle: -90, position: 'insideLeft', fill: colors.axis, fontSize: 10 } : undefined} />
        <Tooltip {...tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12, color: colors.axis }} />
        {series.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color ?? palette[i % palette.length]} radius={[4, 4, 0, 0]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
