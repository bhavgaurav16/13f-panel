#!/usr/bin/env python3
"""Whole-history audit gate. Runs after every build and FAILS the workflow (red X, message in the log)
if any guardrail is breached, so bad data is caught by GitHub, not discovered later. Writes data/audit.json."""
import glob, json, os, sys
import numpy as np
import pandas as pd

DATA = os.environ.get("PANEL_DATA", "data")
LIMITS = {"tier_net_flow_pct": 10.0, "total_aum_qoq": 0.35, "largest_filer": 15e12, "split_suspect_pct_held": 0.5}


def main():
    fails, warns, tiers = [], [], []
    feats = sorted(glob.glob(os.path.join(DATA, "features", "*.parquet")))
    prev_total = None
    for p in feats:
        q = os.path.basename(p)[:6]
        f = pd.read_parquet(p)
        total = f.aum.sum()
        if f.aum.max() >= LIMITS["largest_filer"]:
            fails.append(f"{q}: filer {f.loc[f.aum.idxmax(), 'name']} reports ${f.aum.max()/1e12:.1f}tn")
        if prev_total and abs(total / prev_total - 1) > LIMITS["total_aum_qoq"]:
            fails.append(f"{q}: total 13F value moved {total/prev_total-1:+.0%} vs prior quarter")
        prev_total = total
        if "aum_prior" not in f or "net_flow_pct" not in f:
            continue
        c = f[f.aum_prior.notna() & f.net_flow_pct.notna()].copy()
        if "discontinuous" in c:
            c = c[~c.discontinuous.fillna(False).astype(bool)]
        c["net"] = c.net_flow_pct * c.aum_prior
        top = c.nlargest(10, "aum").index
        c["tier"] = np.select([c.index.isin(top), c.aum >= 10e9, c.aum >= 1e9, c.aum >= 100e6],
                              ["top10", "t10b", "t1b", "t100m"], "sub100m")
        g = c[c.tier != "sub100m"].groupby("tier").agg(net=("net", "sum"), prior=("aum_prior", "sum"))
        for t, r in g.iterrows():
            pct = 100 * r.net / r.prior
            tiers.append({"quarter": q, "tier": t, "net_flow_pct": round(float(pct), 2)})
            if abs(pct) > LIMITS["tier_net_flow_pct"]:
                fails.append(f"{q}: tier {t} net flow {pct:+.1f}% of AUM")
    # dashboard file, if present
    hp = os.path.join(DATA, "dashboard", "history.json")
    if os.path.exists(hp):
        h = json.load(open(hp))
        for q, d in h["quarters"].items():
            for t, v in d["tiers"].items():
                if v.get("net_flow_pct") is not None and abs(v["net_flow_pct"]) * 100 > LIMITS["tier_net_flow_pct"]:
                    fails.append(f"dashboard {q}: tier {t} net flow {v['net_flow_pct']*100:+.1f}%")
                for x in v.get("top_buys", [])[:10] + v.get("top_sells", [])[:10]:
                    # a stock whose net flow exceeds half of everything held, bought by nearly all holders: a missed split
                    one_sided = max(x["buyers"], x["sellers"]) > 5 * max(1, min(x["buyers"], x["sellers"]))
                    not_ipo = x["new"] < 0.5 * max(1, x["buyers"])          # IPOs: nearly every buyer is new
                    not_deal = x["exits"] < 0.5 * max(1, x["sellers"])      # takeovers: nearly every seller exits
                    if x.get("held") and x.get("flow_pct_held") and abs(x["flow_pct_held"]) > LIMITS["split_suspect_pct_held"] \
                            and one_sided and not_ipo and not_deal:
                        warns.append(f"dashboard {q} {t}: {x['issuer']} flow {x['flow_pct_held']:+.0%} of held - check for missed split")
    out = {"passed": not fails, "failures": fails, "warnings": sorted(set(warns)), "quarters": len(feats), "tiers": tiers}
    json.dump(out, open(os.path.join(DATA, "audit.json"), "w"), indent=1)
    print(f"AUDIT: {len(feats)} quarters, {len(fails)} failures, {len(set(warns))} warnings")
    for m in fails:
        print("FAIL", m)
    for m in sorted(set(warns)):
        print("WARN", m)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
