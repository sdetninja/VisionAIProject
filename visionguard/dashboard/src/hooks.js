import { useEffect, useRef, useState } from "react";
import { startPolling } from "./polling.js";

// Fetch + auto-refresh. `load(signal)` returns data. Keeps last good data on error (shows stale banner via `error`).
export function usePolling(load, deps, ms = 5000) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const ref = useRef(load);
  ref.current = load;
  useEffect(() => {
    const ctl = new AbortController();
    setState((s) => ({ ...s, loading: true }));
    const stop = startPolling(async () => {
      const data = await ref.current(ctl.signal);
      setState({ data, error: null, loading: false });
    }, ms, { onError: (e) => { if (e.name !== "AbortError") setState((s) => ({ ...s, error: e, loading: false })); } });
    return () => { ctl.abort(); stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
