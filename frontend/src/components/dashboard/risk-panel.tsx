"use client";

import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency, formatPercent } from "@/lib/utils";
import type { RiskStatus } from "@/lib/api";

interface RiskPanelProps {
  data: RiskStatus;
}

function ProgressBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 w-full rounded-full bg-zinc-800">
      <div
        className={`h-1.5 rounded-full ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function RiskPanel({ data }: RiskPanelProps) {
  const dailyLossPct = data.daily_loss_limit > 0
    ? (Math.abs(data.daily_loss) / data.daily_loss_limit) * 100
    : 0;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Risk Status</CardTitle>
        {data.circuit_breaker_active && (
          <Badge variant="danger">Circuit Breaker Active</Badge>
        )}
      </CardHeader>

      <div className="space-y-4">
        {/* Daily Loss */}
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-zinc-400">Daily Loss</span>
            <span className="text-zinc-300">
              {formatCurrency(Math.abs(data.daily_loss))} / {formatCurrency(data.daily_loss_limit)}
            </span>
          </div>
          <ProgressBar
            value={Math.abs(data.daily_loss)}
            max={data.daily_loss_limit}
            color={dailyLossPct > 80 ? "bg-red-500" : dailyLossPct > 50 ? "bg-amber-500" : "bg-emerald-500"}
          />
        </div>

        {/* Open Positions */}
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-zinc-400">Open Positions</span>
            <span className="text-zinc-300">
              {data.open_positions} / {data.max_positions}
            </span>
          </div>
          <ProgressBar
            value={data.open_positions}
            max={data.max_positions}
            color="bg-blue-500"
          />
        </div>

        {/* Drawdown */}
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-zinc-400">Drawdown</span>
            <span className="text-zinc-300">
              {formatPercent(-data.drawdown_pct)} / {formatPercent(-data.max_drawdown_pct)} max
            </span>
          </div>
          <ProgressBar
            value={data.drawdown_pct}
            max={data.max_drawdown_pct}
            color={data.drawdown_pct > data.max_drawdown_pct * 0.8 ? "bg-red-500" : "bg-emerald-500"}
          />
        </div>
      </div>
    </Card>
  );
}
