import { useEffect, useState } from "react";
import { api, CatalogSku, inr } from "../api";

const SPEC_ORDER = ["cable_type", "voltage_grade", "conductor_material", "core_count",
  "cross_section_sqmm", "insulation_type", "armouring", "standard", "temp_rating", "sheath_type"];

export default function Catalog() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [items, setItems] = useState<CatalogSku[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.catalogStats().then((s) => setCategories(s.categories)).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      api.catalogSkus(q, category)
        .then((d) => { setItems(d.items); setTotal(d.total); })
        .catch(() => {})
        .finally(() => setLoading(false));
    }, 250); // debounce typing
    return () => clearTimeout(t);
  }, [q, category]);

  const loadMore = () =>
    api.catalogSkus(q, category, items.length)
      .then((d) => setItems((prev) => [...prev, ...d.items]))
      .catch(() => {});

  return (
    <>
      <h1 className="page-title">Product catalog</h1>
      <p className="page-sub">
        {total} products the matcher can offer. Codes are self-describing:
        category – type – cores – sqmm – voltage – <b>A</b>rmoured/<b>U</b>narmoured.
        Rows marked <i>extended</i> carry estimated prices — verify before quoting.
      </p>

      <div className="card">
        <div className="btn-row" style={{ marginBottom: 14 }}>
          <input type="text" placeholder="search — e.g. 11 kv aluminium, flat cable, IS 694…"
                 value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 320 }} />
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <div className="spacer" />
          <span className="small muted">{loading ? "searching…" : `${total} match(es)`}</span>
        </div>

        <table>
          <thead>
            <tr><th>Code</th><th>Category</th><th>Specifications</th><th>Price</th></tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.sku_id}>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>{s.sku_id}
                  {s.source.toLowerCase().includes("extended") &&
                    <div className="small muted">extended</div>}
                </td>
                <td>{s.category}</td>
                <td className="small">
                  {SPEC_ORDER.filter((k) => s.specs[k]).map((k) => (
                    <span key={k} style={{ marginRight: 10, display: "inline-block" }}>
                      <span className="muted">{k.replaceAll("_", " ")}:</span> {s.specs[k]}
                    </span>
                  ))}
                </td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  {s.unit_price != null
                    ? <>{inr(s.unit_price)}<span className="muted small">/{s.unit}</span></>
                    : <span className="reason">no price</span>}
                </td>
              </tr>
            ))}
            {!loading && items.length === 0 && (
              <tr><td colSpan={4}>
                <div className="empty"><div className="glyph">🔍</div>Nothing matches — try fewer words.</div>
              </td></tr>
            )}
          </tbody>
        </table>

        {items.length < total && (
          <div className="btn-row" style={{ marginTop: 14, justifyContent: "center" }}>
            <button onClick={loadMore}>Show more ({total - items.length} remaining)</button>
          </div>
        )}
      </div>
    </>
  );
}
