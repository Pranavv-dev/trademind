"use client";

import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { formatCurrency } from "@/lib/utils";
import type { EquityCurvePoint } from "@/lib/api";

interface EquityChartProps {
  data: EquityCurvePoint[];
}

export function EquityChart({ data }: EquityChartProps) {
  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Equity Curve</CardTitle>
        </CardHeader>
        <div className="flex h-48 items-center justify-center text-sm text-zinc-500">
          No equity data yet
        </div>
      </Card>
    );
  }

  const values = data.map((d) => d.portfolio_value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  // Simple SVG line chart
  const width = 800;
  const height = 200;
  const padding = 4;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * (width - padding * 2);
    const y = height - padding - ((d.portfolio_value - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  }).join(" ");

  // Area fill
  const firstX = padding;
  const lastX = padding + ((data.length - 1) / (data.length - 1)) * (width - padding * 2);
  const areaPoints = `${firstX},${height} ${points} ${lastX},${height}`;

  const isPositive = values[values.length - 1] >= values[0];
  const strokeColor = isPositive ? "#34d399" : "#f87171";
  const fillColor = isPositive ? "#34d39915" : "#f8717115";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Equity Curve</CardTitle>
        <span className="text-sm text-zinc-400">
          {formatCurrency(values[values.length - 1])}
        </span>
      </CardHeader>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-48 w-full">
        <polygon points={areaPoints} fill={fillColor} />
        <polyline
          points={points}
          fill="none"
          stroke={strokeColor}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="mt-2 flex justify-between text-xs text-zinc-500">
        <span>{data[0].date}</span>
        <span>{data[data.length - 1].date}</span>
      </div>
    </Card>
  );
}
