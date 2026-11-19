import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ModelTrainingHistory } from "@/types";

const SERIES_COLORS = ["hsl(var(--primary))", "hsl(var(--muted-foreground))", "#f59e0b", "#22c55e"];

function mergeSeries(history: ModelTrainingHistory) {
  const rows = new Map<number, Record<string, number>>();
  history.series.forEach((series, index) => {
    const key = series.name || `Series ${index + 1}`;
    series.points.forEach((point) => {
      const row = rows.get(point.x) ?? { x: point.x };
      row[key] = point.y;
      rows.set(point.x, row);
    });
  });
  return Array.from(rows.values()).sort((a, b) => a.x - b.x);
}

/**
 * Loss / learning curve for a trained model.
 *
 * Recharts is already a dependency (via the shadcn chart wrapper); using it
 * directly keeps this compact and lets the curve sit inside model cards.
 */
export function TrainingLossChart({
  history,
  height = 220,
}: {
  history: ModelTrainingHistory;
  height?: number;
}) {
  const data = mergeSeries(history);
  if (data.length === 0) return null;

  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="x"
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            stroke="hsl(var(--border))"
            label={{
              value: history.x_label || "",
              position: "insideBottom",
              offset: -2,
              fontSize: 11,
              fill: "hsl(var(--muted-foreground))",
            }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            stroke="hsl(var(--border))"
            width={58}
            label={{
              value: history.y_label || "",
              angle: -90,
              position: "insideLeft",
              fontSize: 11,
              fill: "hsl(var(--muted-foreground))",
            }}
          />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "hsl(var(--muted-foreground))" }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {history.series.map((series, index) => (
            <Line
              key={series.name || index}
              type="monotone"
              dataKey={series.name || `Series ${index + 1}`}
              stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
              strokeWidth={2}
              dot={{ r: 2.5 }}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
