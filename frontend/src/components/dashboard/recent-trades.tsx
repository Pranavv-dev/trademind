"use client";

import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency, formatPnL, relativeTime } from "@/lib/utils";
import type { Trade } from "@/lib/api";

interface RecentTradesProps {
  trades: Trade[];
}

export function RecentTrades({ trades }: RecentTradesProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Trades</CardTitle>
      </CardHeader>
      <div className="space-y-3">
        {trades.length === 0 && (
          <p className="py-6 text-center text-sm text-zinc-500">No trades today</p>
        )}
        {trades.slice(0, 10).map((trade) => (
          <div
            key={trade.id}
            className="flex items-center justify-between rounded-md bg-zinc-800/40 px-3 py-2"
          >
            <div className="flex items-center gap-3">
              <Badge variant={trade.side === "BUY" ? "success" : "danger"}>
                {trade.side}
              </Badge>
              <div>
                <p className="text-sm font-medium text-zinc-200">{trade.symbol}</p>
                <p className="text-xs text-zinc-500">{trade.agent_name}</p>
              </div>
            </div>
            <div className="text-right">
              <p className="text-sm text-zinc-300">
                {trade.quantity} @ {formatCurrency(trade.fill_price ?? trade.price)}
              </p>
              <div className="flex items-center justify-end gap-2">
                {trade.pnl !== null && (
                  <span
                    className={`text-xs font-mono ${trade.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}
                  >
                    {formatPnL(trade.pnl)}
                  </span>
                )}
                <span className="text-xs text-zinc-600">
                  {trade.executed_at ? relativeTime(trade.executed_at) : trade.status}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
