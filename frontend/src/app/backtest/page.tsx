"use client";

import { useState } from "react";
import { Card, CardHeader, CardTitle, CardValue } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency, formatPnL } from "@/lib/utils";

const STRATEGIES = ["technical", "sentiment", "reasoning"];

interface BacktestResult {
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  max_drawdown: number;
  sharpe_ratio: number;
  profit_factor: number;
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("technical");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [capital, setCapital] = useState("100000");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    setRunning(true);
    setResult(null);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000"}/api/backtest/run`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol,
            strategy_type: strategy,
            start_date: startDate,
            end_date: endDate,
            initial_capital: Number(capital),
          }),
        }
      );
      if (res.ok) {
        setResult(await res.json());
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Backtest</h1>
        <p className="text-sm text-zinc-500">Test strategies against historical data</p>
      </div>

      {/* Config form */}
      <Card>
        <CardHeader><CardTitle>Configuration</CardTitle></CardHeader>
        <form onSubmit={handleRun} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Symbol</label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              required
              placeholder="RELIANCE"
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
            >
              {STRATEGIES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Initial Capital (INR)</label>
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              required
              min="10000"
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-400">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              required
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-zinc-400">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              required
              className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div className="flex items-end">
            <button
              type="submit"
              disabled={running || !symbol || !startDate || !endDate}
              className="w-full rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
            >
              {running ? "Running..." : "Run Backtest"}
            </button>
          </div>
        </form>
      </Card>

      {/* Results */}
      {running && (
        <div className="flex h-32 items-center justify-center text-zinc-500">
          Running backtest...
        </div>
      )}

      {result && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Card>
              <CardTitle>Total P&amp;L</CardTitle>
              <CardValue className={result.total_pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
                {formatPnL(result.total_pnl)}
              </CardValue>
            </Card>
            <Card>
              <CardTitle>Total Trades</CardTitle>
              <CardValue>{result.total_trades}</CardValue>
            </Card>
            <Card>
              <CardTitle>Win Rate</CardTitle>
              <CardValue>{(result.win_rate * 100).toFixed(1)}%</CardValue>
            </Card>
            <Card>
              <CardTitle>Max Drawdown</CardTitle>
              <CardValue className="text-red-400">
                {(result.max_drawdown * 100).toFixed(2)}%
              </CardValue>
            </Card>
            <Card>
              <CardTitle>Sharpe Ratio</CardTitle>
              <CardValue>{result.sharpe_ratio.toFixed(2)}</CardValue>
            </Card>
            <Card>
              <CardTitle>Profit Factor</CardTitle>
              <CardValue>
                <Badge variant={result.profit_factor >= 1.5 ? "success" : result.profit_factor >= 1 ? "warning" : "danger"}>
                  {result.profit_factor.toFixed(2)}
                </Badge>
              </CardValue>
            </Card>
          </div>

          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 text-center text-sm text-zinc-500">
            Equity curve visualization will be available when the backtesting engine is implemented (Step 15).
          </div>
        </>
      )}

      {!result && !running && (
        <div className="py-12 text-center text-zinc-500">
          Configure parameters above and click &quot;Run Backtest&quot; to test a strategy.
        </div>
      )}
    </div>
  );
}
