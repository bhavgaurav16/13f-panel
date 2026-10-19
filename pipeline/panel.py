#!/usr/bin/env python3
"""
Manager panel builder for SEC Form 13F bulk data sets.

For each calendar quarter Q it writes (all parquet, small enough for git):
  data/features/Q.parquet   one row per 13F filer: size, concentration, turnover, trim pattern, flags
  data/holdings/Q.parquet   position-level book for candidate managers (focused, not index/wealth)
  data/prices/Q.parquet     per CUSIP: issuer, median implied quarter-end price, holders, total held
  data/manifest.json        what was processed, from which SEC files, with row counts and checks

Usage:
  python pipeline/panel.py --plan [--start 2013Q2] [--end 2026Q2]   # print quarters still to build
  python pipeline/panel.py --quarter 2026Q2                          # build one quarter
"""
import argparse, hashlib, json, os, re, sys, zipfile, datetime as dt
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "13F research contact@example.com"),
      "Accept-Encoding": "gzip, deflate"}
PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
DATA = os.environ.get("PANEL_DATA", "data")
CACHE = os.environ.get("PANEL_CACHE", "cache")
FIRST_Q = "2013Q2"                      # first quarter of the XML-era bulk data sets
UNITS_CUTOFF = pd.Timestamp("2023-01-03")  # filings before this reported VALUE in $000s

# Candidate = plausible stock-picker worth keeping full holdings for
CAND = dict(min_aum=50e6, max_positions=200, max_etf_wt=0.5)

ETF_PAT = re.compile(r"\b(?:ETF|ETN|SPDR|ISHARES|INDEX FD|INDEX FUND|SELECT SECTOR|VANGUARD|INVESCO QQQ|PROSHARES|DIREXION|"
                     r"WISDOMTREE|SCHWAB STRATEGIC|TRUST UNIT|SER TR|ETF TR|FDS TR|EXCHANGE TRADED|JPMORGAN EXCHANGE|"
                     r"DIMENSIONAL ETF|FIRST TR|GLOBAL X|ARK ETF|VANECK|PIMCO ETF|AMPLIFY|BLACKROCK ETF|ISHARES TR|SPDR S&P|"
                     r"SPDR SER|SPDR GOLD|SPDR INDEX|SECTOR SPDR)\b", re.I)


# ----------------------------------------------------------------------------- quarters
def qlabel(d):
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def qend(q):
    y, n = int(q[:4]), int(q[-1])
    return pd.Timestamp(y, 3 * n, 1) + pd.offsets.MonthEnd(0)


def prev_q(q):
    y, n = int(q[:4]), int(q[-1])
    return f"{y - 1}Q4" if n == 1 else f"{y}Q{n - 1}"


def next_q(q):
    y, n = int(q[:4]), int(q[-1])
    return f"{y + 1}Q1" if n == 4 else f"{y}Q{n + 1}"


def q_range(a, b):
    if a > b:
        return []
    out = [a]
    while out[-1] < b:
        out.append(next_q(out[-1]))
    return out


# ----------------------------------------------------------------------------- SEC files
def list_zips(html=None):
    html = html if html is not None else requests.get(PAGE, headers=UA, timeout=60).text
    urls = sorted(set(re.findall(r'href="([^"]+_form13f\.zip)"', html, re.I)))
    return [u if u.startswith("http") else "https://www.sec.gov" + u for u in urls]


def window(u):
    """Filing-date window covered by a data-set file. Handles both naming schemes."""
    name = u.rsplit("/", 1)[-1].lower()
    m = re.search(r"(\d{2}[a-z]{3}\d{4})-(\d{2}[a-z]{3}\d{4})_form13f", name)
    if m:
        return (pd.Timestamp(dt.datetime.strptime(m.group(1), "%d%b%Y")),
                pd.Timestamp(dt.datetime.strptime(m.group(2), "%d%b%Y")))
    m = re.search(r"(\d{4})q([1-4])_form13f", name)
    if m:
        s = pd.Timestamp(int(m.group(1)), 3 * int(m.group(2)) - 2, 1)
        return s, s + pd.offsets.QuarterEnd(0)
    return None


