"use client";

import { use } from "react";
import { useApi } from "@/hooks/use-api";
import { Card, CardHeader, CardTitle, CardValue } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency, formatPnL, relativeTime } from "@/lib/utils";
import { getAgent, getTrades, startAgent, stopAgent } from "@/lib/api";
import Link from "next/link";

export default function AgentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: agent, loading, refetch } = useApi(() => getAgent(id), [id]);
  const { data: trades } = useApi(() => getTrades({ agent_id: id, limit: 20 }), [id]);

  async function handleToggle() {
    if (!agent) return;
    if (agent.status === "active") {
      await stopAgent(id);
    } else {
      await startAgent(id);
    }
    refetch();
  }

  if (loading) return <p className="text-zinc-500">Loading agent...</p>;
  if (!agent) return <p className="text-zinc-500">Agent not found</p>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link href="/agents" className="text-sm text-zinc-500 hover:text-zinc-300">
            &larr; Back to Agents
          </Link>
          <h1 className="mt-1 text-2xl font-bold">{agent.name}</h1>
          <div className="mt-1 flex items-center gap-2">
            <Badge
              variant={agent.status === "active" ? "success" : agent.status === "paused" ? "warning" : "default"}
            >
              {agent.status}
            </Badge>
            <span className="text-sm text-zinc-500">{agent.strategy_type}</span>
          </div>
        </div>
        <button
          onClick={handleToggle}
          className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${
            agent.status === "active"
              ? "bg-amber-600 text-white hover:bg-amber-500"
              : "bg-emerald-600 text-white hover:bg-emerald-500"
          }`}
        >
          {agent.status === "active" ? "Pause Agent" : "Start Agent"}
        </button>
      </div>

      {/* Info cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardTitle>Market</CardTitle>
          <CardValue className="text-xl">{agent.market}</CardValue>
        </Card>
        <Card>
          <CardTitle>Capital Allocated</CardTitle>
          <CardValue className="text-xl">{formatCurrency(agent.capital_allocated)}</CardValue>
        </Card>
        <Card>
          <CardTitle>Created</CardTitle>
          <CardValue className="text-xl">{relativeTime(agent.created_at)}</CardValue>
        </Card>
        <Card>
          <CardTitle>Last Updated</CardTitle>
          <CardValue className="text-xl">{relativeTime(agent.updated_at)}</CardValue>
        </Card>
      </div>

      {/* Config */}
      {agent.config && Object.keys(agent.config).length > 0 && (
        <Card>
          <CardHeader><CardTitle>Configuration</CardTitle></CardHeader>
          <pre className="overflow-x-auto text-xs text-zinc-400">
            {JSON.stringify(agent.config, null, 2)}
          </pre>
        </Card>
      )}

      {/* Trades */}
      <Card>
        <CardHeader><CardTitle>Recent Trades</CardTitle></CardHeader>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="pb-2 pr-4 font-medium">Symbol</th>
                <th className="pb-2 pr-4 font-medium">Side</th>
                <th className="pb-2 pr-4 font-medium text-right">Qty</th>
                <th className="pb-2 pr-4 font-medium text-right">Price</th>
                <th className="pb-2 pr-4 font-medium text-right">P&L</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {(!trades || trades.length === 0) && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-zinc-500">
                    No trades yet
                  </td>
                </tr>
              )}
              {trades?.map((trade) => (
                <tr key={trade.id} className="border-b border-zinc-800/50">
                  <td className="py-2 pr-4 font-medium text-zinc-200">{trade.symbol}</td>
                  <td className="py-2 pr-4">
                    <Badge variant={trade.side === "BUY" ? "success" : "danger"}>
                      {trade.side}
                    </Badge>
                  </td>
                  <td className="py-2 pr-4 text-right text-zinc-300">{trade.quantity}</td>
                  <td className="py-2 pr-4 text-right font-mono text-zinc-300">
                    {formatCurrency(trade.fill_price ?? trade.price)}
                  </td>
                  <td className={`py-2 pr-4 text-right font-mono ${
                    trade.pnl !== null
                      ? trade.pnl >= 0 ? "text-emerald-400" : "text-red-400"
                      : "text-zinc-500"
                  }`}>
                    {trade.pnl !== null ? formatPnL(trade.pnl) : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    <Badge variant={trade.status === "filled" ? "success" : "default"}>
                      {trade.status}
                    </Badge>
                  </td>
                  <td className="py-2 text-zinc-500">
                    {trade.executed_at ? relativeTime(trade.executed_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
