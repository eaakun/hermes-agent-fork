"""OpenCode Zen free-pool status display.

Shared by the interactive ``/free-pool`` slash command (``cli.py``) and the
top-level ``hermes free-pool`` / ``hermes fp`` CLI subcommand (``main.py``).
"""

def print_free_pool_status(probe=False):
    """Print live pool, per-model health, circuit-break, cache age, last outage."""
    try:
        from hermes_cli.models import (
            opencode_zen_free_rotation_pool,
            _free_pool_candidates,
            _FREE_MODEL_HEALTH,
            _FREE_POOL_CACHE,
            _FREE_POOL_TICKER_STARTED,
            _free_pool_outage_state_path,
            _get_free_pool_cfg,
            _free_pool_health_path,
        )
        import json
        import os
        import time

        cfg = _get_free_pool_cfg()
        print("=== OpenCode Zen free-pool status ===")
        print("enabled: %s  ticker: %s (interval=%ss)" % (
            cfg.get("enabled"),
            "on" if _FREE_POOL_TICKER_STARTED[0] else "off",
            cfg.get("background_probe_interval"),
        ))
        models_cached = _FREE_POOL_CACHE.get("models")
        if models_cached is not None:
            age = time.time() - _FREE_POOL_CACHE.get("ts", 0.0)
            print("cached live pool: %s  (age: %.0fs)" % (models_cached, age))
        else:
            print("cached live pool: <empty> (populated by gateway ticker or --probe)")
        if probe:
            print("probing now (force=True)...")
            pool = opencode_zen_free_rotation_pool(force=True)
            print("rotation pool now: %s" % pool)
        else:
            print("rotation pool now: (cached; pass --probe to force a live check)")
        print("candidates: %s" % _free_pool_candidates())
        print("\nper-model health (ok/fail/cf -> score, circuit-broken?):")
        cb = int(cfg.get("circuit_break_failures", 3))
        for m in _free_pool_candidates():
            h = _FREE_MODEL_HEALTH.get(m, {})
            ok = h.get("ok", 0)
            fail = h.get("fail", 0)
            cf = h.get("cf", 0)
            score = ok - fail
            broken = cf >= cb
            flag = "  [CIRCUIT-BROKEN]" if broken else ""
            print("  %-35s ok=%d fail=%d cf=%d score=%+d%s" % (
                m, ok, fail, cf, score, flag))
        op = _free_pool_outage_state_path()
        if os.path.exists(op):
            try:
                d = json.loads(open(op).read())
                when = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(d.get("last_at", 0))
                )
                print("\nlast full outage: consecutive=%s at=%s" % (
                    d.get("consecutive"), when))
            except Exception:
                pass
        else:
            print("\nlast full outage: none recorded")
        hp = _free_pool_health_path()
        print("persisted health file: %s (%s)" % (
            "exists" if os.path.exists(hp) else "absent", hp))
        print("feishu alert: %s" % ("on" if cfg.get("feishu_alert") else "off"))
    except Exception as _e:
        print("  free-pool status error: %s" % _e)