def zips_for(urls, q):
    """Files whose filing window overlaps [quarter end, quarter end + 5 months] (captures late filers)."""
    lo, hi = qend(q), qend(q) + pd.DateOffset(months=5)
    out = [u for u in urls if window(u) and window(u)[1] >= lo and window(u)[0] <= hi]
    return sorted(out, key=lambda u: window(u)[0])


def latest_complete(urls):
    """Latest quarter whose main filing window (quarter end + 45 days) is covered by posted files."""
    last = max(window(u)[1] for u in urls if window(u))
    q = qlabel(last - pd.DateOffset(days=46))
    return q if qend(q) + pd.DateOffset(days=45) <= last else prev_q(q)


def fetch(url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, url.rsplit("/", 1)[-1])
    if not os.path.exists(path):
        print("downloading", url, file=sys.stderr)
        with requests.get(url, headers=UA, timeout=900, stream=True) as r:
            r.raise_for_status()
            with open(path + ".part", "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(path + ".part", path)
    return path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------- parsing
def _read(z, names, name, cols=None):
    key = name.upper()
    if key not in names:
        return None
    with z.open(names[key]) as f:
        df = pd.read_csv(f, sep="\t", dtype=str, quoting=3, on_bad_lines="skip", low_memory=False)
    df.columns = [c.upper().strip() for c in df.columns]
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return df


def load_quarter(paths, q):
    """Return (info, filers, checks) for period-of-report quarter q."""
    subs, covs, sums, infos = [], [], [], []
    for p in paths:
        z = zipfile.ZipFile(p)
        names = {n.rsplit("/", 1)[-1].upper(): n for n in z.namelist()}
        s = _read(z, names, "SUBMISSION.TSV")
        s["PERIOD"] = pd.to_datetime(s["PERIODOFREPORT"], format="%d-%b-%Y", errors="coerce")
        s = s[s["PERIOD"].map(lambda d: pd.notna(d) and qlabel(d) == q)]
        s = s[s["SUBMISSIONTYPE"].str.upper().str.startswith("13F-HR", na=False)]
        if s.empty:
            continue
        acc = set(s["ACCESSION_NUMBER"])
        c = _read(z, names, "COVERPAGE.TSV", ["ACCESSION_NUMBER", "FILINGMANAGER_NAME", "ISAMENDMENT", "AMENDMENTTYPE"])
        sm = _read(z, names, "SUMMARYPAGE.TSV", ["ACCESSION_NUMBER", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"])
        i = _read(z, names, "INFOTABLE.TSV", ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "VALUE",
                                               "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"])
        subs.append(s)
        covs.append(c[c["ACCESSION_NUMBER"].isin(acc)])
        if sm is not None:
            sums.append(sm[sm["ACCESSION_NUMBER"].isin(acc)])
        infos.append(i[i["ACCESSION_NUMBER"].isin(acc)])
    if not subs:
        raise SystemExit(f"no 13F-HR submissions for {q} in {paths}")
    sub = pd.concat(subs).drop_duplicates("ACCESSION_NUMBER")
    cov = pd.concat(covs).drop_duplicates("ACCESSION_NUMBER")
    summ = pd.concat(sums).drop_duplicates("ACCESSION_NUMBER") if sums else pd.DataFrame(columns=["ACCESSION_NUMBER"])
    info = pd.concat(infos)

    sub["FILED"] = pd.to_datetime(sub["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    sub = sub.merge(cov, on="ACCESSION_NUMBER", how="left")
    for col in ("ISAMENDMENT", "AMENDMENTTYPE", "FILINGMANAGER_NAME"):
        if col not in sub:
            sub[col] = None
    sub["ISAMEND"] = (sub["ISAMENDMENT"].fillna("N").str.upper() == "Y") | sub["SUBMISSIONTYPE"].str.upper().str.endswith("/A")
    sub["NEWH"] = sub["ISAMEND"] & sub["AMENDMENTTYPE"].fillna("").str.upper().str.contains("NEW HOLDINGS")
    base = sub[~sub["NEWH"]].sort_values("FILED").groupby("CIK").tail(1)
    newh = sub[sub["NEWH"]].merge(base[["CIK", "FILED"]].rename(columns={"FILED": "BFILED"}), on="CIK")
    newh = newh[newh["FILED"] >= newh["BFILED"]]
    chosen = pd.concat([base, newh[base.columns]])

    info = info.merge(chosen[["ACCESSION_NUMBER", "CIK", "FILED"]], on="ACCESSION_NUMBER", how="inner")
    info["VALUE"] = pd.to_numeric(info["VALUE"], errors="coerce").fillna(0.0).astype("float64")
    info["SSHPRNAMT"] = pd.to_numeric(info["SSHPRNAMT"], errors="coerce").fillna(0.0).astype("float64")
    info["CUSIP"] = info["CUSIP"].fillna("").str.upper().str.strip().str.zfill(9).str[:9]
    info["PUTCALL"] = info["PUTCALL"].fillna("").str.upper().str.strip()
    info["SSHPRNAMTTYPE"] = info["SSHPRNAMTTYPE"].fillna("").str.upper().str.strip()

    # cover-page reconciliation (before unit fixes, both sides in reported units)
    checks = {}
    if "TABLEVALUETOTAL" in summ.columns and len(summ):
        tot = info.groupby("ACCESSION_NUMBER")["VALUE"].sum()
        rep = pd.to_numeric(summ.set_index("ACCESSION_NUMBER")["TABLEVALUETOTAL"], errors="coerce")
        j = pd.concat([tot.rename("calc"), rep.rename("rep")], axis=1, join="inner").dropna()
        j = j[j.rep > 0]
        ok = (j.calc / j.rep - 1).abs() <= 0.005
        checks["coverpage_value_match_share"] = float(ok.mean()) if len(j) else None
        checks["coverpage_filings_compared"] = int(len(j))

    # units: pre-2023 filings reported $000s
    info.loc[info["FILED"] < UNITS_CUTOFF, "VALUE"] *= 1000.0
    info, nfix = fix_units(info)
    info, nrow, nhuge = fix_rows(info)
    checks["unit_fixes"] = nfix
    checks["row_value_fixes"] = nrow
    checks["rows_zeroed"] = nhuge

    names = chosen.sort_values("FILED").groupby("CIK")["FILINGMANAGER_NAME"].last()
    filed = chosen.groupby("CIK")["FILED"].max()
    filers = pd.DataFrame({"name": names, "filed": filed})
    return info, filers, checks


def fix_units(info):
    """Per filer, compare implied prices with the cross-filer median; rescale filers off by ~1000x."""
    eq = info[(info.SSHPRNAMTTYPE == "SH") & (info.PUTCALL == "") & (info.SSHPRNAMT > 0) & (info.VALUE > 0)]
    px = eq.VALUE / eq.SSHPRNAMT
    med = px.groupby(eq.CUSIP).transform("median")
    r = (px / med).groupby(eq.CIK).median()
    up, down = r[r < 0.01].index, r[r > 100].index
    info.loc[info.CIK.isin(up), "VALUE"] *= 1000.0
    info.loc[info.CIK.isin(down), "VALUE"] /= 1000.0
    return info, int(len(up) + len(down))


def fix_rows(info):
    """Line-level sanity check. A holding line whose implied price is >50x or <1/50x the cross-filer median
    for that CUSIP (3+ holders) is a data-entry error. Always take the smaller reading: revalue an inflated value
    at shares x median price, or restate inflated shares as value / median price. Lines above
    $1.5tn (larger than any plausible single position) with no usable median are zeroed. Returns counts."""
    sh = (info.SSHPRNAMTTYPE == "SH") & (info.SSHPRNAMT > 0) & (info.VALUE > 0)
    eq = info[sh & (info.PUTCALL == "")]
    g = (eq.VALUE / eq.SSHPRNAMT).groupby(eq.CUSIP).agg(["median", "count"])
    med = info.CUSIP.map(g["median"])
    cnt = info.CUSIP.map(g["count"]).fillna(0)
    ratio = info.VALUE / (info.SSHPRNAMT * med)
    hi = sh & (cnt >= 3) & (ratio > 50)     # value typo: revalue at shares x median price
    lo = sh & (cnt >= 3) & (ratio < 0.02)   # share-count typo (or value in $000s): keep value, restate shares
    info.loc[hi, "VALUE"] = info.loc[hi, "SSHPRNAMT"] * med[hi]
    info.loc[lo, "SSHPRNAMT"] = info.loc[lo, "VALUE"] / med[lo]
    bad = hi | lo
    huge = info.VALUE > 1.5e12
    info.loc[huge, "VALUE"] = 0.0
    return info, int(bad.sum()), int(huge.sum())


# ----------------------------------------------------------------------------- books
def books(info):
    long_ = info[(info.SSHPRNAMTTYPE == "SH") & (info.PUTCALL == "")]
    book = long_.groupby(["CIK", "CUSIP"], as_index=False).agg(SH=("SSHPRNAMT", "sum"), VAL=("VALUE", "sum"))
    issuer = long_.groupby("CUSIP")["NAMEOFISSUER"].agg(lambda s: s.mode().iat[0] if len(s.mode()) else s.iat[0])
    opts = info[info.PUTCALL.isin(["PUT", "CALL"])].groupby(["CIK", "PUTCALL"])["VALUE"].sum().unstack(fill_value=0.0)
    for k in ("PUT", "CALL"):
        if k not in opts:
            opts[k] = 0.0
    return book, issuer, opts


def prices(book, issuer):
    b = book[(book.SH > 0) & (book.VAL > 0)]
    g = b.assign(PX=b.VAL / b.SH).groupby("CUSIP").agg(price=("PX", "median"), holders=("CIK", "nunique"),
                                                         shares_held=("SH", "sum"), value_held=("VAL", "sum"))
    g["issuer"] = g.index.map(issuer)
    g["is_etf"] = g["issuer"].fillna("").str.contains(ETF_PAT)
    return g


def splits(cur, prv):
    m = cur.merge(prv, on=["CIK", "CUSIP"], suffixes=("", "_P"))
    m = m[(m.SH > 0) & (m.SH_P > 0)]
    r = m.SH / m.SH_P
    ratio = r.groupby(m.CUSIP).agg(["median", "count"])
    cand = ratio[(ratio["count"] >= 10) & ((ratio["median"] > 1.8) | (ratio["median"] < 0.55))]["median"]
    common = np.array([1/20, 1/10, 1/8, 1/5, 1/4, 1/3, 1/2, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50])
    def snap(x):
        d = np.abs(np.log(common / x))
        return common[np.argmin(d)] if d.min() < 0.08 else np.nan
    snapped = cand.map(snap).dropna()
    # a real split moves nearly every holder by the same ratio; require 70%+ of holders within 10% of it
    near = (np.abs(np.log(r / m.CUSIP.map(snapped))) < 0.1).groupby(m.CUSIP).mean()
    return snapped[near.reindex(snapped.index).fillna(0) >= 0.7]


def features(q, cur, prv, opts, px, filers):
    aum = cur.groupby("CIK").VAL.sum()
    aum = aum[aum > 0]
    cur = cur[cur.CIK.isin(aum.index)]
    etf = cur.CUSIP.map(px["is_etf"]).fillna(False).astype(bool)
    w = cur.VAL / cur.CIK.map(aum)
    mkt = cur.groupby("CUSIP").VAL.sum() / cur.VAL.sum()          # aggregate 13F book as the market proxy
    f = pd.DataFrame({"aum": aum})
    f["n_positions"] = cur[cur.VAL > 0].groupby("CIK").size()
    f["eff_n"] = 1.0 / (w ** 2).groupby(cur.CIK).sum()
    srt = cur.assign(W=w).sort_values(["CIK", "W"], ascending=[True, False])
    f["top10_wt"] = srt.groupby("CIK").head(10).groupby("CIK").W.sum()
    f["top1_wt"] = srt.groupby("CIK").head(1).groupby("CIK").W.sum()
    f["etf_wt"] = cur[etf].groupby("CIK").VAL.sum().reindex(f.index).fillna(0) / f.aum
    # active share vs aggregate 13F market: 0.5 * sum|w - w_mkt|, held names + unheld market weight
    wm = cur.CUSIP.map(mkt)
    held_mkt = wm.groupby(cur.CIK).sum()
    f["active_share"] = 0.5 * ((w - wm).abs().groupby(cur.CIK).sum() + (1 - held_mkt))
    o = opts.reindex(f.index).fillna(0.0)
    f["put_notional_wt"] = o["PUT"] / f.aum
    f["call_notional_wt"] = o["CALL"] / f.aum

    hold = None
    if prv is not None:
        sp = splits(cur, prv)
        p = prv.copy()
        p["SH"] = p.SH * p.CUSIP.map(sp).fillna(1.0)
        aum_p = p.groupby("CIK").VAL.sum()
        both = f.index.intersection(aum_p.index)
        m = cur[cur.CIK.isin(both)].merge(p[p.CIK.isin(both)], on=["CIK", "CUSIP"], how="outer",
                                          suffixes=("", "_P")).fillna(0.0)
        m["PX"] = m.CUSIP.map(px["price"]).fillna(0.0)
        m["DSH"] = m.SH - m.SH_P
        m["FLOW"] = np.where(m.DSH.abs() >= 1, m.DSH * m.PX, 0.0)
        m["STATUS"] = np.select([(m.SH_P <= 0) & (m.SH > 0), (m.SH_P > 0) & (m.SH <= 0),
                                 m.DSH >= 1, m.DSH <= -1], ["new", "exit", "added", "trimmed"], "unchanged")
        g = m.groupby("CIK")
        buys = g.FLOW.apply(lambda s: s.clip(lower=0).sum())
        sells = g.FLOW.apply(lambda s: -s.clip(upper=0).sum())
        avg = (f.aum.reindex(both) + aum_p.reindex(both)) / 2
        f.loc[both, "aum_prior"] = aum_p.reindex(both)
        f.loc[both, "gross_buys"] = buys
        f.loc[both, "gross_sells"] = sells
        f.loc[both, "net_flow_pct"] = (buys - sells) / aum_p.reindex(both)
        f.loc[both, "turnover"] = np.minimum(buys, sells) / avg
        f.loc[both, "new_wt"] = m[m.STATUS == "new"].groupby("CIK").VAL.sum().reindex(both).fillna(0) / f.aum.reindex(both)
        f.loc[both, "exit_wt_prior"] = m[m.STATUS == "exit"].groupby("CIK").VAL_P.sum().reindex(both).fillna(0) / aum_p.reindex(both)
        cont = m[(m.SH_P > 0) & (m.SH > 0)]
        cg = cont.groupby("CIK")
        f.loc[both, "n_continuing"] = cg.size().reindex(both).fillna(0)
        # redemption signature: share of continuing positions trimmed, and how similar the trims are
        f.loc[both, "trim_breadth"] = cg.apply(lambda d: (d.DSH <= -1).mean()).reindex(both)
        chg = (cont.SH / cont.SH_P - 1)
        trims = chg[chg < -0.005]
        f.loc[both, "trim_median"] = trims.groupby(cont.CIK).median().reindex(both)
        f.loc[both, "trim_dispersion"] = trims.groupby(cont.CIK).std().reindex(both)
        hold = m
    f["name"] = filers["name"].reindex(f.index)
    f["filed"] = filers["filed"].reindex(f.index)
    f["is_candidate"] = (f.aum >= CAND["min_aum"]) & (f.n_positions <= CAND["max_positions"]) & (f.etf_wt <= CAND["max_etf_wt"])
    f["quarter"] = q
    f.index.name = "cik"
    return f.reset_index(), hold


# ----------------------------------------------------------------------------- build
def build(q, urls, keep_cache=False):
    os.makedirs(os.path.join(DATA, "features"), exist_ok=True)
    os.makedirs(os.path.join(DATA, "holdings"), exist_ok=True)
    os.makedirs(os.path.join(DATA, "prices"), exist_ok=True)
    need_c = zips_for(urls, q)
    need_p = zips_for(urls, prev_q(q)) if q > FIRST_Q else []
    paths_c = [fetch(u) for u in need_c]
    info, filers, checks = load_quarter(paths_c, q)
    cur, issuer, opts = books(info)
    px = prices(cur, issuer)
    prv = None
    if need_p:
        pinfo, _, _ = load_quarter([fetch(u) for u in need_p], prev_q(q))
        prv, _, _ = books(pinfo)
        del pinfo
    f, m = features(q, cur, prv, opts, px, filers)

    f.to_parquet(os.path.join(DATA, "features", f"{q}.parquet"), index=False, compression="zstd")
    px.reset_index().rename(columns={"CUSIP": "cusip"}).assign(quarter=q).to_parquet(
        os.path.join(DATA, "prices", f"{q}.parquet"), index=False)
    cands = set(f.loc[f.is_candidate, "cik"])
    if m is not None:
        h = m[m.CIK.isin(cands)][["CIK", "CUSIP", "SH", "VAL", "SH_P", "VAL_P", "DSH", "FLOW", "STATUS"]]
        fresh = cur[cur.CIK.isin(cands - set(m.CIK))].assign(SH_P=np.nan, VAL_P=np.nan, DSH=np.nan, FLOW=np.nan,
                                                              STATUS="first_filing")
        h = pd.concat([h, fresh[h.columns]], ignore_index=True)
    else:
        h = cur[cur.CIK.isin(cands)].assign(SH_P=np.nan, VAL_P=np.nan, DSH=np.nan, FLOW=np.nan, STATUS="n/a")
    h.columns = [c.lower() for c in h.columns]
    for c in ("sh", "val", "sh_p", "val_p", "dsh", "flow"):
        h[c] = h[c].astype("float64")
    h.assign(quarter=q).to_parquet(os.path.join(DATA, "holdings", f"{q}.parquet"), index=False, compression="zstd")

    man_p = os.path.join(DATA, "manifest.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {"quarters": {}}
    man["quarters"][q] = {
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sec_files": [{"name": os.path.basename(p), "bytes": os.path.getsize(p), "sha256": sha256(p)} for p in paths_c],
        "filers": int(len(f)), "candidates": int(len(cands)), "holding_rows": int(len(h)),
        "total_long_value": float(f.aum.sum()), "has_prior": prv is not None, "checks": checks,
    }
    man["latest"] = max(man["quarters"])
    json.dump(man, open(man_p, "w"), indent=1, sort_keys=True)

    if not keep_cache:  # free disk: drop files no later quarter needs
        keep_from = qend(q)  # the next build needs files for next_q and for q (as its prior quarter)
        for u in urls:
            w = window(u)
            pth = os.path.join(CACHE, u.rsplit("/", 1)[-1])
            if w and w[1] < keep_from and os.path.exists(pth):
                os.remove(pth)
    print(json.dumps({"quarter": q, **{k: man["quarters"][q][k] for k in ("filers", "candidates", "holding_rows")}}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--quarter", default="")
    a = ap.parse_args()
    urls = list_zips()
    if a.plan:
        end = a.end or latest_complete(urls)
        man_p = os.path.join(DATA, "manifest.json")
        done = set(json.load(open(man_p))["quarters"]) if os.path.exists(man_p) else set()
        start = a.start or (next_q(max(done)) if done else end)
        print(" ".join(q for q in q_range(max(start, FIRST_Q), end) if q not in done or a.start))
        return
    build(a.quarter, urls)


if __name__ == "__main__":
    main()
