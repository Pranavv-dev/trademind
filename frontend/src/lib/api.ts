const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Dashboard ──

export interface DashboardOverview {
  total_capital: number;
  deployed_capital: number;
  available_capital: number;
  total_pnl_today: number;
  total_pnl_overall: number;
  active_agents: number;
  total_trades_today: number;
  win_rate: number;
  sharpe_ratio: number | null;
  max_drawdown: number;
}

export interface EquityCurvePoint {
  date: string;
  portfolio_value: number;
  daily_pnl: number;
}

export interface AgentSummary {
  id: string;
  name: string;
  strategy_type: string;
  status: string;
  pnl_today: number;
  trades_today: number;
  win_rate: number;
  last_signal: string | null;
}

export function getDashboard() {
  return request<DashboardOverview>("/api/dashboard/overview");
}

export function getEquityCurve(days = 30) {
  return request<EquityCurvePoint[]>(`/api/dashboard/equity-curve?days=${days}`);
}

export function getAgentSummary() {
  return request<AgentSummary[]>("/api/dashboard/agent-summary");
}

// ── Agents ──

export interface Agent {
  id: string;
  name: string;
  strategy_type: string;
  status: string;
  market: string;
  config: Record<string, unknown>;
  universe: unknown;
  risk_params: Record<string, unknown>;
  capital_allocated: number;
  created_at: string;
  updated_at: string;
}

export interface AgentCreate {
  name: string;
  strategy_type: string;
  market?: string;
  config?: Record<string, unknown>;
  universe?: unknown;
  risk_params?: Record<string, unknown>;
  capital_allocated?: number;
}

export function getAgents() {
  return request<Agent[]>("/api/agents");
}

export function getAgent(id: string) {
  return request<Agent>(`/api/agents/${id}`);
}

export function createAgent(data: AgentCreate) {
  return request<Agent>("/api/agents", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateAgent(id: string, data: Partial<AgentCreate>) {
  return request<Agent>(`/api/agents/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteAgent(id: string) {
  return request<void>(`/api/agents/${id}`, { method: "DELETE" });
}

export function startAgent(id: string) {
  return request<{ status: string }>(`/api/agents/${id}/start`, { method: "POST" });
}

export function stopAgent(id: string) {
  return request<{ status: string }>(`/api/agents/${id}/stop`, { method: "POST" });
}

// ── Trades ──

export interface Trade {
  id: string;
  agent_id: string;
  agent_name: string;
  symbol: string;
  exchange: string;
  side: string;
  quantity: number;
  price: number;
  order_type: string;
  status: string;
  fill_price: number | null;
  pnl: number | null;
  is_paper: boolean;
  executed_at: string | null;
  created_at: string;
}

export interface TradeSummary {
  total_trades: number;
  buy_count: number;
  sell_count: number;
  total_pnl: number;
  wins: number;
  losses: number;
  win_rate: number;
}

export function getTrades(params?: { agent_id?: string; symbol?: string; limit?: number }) {
  const qs = new URLSearchParams();
  if (params?.agent_id) qs.set("agent_id", params.agent_id);
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.limit) qs.set("limit", String(params.limit));
  return request<Trade[]>(`/api/trades?${qs}`);
}

export function getTradeSummary() {
  return request<TradeSummary>("/api/trades/summary/today");
}

// ── Risk ──

export interface RiskStatus {
  circuit_breaker_active: boolean;
  daily_loss: number;
  daily_loss_limit: number;
  open_positions: number;
  max_positions: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
}

export function getRiskStatus() {
  return request<RiskStatus>("/api/risk/status");
}

// ── Market ──

export interface Quote {
  symbol: string;
  ltp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change: number;
  change_pct: number;
}

export function getQuote(symbol: string) {
  return request<Quote>(`/api/market/quote/${symbol}`);
}

export function searchStocks(q: string) {
  return request<{ symbol: string; name: string }[]>(`/api/market/search?q=${q}`);
}
