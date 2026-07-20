import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Rfp } from "../api";
import { Chip, DaysLeft } from "../components";

type Detail = Rfp & {
  dataset: { line_items: Record<string, unknown>[]; tests: Record<string, unknown>[] } | null;
  runs: { run_id: string; status: string; started_at: string }[];
  doc_paths: string[];
};

// Clicking a tender should just SHOW the analysis: forward to the newest run,
// or start one automatically. The only clicks a user ever makes are decisions.
export default function RfpDetail() {
  const { rfpId } = useParams();
  const nav = useNavigate();
  const [rfp, setRfp] = useState<Detail | null>(null);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const autoStarted = useRef(false);

  useEffect(() => {
    if (rfpId) api.rfp(rfpId).then((d) => setRfp(d as Detail)).catch((e) => setErr(String(e)));
  }, [rfpId]);

  useEffect(() => {
    if (!rfp || autoStarted.current) return;
    if (rfp.runs.length > 0) {
      nav(`/runs/${rfp.runs[0].run_id}`, { replace: true });
      return;
    }
    if (rfp.status !== "closed" && (rfp.dataset || rfp.doc_paths.length > 0)) {
      autoStarted.current = true;
      respond();
    }
  }, [rfp]);

  const respond = async () => {
    if (!rfpId) return;
    setStarting(true);
    try {
      const { run_id } = await api.respond(rfpId);
      nav(`/runs/${run_id}`, { replace: true });
    } catch (e) {
      setErr(String(e));
      setStarting(false);
    }
  };

  if (err) return <p className="reason">{err}</p>;
  if (!rfp) return <p className="muted">Loading…</p>;
  if (starting) {
    return (
      <>
        <h1 className="page-title">{rfp.title || "(untitled tender)"}</h1>
        <p className="page-sub">Analyzing the tender — extracting specifications, matching your catalog and pricing. You'll land on the results automatically…</p>
      </>
    );
  }

  return (
    <>
      <h1 className="page-title">{rfp.title || "(untitled tender)"}</h1>
      <p className="page-sub">
        {rfp.issuer || "unknown issuer"} · <span className="mono">{rfp.reference_no || "no ref"}</span> ·{" "}
        {rfp.due_date ?? ""} <DaysLeft days={rfp.days_left} unknown={rfp.due_unknown} /> · <Chip status={rfp.status} />
      </p>

      <div className="card">
        <div className="btn-row">
          <button className="primary" onClick={respond}>
            {rfp.status === "closed" ? "▶ Analyze anyway" : "▶ Analyze tender"}
          </button>
          {rfp.status === "closed" && (
            <span className="reason">⚠ deadline has passed — analysis only; bidding is blocked</span>
          )}
          {rfp.doc_paths.length === 0 && (
            <span className="muted small">no documents attached — analysis needs the tender files</span>
          )}
        </div>
      </div>

      {rfp.dataset && (
        <div className="card">
          <h3>Extracted requirements — {rfp.dataset.line_items.length} item(s), {rfp.dataset.tests.length} test(s)</h3>
          <table>
            <thead><tr><th>Item</th><th>Description</th><th>Qty</th><th>Specifications</th></tr></thead>
            <tbody>
              {rfp.dataset.line_items.map((li, i: number) => (
                <tr key={i}>
                  <td>{String(li.item_no)}</td>
                  <td>{String(li.description)}</td>
                  <td className="num">{String(li.quantity)} {String(li.unit)}</td>
                  <td className="small muted">
                    {(li.specs as { name: string; value: string }[])
                      .map((s) => `${s.name.replaceAll("_", " ")}: ${s.value}`).join(" · ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
