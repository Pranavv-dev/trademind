import { cn } from "@/lib/utils";

interface StatusDotProps {
  connected: boolean;
  className?: string;
}

export function StatusDot({ connected, className }: StatusDotProps) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        connected ? "bg-emerald-400" : "bg-red-400",
        className
      )}
      title={connected ? "Connected" : "Disconnected"}
    />
  );
}
