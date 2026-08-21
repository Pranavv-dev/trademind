"use client";

import Link from "next/link";
import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatPnL, formatPercent, relativeTime } from "@/lib/utils";
import type { AgentSummary } from "@/lib/api";

interface AgentTableProps {
  agents: AgentSummary[];
}

function statusVariant(status: string) {
  switch (status) {
    case "active":
      return "success" as const;
    case "paused":
      return "warning" as const;
    case "error":
      return "danger" as const;
    default:
      return "default" as const;
  }
}

export function AgentTable({ agents }: AgentTableProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Agents</CardTitle>
      </CardHeader>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
              <th className="pb-2 pr-4 font-medium">Name</th>
              <th className="pb-2 pr-4 font-medium">Type</th>
              <th className="pb-2 pr-4 font-medium">Status</th>
              <th className="pb-2 pr-4 font-medium text-right">P&L Today</th>
              <th className="pb-2 pr-4 font-medium text-right">Trades</th>
              <th className="pb-2 pr-4 font-medium text-right">Win Rate</th>
              <th className="pb-2 font-medium">Last Signal</th>
            </tr>
          </thead>
          <tbody>
            {agents.length === 0 && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-zinc-500">
                  No agents configured
                </td>
              </tr>
            )}
            {agents.map((agent) => (
              <tr
                key={agent.id}
                className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/30"
              >
                <td className="py-3 pr-4">
                  <Link href={`/agents/${agent.id}`} className="font-medium text-zinc-200 hover:text-emerald-400">
                    {agent.name}
                  </Link>
                </td>
                <td className="py-3 pr-4 text-zinc-400">{agent.strategy_type}</td>
                <td className="py-3 pr-4">
                  <Badge variant={statusVariant(agent.status)}>{agent.status}</Badge>
                </td>
                <td className={`py-3 pr-4 text-right font-mono ${agent.pnl_today >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {formatPnL(agent.pnl_today)}
                </td>
                <td className="py-3 pr-4 text-right text-zinc-300">{agent.trades_today}</td>
                <td className="py-3 pr-4 text-right text-zinc-300">{formatPercent(agent.win_rate)}</td>
                <td className="py-3 text-zinc-500">
                  {agent.last_signal ? relativeTime(agent.last_signal) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
