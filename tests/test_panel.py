import io, json, os, sys, zipfile
import pandas as pd
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import panel


def tsv(rows, cols):
    return pd.DataFrame(rows, columns=cols).to_csv(sep="\t", index=False)


def make_zip(path, subs, infos, summ):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv([s[:5] for s in subs],
                   ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"]))
        z.writestr("COVERPAGE.tsv", tsv([[s[0], s[5], "N", ""] for s in subs],
                   ["ACCESSION_NUMBER", "FILINGMANAGER_NAME", "ISAMENDMENT", "AMENDMENTTYPE"]))
        z.writestr("SUMMARYPAGE.tsv", tsv(summ, ["ACCESSION_NUMBER", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"]))
        z.writestr("INFOTABLE.tsv", tsv(infos, ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "VALUE",
                                               "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"]))


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(panel, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setitem(panel.CAND, "min_aum", 0)
    os.makedirs(tmp_path / "cache")
    # Q1 2023 filed in window 01mar2023-31may2023 (dollars); Q4 2022 filed Feb 2023 window (dollars too)
    # Manager A (concentrated): Q4 holds AAA 100sh, BBB 50sh; Q1: AAA 120sh (added), CCC 10sh (new), BBB exited
    # Manager B (in $000s by mistake in Q1), plus 4 other holders to give stable median prices; DDD 2:1 split
    others = [f"000000000{i}" for i in range(3, 9)]
    def book(acc, cik, rows):
        return [[acc, n, "COM", c, v, sh, "SH", ""] for n, c, v, sh in rows]
    q4, q1, s4, s1, sm = [], [], [], [], []
    for i, cik in enumerate(["0000000001", "0000000002"] + others):
        a4, a1 = f"A4-{i}", f"A1-{i}"
        s4.append([a4, "10-FEB-2023", "13F-HR", cik, "31-DEC-2022", f"MGR {i}"])
        s1.append([a1, "10-MAY-2023", "13F-HR", cik, "31-MAR-2023", f"MGR {i}"])
        if i == 0:
            r4 = [("AAA INC", "AAA000001", 1000, 100), ("BBB CORP", "BBB000001", 1000, 50), ("DDD CO", "DDD000001", 400, 10)]
            r1 = [("AAA INC", "AAA000001", 1320, 120), ("CCC LTD", "CCC000001", 500, 10), ("DDD CO", "DDD000001", 440, 20)]
        else:
            r4 = [("AAA INC", "AAA000001", 1000, 100), ("BBB CORP", "BBB000001", 2000, 100), ("DDD CO", "DDD000001", 400, 10)]
            r1 = [("AAA INC", "AAA000001", 1100, 100), ("BBB CORP", "BBB000001", 2000, 100), ("DDD CO", "DDD000001", 440, 20),
                  ("CCC LTD", "CCC000001", 500, 10)]
        if i == 1:  # reports $000s in Q1
            r1 = [(n, c, v / 1000, sh) for n, c, v, sh in r1]
        if i == 2:  # reports 1000x too large in Q1 (needs dividing, giving non-integer values)
            r1 = [(n, c, int(v * 1000 + 7), sh) for n, c, v, sh in r1]
        q4 += book(a4, cik, r4); q1 += book(a1, cik, r1)
        sm.append([a4, sum(r[2] for r in r4), len(r4)]); sm.append([a1, sum(r[2] for r in r1), len(r1)])
    make_zip(tmp_path / "cache" / "01dec2022-28feb2023_form13f.zip", s4, q4, [x for x in sm if x[0].startswith("A4")])
    make_zip(tmp_path / "cache" / "01mar2023-31may2023_form13f.zip", s1, q1, [x for x in sm if x[0].startswith("A1")])
    urls = ["https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01dec2022-28feb2023_form13f.zip",
            "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01mar2023-31may2023_form13f.zip"]
    return tmp_path, urls


def test_windows_and_plan():
    assert panel.window("x/2013q2_form13f.zip") == (pd.Timestamp("2013-04-01"), pd.Timestamp("2013-06-30"))
    assert panel.window("x/01jun2024-31aug2024_form13f.zip") == (pd.Timestamp("2024-06-01"), pd.Timestamp("2024-08-31"))
    urls = ["a/01jun2024-31aug2024_form13f.zip", "a/01sep2024-30nov2024_form13f.zip", "a/01dec2024-28feb2025_form13f.zip"]
    assert panel.zips_for(urls, "2024Q3") == urls[1:]
    assert panel.latest_complete(urls) == "2024Q4"
    assert panel.q_range("2024Q3", "2025Q1") == ["2024Q3", "2024Q4", "2025Q1"]
    assert panel.q_range("2025Q2", "2025Q1") == []


def test_build(world):
    tmp, urls = world
    panel.build("2023Q1", urls, keep_cache=True)
    f = pd.read_parquet(tmp / "data/features/2023Q1.parquet").set_index("cik")
    h = pd.read_parquet(tmp / "data/holdings/2023Q1.parquet")
    man = json.load(open(tmp / "data/manifest.json"))["quarters"]["2023Q1"]
    a = f.loc["0000000001"]
    assert a.n_positions == 3 and a.aum == pytest.approx(2260)
    ha = h[h.cik == "0000000001"].set_index("cusip")
    assert ha.loc["AAA000001", "status"] == "added" and ha.loc["CCC000001", "status"] == "new"
    assert ha.loc["BBB000001", "status"] == "exit"
    assert ha.loc["DDD000001", "status"] == "unchanged"            # 2:1 split detected, not a buy
    assert ha.loc["AAA000001", "flow"] == pytest.approx(20 * 11)     # 20 shares x median price 11
    assert f.loc["0000000002"].aum == pytest.approx(f.loc["0000000004"].aum)  # $000s filer rescaled
    assert man["checks"]["unit_fixes"] == 2
    assert f.loc["0000000003"].aum == pytest.approx(f.loc["0000000004"].aum, rel=1e-3)
    assert man["checks"]["coverpage_value_match_share"] == 1.0
    assert 0 < a.eff_n <= 3 and a.top10_wt == pytest.approx(1.0)
    assert f.is_candidate.all()


def test_fix_units_integer_input():
    # Real SEC files parse VALUE as int64; dividing one filer by 1000 must not fail (pandas 3 raised TypeError)
    rows = []
    for cik in ["A", "B", "C", "D"]:
        for cus, px in (("X00000001", 10), ("Y00000001", 50)):
            v = px * 100 * (1000 if cik == "A" else 1) + (7 if cik == "A" else 0)
            rows.append({"CIK": cik, "CUSIP": cus, "SSHPRNAMT": "100", "VALUE": str(v), "SSHPRNAMTTYPE": "SH", "PUTCALL": ""})
    info = pd.DataFrame(rows)
    info["VALUE"] = pd.to_numeric(info["VALUE"], errors="coerce").fillna(0.0).astype("float64")
    info["SSHPRNAMT"] = pd.to_numeric(info["SSHPRNAMT"], errors="coerce").fillna(0.0).astype("float64")
    info, n = panel.fix_units(info)
    assert n == 1 and info[info.CIK == "A"].VALUE.sum() == pytest.approx(6000.014)
