import { useCallback, useEffect, useRef, useState } from "react";

/** Load data on mount and whenever `deps` change; `reload()` fetches again. Stale responses are ignored. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(load, deps); // `deps` decide when to refetch, like useEffect's

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const value = await run();
      if (mine === seq.current) {
        setData(value);
        setError(undefined);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [run]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload, setData };
}

export function money(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export const pct = (v: number) => `${Math.round(v * 100)}%`;

/** True when the text names dollar figures and all are zero ("$0.00", "$0 - $0"): how sources write "unknown".
 * Same rule and pattern as textutil.is_zero_amount. */
export function isZeroAmount(raw: string): boolean {
  const figures = [...raw.matchAll(/\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?/g)].map((m) =>
    Number(`${m[1]!.replaceAll(",", "")}.${m[2] ?? "0"}`),
  );
  return figures.length > 0 && figures.every((v) => v === 0);
}

export function amountLabel(display: string | undefined, value?: number | null): string {
  if (value) return money(value);
  if (!display || isZeroAmount(display)) return "Varies";
  return display;
}

/** One item per line or ';', blanks dropped (same rule as profile_io.split_lines). */
export const splitLines = (text: string) =>
  text
    .split(/[\n;]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const isoDay = (d: string) => new Date(`${d.slice(0, 10)}T00:00:00`);

/** Whole days from `asOf` (the run's evaluation date) to `deadline`; null when there is no deadline. */
export function daysUntil(deadline: string | null | undefined, asOf: string): number | null {
  if (!deadline) return null;
  return Math.round((isoDay(deadline).getTime() - isoDay(asOf).getTime()) / 86_400_000);
}

/** A short deadline such as "Jan 8" (no year: open deadlines fall within the coming year). */
export function shortDate(d: string | null | undefined): string {
  if (!d) return "—";
  return isoDay(d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const FIT = { strong: "Strong fit", moderate: "Good fit", weak: "Weak fit", none: "Low fit" } as const;

/** The fit chip's label; items without an AI judgement were scored by the rules alone. */
export function fitLabel(r: { judgement?: { fit: keyof typeof FIT } | null }): string {
  return r.judgement ? FIT[r.judgement.fit] : "Rule-based";
}
