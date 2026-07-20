import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, inr, Rfp, Stats } from "../api";
import { Chip, DaysLeft, Tile } from "../components";

const VERDICT_LABEL: Record<string, string> = {
  proceed: "Ready to bid",
  proceed_with_deviations: "Bid with deviations",
  recommend_no_bid: "Advised: no bid",
};

export default function Pipeline() {
  const nav = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [rfps, setRfps] = useState<Rfp[]>([]);
  const [windowed, setWindowed] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = () => {
    api.stats().then(setStats).catch(() => {});
    api.rfps(windowed ? "3m" : undefined).then(setRfps).catch(() => {});
  };
  useEffect(load, [windowed]);
  useEffect(() => {
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [windowed]);

  const trigger = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await fn();
      setToast(`${name} finished.`);
      load();
    } catch (e) {
      setToast(`${name} failed: ${e}`);
    } finally {
      setBusy(null);
      setTimeout(() => setToast(null), 5000);
    }
  };

  const queue = stats?.review_queue ?? [];
  const name = (localStorage.getItem("bidpilot_actor") || "").split(" ")[0];
  const hour = new Date().getHours();
  const daypart = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
  const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
  const dist = {
    live: rfps.filter((r) => ["new", "extracted", "drafting"].includes(r.status)).length,
    review: rfps.filter((r) => r.status === "awaiting_review").length,
    won: rfps.filter((r) => ["approved", "submitted"].includes(r.status)).length,
    closed: rfps.filter((r) => ["closed", "no_bid"].includes(r.status)).length,
  };
  const distTotal = Math.max(1, dist.live + dist.review + dist.won + dist.closed);

  return (
    <>
      <div className="greet">
        <h1 className="hello">
          Good {daypart}{name ? <>, <em>{name.charAt(0).toUpperCase() + name.slice(1)}</em></> : ""}
        </h1>
        <p className="today">
          {today} · {queue.filter((q) => !q.running).length > 0
            ? `${queue.filter((q) => !q.running).length} bid draft(s) waiting for your decision`
            : "no decisions pending — the desk is clear"}
        </p>
      </div>

      <div className="statusbar-wrap">
        <div className="statusbar" title="Tender pipeline in view">
          <div className="seg s-live" style={{ width: `${(dist.live / distTotal) * 100}%` }} />
          <div className="seg s-review" style={{ width: `${(dist.review / distTotal) * 100}%` }} />
          <div className="seg s-won" style={{ width: `${(dist.won / distTotal) * 100}%` }} />
          <div className="seg s-closed" style={{ width: `${(dist.closed / distTotal) * 100}%` }} />
        </div>
        <div className="statusbar-legend">
          <span><i className="seg s-live" /> incoming {dist.live}</span>
          <span><i className="seg s-review" /> in review {dist.review}</span>
          <span><i className="seg s-won" /> approved/submitted {dist.won}</span>
          <span><i className="seg s-closed" /> closed {dist.closed}</span>
        </div>
      </div>

      {!stats && (
        <div className="tiles">
          {[...Array(5)].map((_, i) => <div key={i} className="skeleton" />)}
        </div>
      )}
      {stats && (
        <div className="tiles">
          <Tile label="Awaiting your decision" value={queue.filter((q) => !q.running).length}
                alert={queue.some((q) => !q.running)} />
          <Tile label="Live tenders in window" value={stats.rfps_in_window} />
          <Tile label="Submitted bids" value={stats.bids_submitted} />
          <Tile label="Open issues" value={stats.open_escalations} alert={stats.open_escalations > 0} />
          <Tile label="Catalog products" value={stats.skus} />
        </div>
      )}

      {/* ---------------- the work queue ---------------- */}
      <div className="card queue-card">
        <h3>Needs your decision {queue.length > 0 && <span className="counter">{queue.length}</span>}</h3>
        {queue.length === 0 && (
          <div className="empty">
            <div className="glyph">🗂️</div>
            All clear — new tenders are picked up from your inbox every 5 minutes,
            analyzed automatically, and land here for your decision.
          </div>
        )}
        <div className="queue">
          {queue.map((q) => (
            <button key={q.run_id} className="queue-row" onClick={() => nav(`/runs/${q.run_id}`)}>
              <div className="q-main">
                <div className="q-title">{q.title || "(untitled tender)"}</div>
                <div className="q-meta">{q.issuer || "unknown issuer"}
                  {q.due_date && <> · due {q.due_date}</>}{" "}
                  <DaysLeft days={q.days_left} unknown={q.due_date === null} />
                </div>
              </div>
              <div className="q-right">
                {q.running ? (
                  <span className="chip drafting">analyzing…</span>
                ) : (
                  <>
                    <div className="q-amount">{inr(q.grand_total)}</div>
                    <div className="q-flags">
                      {q.verdict && <Chip status={q.verdict} />}
                      <span className="small muted">
                        {q.items_total - q.items_flagged}/{q.items_total} items matched
                      </span>
                    </div>
                  </>
                )}
              </div>
              <div className="q-go">Review →</div>
            </button>
          ))}
        </div>
      </div>

      {/* ---------------- registry ---------------- */}
      <div className="card">
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>All tenders ({rfps.length})</h3>
          <div className="spacer" />
          <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={windowed} onChange={(e) => setWindowed(e.target.checked)} />
            due within 92 days
          </label>
          <button disabled={busy !== null} onClick={() => trigger("Inbox scan", api.scanEmail)}>
            {busy === "Inbox scan" ? "Scanning…" : "📬 Scan now"}
          </button>
          <button disabled={busy !== null} onClick={() => trigger("Portal scan", api.scanWeb)}>🌐 Portals</button>
          <button disabled={busy !== null} onClick={() => trigger("Catalog rebuild", api.rebuildCatalog)}>⚙ Catalog</button>
        </div>
        <table>
          <thead>
            <tr><th>Tender</th><th>Issuer</th><th>Ref</th><th>Due</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rfps.map((r) => (
              <tr key={r.rfp_id} className="clickable" onClick={() => nav(`/rfps/${r.rfp_id}`)}>
                <td>{r.title || <span className="muted">(untitled)</span>}</td>
                <td>{r.issuer || "—"}</td>
                <td className="mono small">{r.reference_no || "—"}</td>
                <td>{r.due_date ?? ""} <DaysLeft days={r.days_left} unknown={r.due_unknown} /></td>
                <td><Chip status={r.status} /></td>
              </tr>
            ))}
            {rfps.length === 0 && (
              <tr><td colSpan={5} className="muted">Nothing yet — the inbox is polled every 5 minutes, or press Scan now.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {toast && <div className="toast small">{toast}</div>}
    </>
  );
}
