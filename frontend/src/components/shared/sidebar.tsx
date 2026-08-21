"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: "◉" },
  { href: "/agents", label: "Agents", icon: "⬡" },
  { href: "/trades", label: "Trades", icon: "⇄" },
  { href: "/backtest", label: "Backtest", icon: "↻" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 flex h-screen w-56 flex-col border-r border-zinc-800 bg-zinc-950 px-3 py-5">
      <Link href="/" className="mb-8 flex items-center gap-2 px-3">
        <span className="text-xl font-bold text-emerald-400">TM</span>
        <span className="text-lg font-semibold text-zinc-100">TradeMind</span>
      </Link>

      <nav className="flex flex-1 flex-col gap-1">
        {NAV_ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              )}
            >
              <span className="text-base">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto border-t border-zinc-800 px-3 pt-4">
        <p className="text-xs text-zinc-600">Paper Trading Mode</p>
      </div>
    </aside>
  );
}
