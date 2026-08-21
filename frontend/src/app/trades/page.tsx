"use client";

import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { Card, CardHeader, CardTitle, CardValue } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency, formatPnL, relativeTime } from "@/lib/utils";
import { getTrades, getTradeSummary, getAgents, type Trade } from "@/lib/api";

export default function TradesPage() {
  const [agentFilter, setAgentFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");

  const { data: trades, loading } = useApi(
    () => getTrades({ agent_id: agentFilter || undefined, symbol: symbolFilter || undefined, limit: 100 }),
    [agentFilter, symbolFilter]
  );
  const { data: summary } = useApi(getTradeSummary);
  const { data: agents } = useApi(getAgents);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Trades</h1>
        <p className="text-sm text-zinc-500">Trade history and performance</p>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardTitle>Total Trades</CardTitle>
            <CardValue>{summary.total_trades}</CardValue>
          </Card>
          <Card>
            <CardTitle>Today&apos;s P&amp;L</CardTitle>
            <CardValue className={summary.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
              {formatPnL(summary.total_pnl)}
            </CardValue>
          </Card>
          <Card>
            <CardTitle>Win / Loss</CardTitle>
            <CardValue>
              <span className="text-emerald-400">{summary.wins}</span>
              {" / "}
              <span className="text-red-400">{summary.losses}</span>
            </CardValue>
          </Card>
          <Card>
            <CardTitle>Win Rate</CardTitle>
            <CardValue>{(summary.win_rate * 100).toFixed(1)}%</CardValue>
          </Card>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
        >
          <option value="">All Agents</option>
          {agents?.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <input
          value={symbolFilter}
          onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
          placeholder="Filter by symbol..."
          className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
        />
      </div>

      {/* Table */}
      <Card>
        <CardHeader><CardTitle>Trade History</CardTitle></CardHeader>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="pb-2 pr-4 font-medium">Symbol</th>
                <th className="pb-2 pr-4 font-medium">Agent</th>
                <th className="pb-2 pr-4 font-medium">Side</th>
                <th className="pb-2 pr-4 font-medium text-right">Qty</th>
                <th className="pb-2 pr-4 font-medium text-right">Price</th>
                <th className="pb-2 pr-4 font-medium text-right">P&amp;L</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 pr-4 font-medium">Mode</th>
                <th className="pb-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-zinc-500">
                    Loading trades...
                  </td>
                </tr>
              )}
              {!loading && (!trades || trades.length === 0) && (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-zinc-500">
                    No trades found
                  </td>
                </tr>
              )}
              {trades?.map((trade) => (
                <TradeRow key={trade.id} trade={trade} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function TradeRow({ trade }: { trade: Trade }) {
  return (
    <tr className="border-b border-zinc-800/50">
      <td className="py-2 pr-4 font-medium text-zinc-200">{trade.symbol}</td>
      <td className="py-2 pr-4 text-zinc-400">{trade.agent_name}</td>
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
      <td className="py-2 pr-4">
        <Badge variant={trade.is_paper ? "warning" : "info"}>
          {trade.is_paper ? "Paper" : "Live"}
        </Badge>
      </td>
      <td className="py-2 text-zinc-500">
        {trade.executed_at ? relativeTime(trade.executed_at) : "—"}
      </td>
    </tr>
  );
}
