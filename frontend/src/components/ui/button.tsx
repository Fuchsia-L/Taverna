import * as React from "react";
import { cn } from "@/lib/utils";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline";
}

export function Button({ className, variant = "default", ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex h-11 items-center justify-center rounded-md border px-4 py-2 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 disabled:pointer-events-none disabled:opacity-50",
        variant === "default" &&
          "border-app-border/45 bg-app-accent text-white hover:bg-app-accent/85 focus-visible:ring-app-info",
        variant === "outline" &&
          "border-app-border/45 bg-app-surface text-app-text hover:bg-app-surfaceAlt focus-visible:ring-app-accent",
        className
      )}
      {...props}
    />
  );
}
