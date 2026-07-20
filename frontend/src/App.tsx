import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Assistant from "./Assistant";
import Pipeline from "./pages/Pipeline";
import RunView from "./pages/RunView";
import Bids from "./pages/Bids";
import Escalations from "./pages/Escalations";
import RfpDetail from "./pages/RfpDetail";

export default function App() {
  const [openEsc, setOpenEsc] = useState(0);

  useEffect(() => {
    const load = () => api.stats().then((s) => setOpenEsc(s.open_escalations)).catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo">Bid<span>Pilot</span></div>
        <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
          ⛁ Tender desk
        </NavLink>
        <NavLink to="/bids" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
          ✉ Bids
        </NavLink>
        <NavLink to="/escalations" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
          ⚠ Alerts {openEsc > 0 && <span className="dot" title={`${openEsc} open`} />}
        </NavLink>
        <div style={{ marginTop: 28, padding: "0 10px" }} className="small muted">
          Agents recommend.<br />Humans decide.
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Pipeline />} />
          <Route path="/rfps/:rfpId" element={<RfpDetail />} />
          <Route path="/runs/:runId" element={<RunView />} />
          <Route path="/bids" element={<Bids />} />
          <Route path="/escalations" element={<Escalations />} />
        </Routes>
      </main>
      <Assistant />
    </div>
  );
}
