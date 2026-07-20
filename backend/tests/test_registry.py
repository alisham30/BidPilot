from datetime import date, timedelta

from app.dataset.registry import dedupe_key, rfps_in_window, upsert_rfp


def test_dedupe_on_rescan(session):
    _, created1 = upsert_rfp(session, title="11kV cable tender", issuer="PSU",
                             reference_no="NIT/2026/42", due_date=date(2026, 9, 1),
                             source="email", source_detail="a@b.com")
    _, created2 = upsert_rfp(session, title="different subject line", issuer="PSU",
                             reference_no="NIT / 2026 / 42", due_date=date(2026, 9, 1),
                             source="email", source_detail="a@b.com")
    assert created1 and not created2  # normalized ref no wins


def test_dedupe_without_ref_uses_title_due_issuer(session):
    kw = dict(source="web", source_detail="portal", reference_no="")
    _, c1 = upsert_rfp(session, title="LT cables", issuer="X", due_date=date(2026, 8, 1), **kw)
    _, c2 = upsert_rfp(session, title="LT cables", issuer="X", due_date=date(2026, 8, 1), **kw)
    _, c3 = upsert_rfp(session, title="LT cables", issuer="Y", due_date=date(2026, 8, 1), **kw)
    assert c1 and not c2 and c3


def test_window_includes_day_92_excludes_day_93(session):
    today = date.today()
    kw = dict(issuer="", reference_no="", source="web", source_detail="p")
    upsert_rfp(session, title="day92", due_date=today + timedelta(days=92), **kw)
    upsert_rfp(session, title="day93", due_date=today + timedelta(days=93), **kw)
    upsert_rfp(session, title="unknown-due", due_date=None, **kw)
    titles = [r.title for r in rfps_in_window(session)]
    assert "day92" in titles
    assert "day93" not in titles
    assert "unknown-due" in titles  # kept but flagged (due_unknown in the API)


def test_window_excludes_past_due(session):
    today = date.today()
    upsert_rfp(session, title="expired", issuer="", reference_no="", source="web",
               source_detail="p", due_date=today - timedelta(days=1))
    assert "expired" not in [r.title for r in rfps_in_window(session)]


def test_dedupe_key_stability():
    assert dedupe_key("REF 1", "", None, "") == dedupe_key("ref1", "x", date(2026, 1, 1), "y")
