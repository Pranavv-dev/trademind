"use client";

import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle } from "@/components/shared/card";
import { Badge } from "@/components/shared/badge";

interface Settings {
  paper_trading: boolean;
  api_base_url: string;
  ws_url: string;
  risk_daily_loss_limit: number;
  risk_max_positions: number;
  risk_max_drawdown_pct: number;
  risk_max_position_size_pct: number;
  telegram_enabled: boolean;
  telegram_token: string;
  telegram_chat_id: string;
  gemini_api_key: string;
  zerodha_api_key: string;
  zerodha_api_secret: string;
}

const DEFAULT_SETTINGS: Settings = {
  paper_trading: true,
  api_base_url: "http://localhost:5000",
  ws_url: "ws://localhost:5000/ws/live",
  risk_daily_loss_limit: 50000,
  risk_max_positions: 10,
  risk_max_drawdown_pct: 5,
  risk_max_position_size_pct: 20,
  telegram_enabled: false,
  telegram_token: "",
  telegram_chat_id: "",
  gemini_api_key: "",
  zerodha_api_key: "",
  zerodha_api_secret: "",
};

const STORAGE_KEY = "trademind_settings";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(raw) });
      } catch { /* ignore corrupt data */ }
    }
  }, []);

  function update<K extends keyof Settings>(key: K, value: Settings[K]) {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  }

  function handleSave() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleReset() {
    setSettings(DEFAULT_SETTINGS);
    localStorage.removeItem(STORAGE_KEY);
    setSaved(false);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Settings</h1>
          <p className="text-sm text-zinc-500">Configure TradeMind</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleReset}
            className="rounded-md border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
          >
            Reset
          </button>
          <button
            onClick={handleSave}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
          >
            {saved ? "Saved!" : "Save Settings"}
          </button>
        </div>
      </div>

      {/* Trading Mode */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Trading Mode</CardTitle>
            <Badge variant={settings.paper_trading ? "warning" : "danger"}>
              {settings.paper_trading ? "Paper Trading" : "Live Trading"}
            </Badge>
          </div>
        </CardHeader>
        <div className="flex items-center gap-3">
          <label className="relative inline-flex cursor-pointer items-center">
            <input
              type="checkbox"
              checked={settings.paper_trading}
              onChange={(e) => update("paper_trading", e.target.checked)}
              className="peer sr-only"
            />
            <div className="h-6 w-11 rounded-full bg-zinc-700 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-zinc-300 after:transition-all peer-checked:bg-emerald-600 peer-checked:after:translate-x-full" />
          </label>
          <span className="text-sm text-zinc-400">
            {settings.paper_trading
              ? "Orders are simulated — no real money at risk"
              : "WARNING: Orders will be placed with real money"}
          </span>
        </div>
      </Card>

      {/* Risk Management */}
      <Card>
        <CardHeader><CardTitle>Risk Management</CardTitle></CardHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <SettingsField
            label="Daily Loss Limit (INR)"
            type="number"
            value={settings.risk_daily_loss_limit}
            onChange={(v) => update("risk_daily_loss_limit", Number(v))}
          />
          <SettingsField
            label="Max Open Positions"
            type="number"
            value={settings.risk_max_positions}
            onChange={(v) => update("risk_max_positions", Number(v))}
          />
          <SettingsField
            label="Max Drawdown (%)"
            type="number"
            value={settings.risk_max_drawdown_pct}
            onChange={(v) => update("risk_max_drawdown_pct", Number(v))}
          />
          <SettingsField
            label="Max Position Size (%)"
            type="number"
            value={settings.risk_max_position_size_pct}
            onChange={(v) => update("risk_max_position_size_pct", Number(v))}
          />
        </div>
      </Card>

      {/* Connection */}
      <Card>
        <CardHeader><CardTitle>Connection</CardTitle></CardHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <SettingsField
            label="API Base URL"
            value={settings.api_base_url}
            onChange={(v) => update("api_base_url", v)}
          />
          <SettingsField
            label="WebSocket URL"
            value={settings.ws_url}
            onChange={(v) => update("ws_url", v)}
          />
        </div>
      </Card>

      {/* API Keys */}
      <Card>
        <CardHeader><CardTitle>API Keys</CardTitle></CardHeader>
        <div className="grid gap-4">
          <SettingsField
            label="Gemini API Key"
            type="password"
            value={settings.gemini_api_key}
            onChange={(v) => update("gemini_api_key", v)}
            placeholder="AIza..."
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <SettingsField
              label="Zerodha API Key"
              type="password"
              value={settings.zerodha_api_key}
              onChange={(v) => update("zerodha_api_key", v)}
            />
            <SettingsField
              label="Zerodha API Secret"
              type="password"
              value={settings.zerodha_api_secret}
              onChange={(v) => update("zerodha_api_secret", v)}
            />
          </div>
        </div>
      </Card>

      {/* Notifications */}
      <Card>
        <CardHeader><CardTitle>Notifications (Telegram)</CardTitle></CardHeader>
        <div className="mb-4 flex items-center gap-3">
          <label className="relative inline-flex cursor-pointer items-center">
            <input
              type="checkbox"
              checked={settings.telegram_enabled}
              onChange={(e) => update("telegram_enabled", e.target.checked)}
              className="peer sr-only"
            />
            <div className="h-6 w-11 rounded-full bg-zinc-700 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-zinc-300 after:transition-all peer-checked:bg-emerald-600 peer-checked:after:translate-x-full" />
          </label>
          <span className="text-sm text-zinc-400">Enable Telegram notifications</span>
        </div>
        {settings.telegram_enabled && (
          <div className="grid gap-4 sm:grid-cols-2">
            <SettingsField
              label="Bot Token"
              type="password"
              value={settings.telegram_token}
              onChange={(v) => update("telegram_token", v)}
            />
            <SettingsField
              label="Chat ID"
              value={settings.telegram_chat_id}
              onChange={(v) => update("telegram_chat_id", v)}
            />
          </div>
        )}
      </Card>
    </div>
  );
}

function SettingsField({
  label,
  type = "text",
  value,
  onChange,
  placeholder,
}: {
  label: string;
  type?: string;
  value: string | number;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs text-zinc-400">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500 focus:outline-none"
      />
    </div>
  );
}
