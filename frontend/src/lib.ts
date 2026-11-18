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
