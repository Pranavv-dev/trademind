"use client";

import { useApi } from "@/hooks/use-api";
import { useWebSocket } from "@/hooks/use-websocket";
import { StatusDot } from "@/components/shared/status-dot";
import { StatCards } from "@/components/dashboard/stat-cards";
import { EquityChart } from "@/components/dashboard/equity-chart";
import { AgentTable } from "@/components/dashboard/agent-table";
import { RecentTrades } from "@/components/dashboard/recent-trades";
import { RiskPanel } from "@/components/dashboard/risk-panel";
import {
  getDashboard,
  getEquityCurve,
  getAgentSummary,
  getTrades,
  getRiskStatus,
} from "@/lib/api";

export default function DashboardPage() {
  const { connected } = useWebSocket();

  const dashboard = useApi(getDashboard);
  const equity = useApi(() => getEquityCurve(30));
  const agents = useApi(getAgentSummary);
  const trades = useApi(() => getTrades({ limit: 10 }));
  const risk = useApi(getRiskStatus);

  const loading = dashboard.loading || equity.loading || agents.loading;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-sm text-zinc-500">Real-time portfolio overview</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <StatusDot connected={connected} />
          {connected ? "Live" : "Disconnected"}
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex h-48 items-center justify-center text-zinc-500">
          Loading dashboard...
        </div>
      )}

      {/* Stats */}
      {dashboard.data && <StatCards data={dashboard.data} />}

      {/* Charts & Risk */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {equity.data && <EquityChart data={equity.data} />}
        </div>
        <div>{risk.data && <RiskPanel data={risk.data} />}</div>
      </div>

      {/* Agents & Trades */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {agents.data && <AgentTable agents={agents.data} />}
        </div>
        <div>{trades.data && <RecentTrades trades={trades.data} />}</div>
      </div>

      {/* Error display */}
      {(dashboard.error || equity.error || agents.error) && (
        <div className="rounded-lg border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-400">
          <p className="font-medium">Failed to load dashboard data</p>
          <p className="mt-1 text-red-500/70">
            {dashboard.error || equity.error || agents.error}
          </p>
          <p className="mt-2 text-xs text-red-500/50">
            Make sure the backend is running on port 5000
          </p>
        </div>
      )}
    </div>
  );
}
