// Poll `fn` every `ms` while the tab is visible; back off exponentially (max 60s) on errors. Returns stop().
export function startPolling(fn, ms, { onError = () => {}, isVisible = () => typeof document === "undefined" || !document.hidden, setT = setTimeout, clearT = clearTimeout } = {}) {
  let stopped = false, timer = null, fails = 0;
  const tick = async () => {
    if (stopped) return;
    let delay = ms;
    if (isVisible()) {
      try { await fn(); fails = 0; } catch (e) { fails += 1; onError(e); delay = Math.min(60000, ms * 2 ** fails); }
    }
    if (!stopped) timer = setT(tick, delay);
  };
  tick();
  return () => { stopped = true; clearT(timer); };
}
