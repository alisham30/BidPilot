import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, inr, RunDetail, RunState, runSocket, TechItem, VerdictItem } from "../api";
import { Chip, MatchBar } from "../components";

export default function RunView() {
  const { runId } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [tenderTitle, setTenderTitle] = useState<string>("");
  const [liveNode, setLiveNode] = useState<string | null>(null);
  const [actor, setActor] = useState(localStorage.getItem("bidpilot_actor") ?? "");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [noBidReason, setNoBidReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const reload = () => { if (runId) api.run(runId).then(setRun).catch(() => {}); };

  useEffect(() => {
    reload();
    if (!runId) return;
    wsRef.current = runSocket(runId, (data) => {
      setLiveNode(data.node ?? data.type);
      if (data.state) {
        setRun((prev) => prev
          ? { ...prev, state: data.state as RunState, status: data.type === "awaiting_review" ? "awaiting_review" : prev.status }
          : prev);
      }
      if (data.type === "awaiting_review" || data.type === "failed") reload();
    });
    const poll = setInterval(reload, 12000);
    return () => { wsRef.current?.close(); clearInterval(poll); };
  }, [runId]);

  useEffect(() => { localStorage.setItem("bidpilot_actor", actor); }, [actor]);
  useEffect(() => {
    if (run?.rfp_id && !tenderTitle) {
      api.rfp(run.rfp_id).then((r) => setTenderTitle(r.title)).catch(() => {});
    }
  }, [run?.rfp_id]);

  if (!run) return <p className="muted">Loading run…</p>;
  const s = run.state ?? {};
  const verdictByItem: Record<string, VerdictItem> = {};
  s.verdict?.per_item.forEach((v) => { verdictByItem[v.item_no] = v; });
  const approved = run.decisions.some((d) => d.action === "approve");
  const decided = run.decisions.some((d) => ["approve", "no_bid"].includes(d.action));
  const canDecide = run.status === "awaiting_review" && !decided;

  const act = async (action: string, payload: Record<string, unknown> = {}) => {
    if (!actor.trim()) { setMsg("Enter your name first — every decision is attributed."); return; }
    try {
      await api.decision(run.run_id, actor.trim(), action, payload);
      setMsg(action === "edit" ? "Edit applied — re-verifying…" : `Decision '${action}' recorded.`);
      setTimeout(reload, 800);
    } catch (e) { setMsg(String(e)); }
  };

  return (
    <>
      <h1 className="page-title">{tenderTitle || run.run_id}</h1>
      <p className="page-sub">
        <span className="mono">{run.run_id}</span> · <Chip status={run.status} />{" "}
        {run.status === "running" && <span className="muted">live · current step: <b>{liveNode ?? "starting"}</b></span>}
        {run.status === "awaiting_review" && <span className="muted"> · review the matches below, then approve, edit or no-bid</span>}
      </p>

      {/* ---------------- executive summary ---------------- */}
      {s.tech && s.price && (
        <div className="tiles">
          <div className="tile">
            <div className="label">Bid value</div>
            <div className="value">{inr(s.price.grand_total)}</div>
          </div>
          <div className="tile">
            <div className="label">Items matched</div>
            <div className="value">
              {s.tech.items.filter((i) => !i.below_threshold).length}
              <span className="muted" style={{ fontSize: 16 }}> / {s.tech.items.length}</span>
            </div>
          </div>
          <div className={`tile ${s.tech.items.some((i) => i.below_threshold) ? "alert" : ""}`}>
            <div className="label">Spec gaps</div>
            <div className="value">{s.tech.items.filter((i) => i.below_threshold).length}</div>
          </div>
          <div className={`tile ${(s.price.lines.some((l) => !l.priced) || s.price.test_lines.some((t) => !t.priced)) ? "alert" : ""}`}>
            <div className="label">Unpriced lines</div>
            <div className="value">{s.price.lines.filter((l) => !l.priced).length + s.price.test_lines.filter((t) => !t.priced).length}</div>
          </div>
        </div>
      )}

      {/* ---------------- approval checkpoint ---------------- */}
      <div className="card" style={{ borderColor: canDecide ? "var(--warning)" : undefined }}>
        <h3>Human checkpoint {canDecide ? "— your decision is required" : ""}</h3>
        <div className="btn-row" style={{ marginBottom: 10 }}>
          <input type="text" placeholder="your name (recorded on every action)"
                 value={actor} onChange={(e) => setActor(e.target.value)} style={{ width: 260 }} />
          <button className="good" disabled={!canDecide} onClick={() => act("approve")}>✓ Approve — go</button>
          <button disabled={!canDecide || Object.keys(overrides).length === 0}
                  onClick={() => act("edit", { sku_overrides: overrides })}>
            ✎ Apply {Object.keys(overrides).length} edit(s) & re-verify
          </button>
          <button className="danger" disabled={!canDecide}
                  onClick={() => act("no_bid", { reason: noBidReason })}>✗ No-bid</button>
          <input type="text" placeholder="no-bid reason" value={noBidReason}
                 onChange={(e) => setNoBidReason(e.target.value)} style={{ width: 200 }} />
        </div>
        <div className="btn-row">
          {approved && (
            <>
              <a className="btn" href={api.pdfUrl(run.run_id)} target="_blank" rel="noreferrer">⬇ Bid PDF</a>
              {!run.decisions.some((d) => d.action === "mark_submitted") && (
                <button onClick={() => act("mark_submitted")}>Mark as submitted</button>
              )}
            </>
          )}
          {!approved && <span className="small muted">PDF is locked (403) until an approve decision exists.</span>}
        </div>
        {run.decisions.length > 0 && (
          <div className="small muted" style={{ marginTop: 10 }}>
            {run.decisions.map((d, i) => (
              <div key={i}>· {d.decided_at.slice(0, 19).replace("T", " ")} — <b>{d.actor}</b> → {d.action}</div>
            ))}
          </div>
        )}
        {msg && <div className="small" style={{ marginTop: 8, color: "var(--warning)" }}>{msg}</div>}
      </div>

      {/* ---------------- verdict ---------------- */}
      {s.verdict && (
        <div className={`card ${s.verdict.overall === "proceed" ? "verdict-proceed"
          : s.verdict.overall === "proceed_with_deviations" ? "verdict-deviations" : "verdict-nobid"}`}>
          <h3>Verifier verdict — <Chip status={s.verdict.overall} /> <span className="small muted">(recommendation only; no authority to act)</span></h3>
          <p style={{ margin: "6px 0 10px" }}>{verdictInPlainWords(s)}</p>
          {s.verdict.evidence.length > 0 && (
            <details>
              <summary className="small">evidence trail ({s.verdict.evidence.length})</summary>
              {s.verdict.evidence.map((e, i) => <div className="evidence-note" key={i}>· {e}</div>)}
            </details>
          )}
        </div>
      )}

      {/* ---------------- comparison tables ---------------- */}
      {s.tech?.items.map((item) => (
        <ItemCard key={item.item_no} item={item} verdict={verdictByItem[item.item_no]}
                  canEdit={canDecide} override={overrides[item.item_no]}
                  onOverride={(sku) => setOverrides((o) => {
                    const next = { ...o };
                    if (sku === (item.top_pick ?? "")) delete next[item.item_no];
                    else next[item.item_no] = sku;
                    return next;
                  })} />
      ))}

      {/* ---------------- price table ---------------- */}
      {s.price && (
        <div className="card">
          <h3>Price schedule <span className="small muted">(all arithmetic computed in code from price tables)</span></h3>
          <table>
            <thead><tr><th>Item</th><th>SKU</th><th>Qty</th><th>Rate</th><th>Amount</th></tr></thead>
            <tbody>
              {s.price.lines.map((l) => (
                <tr key={l.item_no}>
                  <td>{l.item_no}</td>
                  <td className="mono">{l.sku_id || "—"}</td>
                  <td className="num">{l.quantity} {l.unit}</td>
                  <td className="num">{l.priced ? `${l.unit_price.toLocaleString("en-IN")} ${l.currency}` : <span className="reason">NOT PRICED</span>}</td>
                  <td className="num">{l.priced ? l.amount.toLocaleString("en-IN") : "—"}</td>
                </tr>
              ))}
              {s.price.test_lines.map((t) => (
                <tr key={t.test_name}>
                  <td className="muted">test</td>
                  <td colSpan={2}>{t.test_name} <span className="muted small">{t.standard}</span></td>
                  <td className="num" colSpan={2}>{t.priced ? `${t.price.toLocaleString("en-IN")} ${t.currency}` : <span className="reason">NOT PRICED</span>}</td>
                </tr>
              ))}
              <tr>
                <td colSpan={4}><b>Grand total</b></td>
                <td className="num"><b>{s.price.grand_total.toLocaleString("en-IN")} {s.price.currency}</b></td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* ---------------- MTO ---------------- */}
      {s.mto && s.mto.length > 0 && (
        <div className="card">
          <h3>Made-to-order requests <span className="small muted">(drafts — a human raises these internally)</span></h3>
          {s.mto.map((m) => (
            <details key={m.item_no} style={{ marginBottom: 8 }}>
              <summary>Item {m.item_no} — {m.draft_subject} <span className="muted small">(base: {m.closest_sku || "none"})</span></summary>
              <pre className="runlog" style={{ marginTop: 8 }}>{m.draft_body}</pre>
            </details>
          ))}
        </div>
      )}

      {/* ---------------- run log ---------------- */}
      <div className="card">
        <h3>Run log</h3>
        <div className="runlog">{(s.run_log ?? []).join("\n")}</div>
      </div>
    </>
  );
}

function verdictInPlainWords(s: RunState): string {
  const items = s.tech?.items ?? [];
  const matched = items.filter((i) => !i.below_threshold).length;
  const gaps = items.length - matched;
  const unpriced = (s.price?.lines ?? []).filter((l) => !l.priced).length;
  const overall = s.verdict?.overall;
  if (overall === "proceed") {
    return `In plain words: all ${items.length} item(s) can be supplied from the catalog and every price was verified. You can approve and prepare the bid.`;
  }
  if (overall === "proceed_with_deviations") {
    return `In plain words: ${matched} of ${items.length} item(s) match well; ${gaps > 0 ? `${gaps} item(s) have specification gaps (listed below with exact parameters)` : "some checks raised concerns"}${unpriced > 0 ? ` and ${unpriced} line(s) could not be priced from the price tables` : ""}. You can still bid, but the deviation statement will disclose these gaps — review each flagged item before approving.`;
  }
  return `In plain words: the system advises AGAINST bidding. ${gaps} of ${items.length} item(s) ask for products the catalog cannot supply (or the tender contains requirements that cannot be met)${unpriced > 0 ? `, and ${unpriced} line(s) have no price` : ""}. If you believe the catalog should cover these, check the flagged parameters below — or treat the made-to-order drafts as the way forward.`;
}

function itemInPlainWords(item: TechItem): string | null {
  const best = item.top3[0];
  if (!best) return "No product in the catalog resembles this item at all — it is outside the product range.";
  const gaps = best.evidence.filter((e) => e.score < 1);
  if (gaps.length === 0) return null;
  const parts = gaps.slice(0, 4).map((g) =>
    `tender needs ${g.param.replaceAll("_", " ")} = ${g.required}, closest product ${g.actual === null ? "does not state it" : `offers ${g.actual}`}`);
  return `Why it ${item.below_threshold ? "failed" : "isn't perfect"}: ${parts.join("; ")}${gaps.length > 4 ? `; +${gaps.length - 4} more` : ""}.`;
}

function ItemCard({ item, verdict, canEdit, override, onOverride }: {
  item: TechItem; verdict?: VerdictItem; canEdit: boolean;
  override?: string; onOverride: (sku: string) => void;
}) {
  const best = item.top3[0];
  const disagreement = verdict?.status === "flagged";
  const needsAttention = disagreement || item.below_threshold;
  return (
    <details className="card item-details" open={needsAttention}
             style={disagreement ? { borderColor: "var(--serious)" } : undefined}>
      <summary className="item-summary">
        <span className="item-name">Item {item.item_no}: {item.description}</span>
        <span className="small muted">{item.quantity} {item.unit}</span>
        {best && <MatchBar pct={best.pct} />}
        {verdict && <Chip status={verdict.status} />}
        {!needsAttention && <span className="small muted">click for details</span>}
      </summary>

      <div className="btn-row" style={{ margin: "12px 0 10px" }}>
        <span className="small muted">Top pick:</span>
        {canEdit ? (
          <select value={override ?? item.top_pick ?? ""} onChange={(e) => onOverride(e.target.value)}>
            {item.top3.map((m) => (
              <option key={m.sku_id} value={m.sku_id}>{m.sku_id} — {m.pct.toFixed(1)}%</option>
            ))}
            {item.top_pick === null && <option value="">(none)</option>}
          </select>
        ) : (
          <b className="mono">{item.top_pick ?? "none"}</b>
        )}
        {item.below_threshold && <span className="chip flagged">below threshold → made-to-order</span>}
        {override !== undefined && <span className="small" style={{ color: "var(--warning)" }}>edited — re-verification will run</span>}
      </div>

      {itemInPlainWords(item) && (
        <p className="small" style={{ margin: "4px 0 8px", color: "var(--ink-2)" }}>{itemInPlainWords(item)}</p>
      )}
      {disagreement && verdict!.reasons.map((r, i) => <div className="reason" key={i}>⚠ {r}</div>)}

      {best && (
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Parameter</th><th>RFP requires</th>
              {item.top3.map((m, i) => <th key={m.sku_id}>{i === 0 ? "★ " : ""}{m.sku_id} ({m.pct.toFixed(1)}%)</th>)}
            </tr>
          </thead>
          <tbody>
            {best.evidence.map((ev, row) => (
              <tr key={ev.param}>
                <td className="mono small">{ev.param}</td>
                <td>{ev.required}</td>
                {item.top3.map((m) => {
                  const cell = m.evidence[row];
                  const bad = cell && cell.score < 1;
                  return (
                    <td key={m.sku_id} className={bad ? "mismatch" : ""}>
                      {cell ? `${cell.actual ?? "—"} · ${cell.score.toFixed(2)}` : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}
