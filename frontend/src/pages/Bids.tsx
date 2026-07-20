import { useEffect, useState } from "react";
import { api, Followup, Rfp } from "../api";
import { Chip, DaysLeft } from "../components";

export default function Bids() {
  const [bids, setBids] = useState<Rfp[]>([]);
  const [followups, setFollowups] = useState<Followup[]>([]);
  const [actor, setActor] = useState(localStorage.getItem("bidpilot_actor") ?? "");
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api.bids().then((d) => { setBids(d.bids); setFollowups(d.followups); }).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, []);
  useEffect(() => { localStorage.setItem("bidpilot_actor", actor); }, [actor]);

  const send = async (id: string) => {
    if (!actor.trim()) { setMsg("Enter your name — sending requires an attributed human approval."); return; }
    try {
      await api.sendFollowup(id, actor.trim());
      setMsg("Follow-up sent and recorded as a decision.");
      load();
    } catch (e) { setMsg(String(e)); }
  };

  return (
    <>
      <h1 className="page-title">Bids</h1>
      <p className="page-sub">Submitted bids, tracker status, and follow-up drafts awaiting your send approval.</p>

      <div className="card">
        <h3>Approved & submitted ({bids.length})</h3>
        <table>
          <thead><tr><th>Tender</th><th>Ref</th><th>Due</th><th>Status</th></tr></thead>
          <tbody>
            {bids.map((b) => (
              <tr key={b.rfp_id}>
                <td>{b.title}</td>
                <td className="mono">{b.reference_no || "—"}</td>
                <td>{b.due_date ?? ""} <DaysLeft days={b.days_left} unknown={b.due_unknown} /></td>
                <td><Chip status={b.status} /></td>
              </tr>
            ))}
            {bids.length === 0 && <tr><td colSpan={4} className="muted">No approved or submitted bids yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Follow-up drafts <span className="small muted">(the tracker never auto-sends)</span></h3>
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <input type="text" placeholder="your name" value={actor} onChange={(e) => setActor(e.target.value)} style={{ width: 240 }} />
        </div>
        {followups.map((f) => (
          <details key={f.id} style={{ marginBottom: 10 }}>
            <summary>
              <Chip status={f.status} /> {f.subject} <span className="muted small">— {f.reason}</span>
            </summary>
            <pre className="runlog" style={{ margin: "8px 0" }}>{f.body}</pre>
            {f.status === "draft" && (
              <button className="primary" onClick={() => send(f.id)}>Approve & send</button>
            )}
          </details>
        ))}
        {followups.length === 0 && <p className="muted small">No drafts. The tracker creates them when deadlines approach or issuers reply.</p>}
        {msg && <div className="small" style={{ marginTop: 8, color: "var(--warning)" }}>{msg}</div>}
      </div>
    </>
  );
}
