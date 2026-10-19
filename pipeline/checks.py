#!/usr/bin/env python3
"""Independent checks against figures transcribed from third-party 13F sites (WhaleWisdom screenshots, Q2 2026).
Writes data/checks/<Q>.json. Does not fail the build: results are for review."""
import json, os, sys
import pandas as pd

DATA = os.environ.get("PANEL_DATA", "data")
KNOWN = {
    "2026Q2": [  # (manager name contains, issuer contains, shares held, expected status)
        ("AKRE CAPITAL", "MASTERCARD", 1989906, "trimmed"),
        ("AKRE CAPITAL", "COSTAR", 11699396, "added"),
        ("COATUE", "MICRON", 3142269, "added"),
        ("COATUE", "INTEL", 12084027, "new"),
        ("LONE PINE", "NEBIUS", 4258008, "new"),
        ("VIKING GLOBAL", "VISA", 6393604, "added"),
        ("FUNDSMITH", "UBER", 8943151, "new"),
        ("HIMALAYA CAPITAL", "PDD", 10761119, "added"),
    ],
    "positions": {"2026Q2": [("HIMALAYA CAPITAL", 8)]},
}


def main(q):
    f = pd.read_parquet(os.path.join(DATA, "features", f"{q}.parquet"))
    h = pd.read_parquet(os.path.join(DATA, "holdings", f"{q}.parquet"))
    px = pd.read_parquet(os.path.join(DATA, "prices", f"{q}.parquet"))
    h = h.merge(px[["cusip", "issuer"]], on="cusip", how="left")
    res = []
    for mgr, iss, sh, status in KNOWN.get(q, []):
        ciks = f[f.name.fillna("").str.upper().str.contains(mgr)].sort_values("aum", ascending=False).cik
        row = {"manager": mgr, "issuer": iss, "expected_shares": sh, "expected_status": status}
        if ciks.empty:
            row.update(result="manager not found")
        else:
            x = h[(h.cik == ciks.iloc[0]) & h.issuer.fillna("").str.upper().str.contains(iss)]
            got = float(x.sh.sum()) if len(x) else None
            row.update(cik=ciks.iloc[0], got_shares=got, got_status=",".join(sorted(set(x.status))) if len(x) else None,
                       result="pass" if got == sh else "FAIL")
        res.append(row)
    for mgr, n in KNOWN["positions"].get(q, []):
        x = f[f.name.fillna("").str.upper().str.contains(mgr)].sort_values("aum", ascending=False)
        got = int(x.n_positions.iloc[0]) if len(x) else None
        res.append({"manager": mgr, "expected_positions": n, "got_positions": got, "result": "pass" if got == n else "FAIL"})
    out = {"quarter": q, "passed": sum(r["result"] == "pass" for r in res), "total": len(res), "checks": res}
    os.makedirs(os.path.join(DATA, "checks"), exist_ok=True)
    json.dump(out, open(os.path.join(DATA, "checks", f"{q}.json"), "w"), indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("quarter", "passed", "total")}))


if __name__ == "__main__":
    for q in sys.argv[1:]:
        if os.path.exists(os.path.join(DATA, "features", f"{q}.parquet")):
            main(q)
