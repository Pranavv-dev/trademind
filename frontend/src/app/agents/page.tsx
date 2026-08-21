"use client";

import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";
import { formatCurrency } from "@/lib/utils";
import {
  getAgents,
  createAgent,
  startAgent,
  stopAgent,
  deleteAgent,
  type Agent,
  type AgentCreate,
} from "@/lib/api";
import Link from "next/link";

const STRATEGY_TYPES = ["technical", "sentiment", "reasoning"];

function statusVariant(status: string) {
  switch (status) {
    case "active": return "success" as const;
    case "paused": return "warning" as const;
    case "error": return "danger" as const;
    default: return "default" as const;
  }
}

export default function AgentsPage() {
  const { data: agents, loading, refetch } = useApi(getAgents);
  const [showCreate, setShowCreate] = useState(false);

  async function handleToggle(agent: Agent) {
    if (agent.status === "active") {
      await stopAgent(agent.id);
    } else {
      await startAgent(agent.id);
    }
    refetch();
  }

  async function handleDelete(id: string) {
    await deleteAgent(id);
    refetch();
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agents</h1>
          <p className="text-sm text-zinc-500">Manage your trading agents</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
        >
          {showCreate ? "Cancel" : "New Agent"}
        </button>
      </div>

      {showCreate && <CreateAgentForm onCreated={() => { setShowCreate(false); refetch(); }} />}

      {loading && <p className="text-zinc-500">Loading agents...</p>}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {agents?.map((agent) => (
          <Card key={agent.id}>
            <div className="flex items-start justify-between">
              <div>
                <Link
                  href={`/agents/${agent.id}`}
                  className="text-lg font-semibold text-zinc-100 hover:text-emerald-400"
                >
                  {agent.name}
                </Link>
                <div className="mt-1 flex items-center gap-2">
                  <Badge variant={statusVariant(agent.status)}>{agent.status}</Badge>
                  <span className="text-xs text-zinc-500">{agent.strategy_type}</span>
                </div>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
              <div>
                <p className="text-xs text-zinc-500">Market</p>
                <p className="text-zinc-300">{agent.market}</p>
              </div>
              <div>
                <p className="text-xs text-zinc-500">Capital</p>
                <p className="text-zinc-300">{formatCurrency(agent.capital_allocated)}</p>
              </div>
            </div>

            <div className="mt-4 flex gap-2">
              <button
                onClick={() => handleToggle(agent)}
                className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  agent.status === "active"
                    ? "bg-amber-900/30 text-amber-400 hover:bg-amber-900/50"
                    : "bg-emerald-900/30 text-emerald-400 hover:bg-emerald-900/50"
                }`}
              >
                {agent.status === "active" ? "Pause" : "Start"}
              </button>
              <button
                onClick={() => handleDelete(agent.id)}
                className="rounded-md bg-red-900/30 px-3 py-1.5 text-xs font-medium text-red-400 transition-colors hover:bg-red-900/50"
              >
                Delete
              </button>
            </div>
          </Card>
        ))}
      </div>

      {agents?.length === 0 && !loading && (
        <div className="py-12 text-center text-zinc-500">
          No agents yet. Click &quot;New Agent&quot; to create one.
        </div>
      )}
    </div>
  );
}

function CreateAgentForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("technical");
  const [market, setMarket] = useState("NSE");
  const [symbols, setSymbols] = useState("RELIANCE, TCS, INFY");
  const [capital, setCapital] = useState("50000");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    const symbolList = symbols.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      await createAgent({
        name,
        strategy_type: type,
        market,
        universe: { symbols: symbolList },
        capital_allocated: Number(capital),
      });
      onCreated();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Create New Agent</CardTitle></CardHeader>
      <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="My Technical Agent"
            className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Strategy Type</label>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            {STRATEGY_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Market</label>
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value)}
            className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
          >
            <option value="NSE">NSE</option>
            <option value="BSE">BSE</option>
            <option value="NFO">NFO</option>
          </select>
        </div>
        <div className="sm:col-span-full">
          <label className="mb-1 block text-xs text-zinc-400">Universe (comma-separated symbols)</label>
          <input
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            required
            placeholder="RELIANCE, TCS, INFY, HDFCBANK"
            className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-400">Capital (INR)</label>
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            required
            min="1000"
            className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500 focus:outline-none"
          />
        </div>
        <div className="flex items-end">
          <button
            type="submit"
            disabled={submitting || !name || !symbols}
            className="w-full rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </form>
    </Card>
  );
}
