"use client";

import { Card, CardTitle, CardValue } from "@/components/shared/card";
import { formatCurrency, formatPnL, formatPercent } from "@/lib/utils";
import type { DashboardOverview } from "@/lib/api";

interface StatCardsProps {
  data: DashboardOverview;
}

export function StatCards({ data }: StatCardsProps) {
  const stats = [
    {
      label: "Total Capital",
      value: formatCurrency(data.total_capital),
    },
    {
      label: "Today's P&L",
      value: formatPnL(data.total_pnl_today),
      color: data.total_pnl_today >= 0 ? "text-emerald-400" : "text-red-400",
    },
    {
      label: "Overall P&L",
      value: formatPnL(data.total_pnl_overall),
      color: data.total_pnl_overall >= 0 ? "text-emerald-400" : "text-red-400",
    },
    {
      label: "Active Agents",
      value: String(data.active_agents),
    },
    {
      label: "Trades Today",
      value: String(data.total_trades_today),
    },
    {
      label: "Win Rate",
      value: formatPercent(data.win_rate),
      color: data.win_rate >= 50 ? "text-emerald-400" : "text-amber-400",
    },
    {
      label: "Max Drawdown",
      value: formatPercent(-data.max_drawdown),
      color: data.max_drawdown < 5 ? "text-zinc-300" : "text-red-400",
    },
    {
      label: "Deployed",
      value: formatCurrency(data.deployed_capital),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.label}>
          <CardTitle>{stat.label}</CardTitle>
          <CardValue className={stat.color}>{stat.value}</CardValue>
        </Card>
      ))}
    </div>
  );
}
