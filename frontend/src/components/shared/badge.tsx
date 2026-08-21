import { cn } from "@/lib/utils";

type Variant = "default" | "success" | "danger" | "warning" | "info";

const variants: Record<Variant, string> = {
  default: "bg-zinc-800 text-zinc-300",
  success: "bg-emerald-900/50 text-emerald-400",
  danger: "bg-red-900/50 text-red-400",
  warning: "bg-amber-900/50 text-amber-400",
  info: "bg-blue-900/50 text-blue-400",
};

interface BadgeProps {
  children: React.ReactNode;
  variant?: Variant;
  className?: string;
}

export function Badge({ children, variant = "default", className }: BadgeProps) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", variants[variant], className)}>
      {children}
    </span>
  );
}
