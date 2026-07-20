import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Escalation } from "../api";
import { Severity } from "../components";

export default function Escalations() {
  const [rows, setRows] = useState<Escalation[]>([]);
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [actor, setActor] = useState(localStorage.getItem("bidpilot_actor") ?? "");

  const load = () => api.escalations(filter).then(setRows).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [filter]);
  useEffect(() => { localStorage.setItem("bidpilot_actor", actor); }, [actor]);

  const ack = async (id: string) => {
    await api.ackEscalation(id, actor || "unknown");
    load();
  };

  return (
    <>
      <h1 className="page-title">Escalations</h1>
      <p className="page-sub">Every agent failure, gap, and low-confidence result lands here. Nothing fails silently.</p>

      <div className="card">
        <div className="btn-row" style={{ marginBottom: 12 }}>
          <button className={filter === "open" ? "primary" : ""} onClick={() => setFilter("open")}>Open</button>
          <button className={filter === "all" ? "primary" : ""} onClick={() => setFilter("all")}>All</button>
          <div className="spacer" />
          <input type="text" placeholder="your name" value={actor} onChange={(e) => setActor(e.target.value)} style={{ width: 200 }} />
        </div>
        <table>
          <thead><tr><th>Severity</th><th>Agent</th><th>Reason</th><th>RFP</th><th>When</th><th></th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td><Severity level={e.severity} /></td>
                <td className="mono small">{e.source_agent}</td>
                <td>{e.reason}</td>
                <td>{e.rfp_id ? <Link to={`/rfps/${e.rfp_id}`} className="mono small">{e.rfp_id.slice(-6)}</Link> : "—"}</td>
                <td className="small muted">{e.created_at.slice(0, 19).replace("T", " ")}</td>
                <td>
                  {e.status !== "resolved" && (
                    <button onClick={() => ack(e.id)}>{e.status === "open" ? "Ack" : "Resolve"}</button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="muted">No {filter === "open" ? "open " : ""}escalations. 🎉</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
