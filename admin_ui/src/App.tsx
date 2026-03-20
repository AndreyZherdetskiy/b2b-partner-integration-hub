import { useEffect, useState } from "react";
import { DeadLetters } from "./pages/DeadLetters";
import { DeliveriesList } from "./pages/DeliveriesList";
import { DeliveryDetail } from "./pages/DeliveryDetail";
import { PartnerCompliance } from "./pages/PartnerCompliance";
import { PartnersList } from "./pages/PartnersList";
import { ReplayApprovals } from "./pages/ReplayApprovals";
import { Sandbox } from "./pages/Sandbox";
import { TokenInput } from "./components/TokenInput";

type Route =
  | { page: "deliveries" }
  | { page: "delivery"; id: string }
  | { page: "dead-letters" }
  | { page: "partners" }
  | { page: "partner-compliance"; id: string }
  | { page: "sandbox" }
  | { page: "replay-approvals" };

function parseRoute(path: string): Route {
  if (path === "/dead-letters") {
    return { page: "dead-letters" };
  }
  if (path === "/partners") {
    return { page: "partners" };
  }
  if (path === "/sandbox") {
    return { page: "sandbox" };
  }
  if (path === "/replay-approvals") {
    return { page: "replay-approvals" };
  }
  const complianceMatch = path.match(/^\/partners\/([^/]+)\/compliance$/);
  if (complianceMatch) {
    return { page: "partner-compliance", id: complianceMatch[1] };
  }
  const deliveryMatch = path.match(/^\/deliveries\/([^/]+)$/);
  if (deliveryMatch) {
    return { page: "delivery", id: deliveryMatch[1] };
  }
  return { page: "deliveries" };
}

function navigate(path: string) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname));

  useEffect(() => {
    function onPopState() {
      setRoute(parseRoute(window.location.pathname));
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function goDeliveries() {
    navigate("/deliveries");
    setRoute({ page: "deliveries" });
  }

  function goDelivery(id: string) {
    navigate(`/deliveries/${id}`);
    setRoute({ page: "delivery", id });
  }

  function goDeadLetters() {
    navigate("/dead-letters");
    setRoute({ page: "dead-letters" });
  }

  function goPartners() {
    navigate("/partners");
    setRoute({ page: "partners" });
  }

  function goPartnerCompliance(id: string) {
    navigate(`/partners/${id}/compliance`);
    setRoute({ page: "partner-compliance", id });
  }

  function goSandbox() {
    navigate("/sandbox");
    setRoute({ page: "sandbox" });
  }

  function goReplayApprovals() {
    navigate("/replay-approvals");
    setRoute({ page: "replay-approvals" });
  }

  const partnersActive =
    route.page === "partners" || route.page === "partner-compliance";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <strong>Hub Admin</strong>
          <span className="subtitle">thin UI — Admin API only</span>
        </div>
        <nav className="nav-links">
          <button type="button" className={route.page === "deliveries" || route.page === "delivery" ? "active" : ""} onClick={goDeliveries}>
            Deliveries
          </button>
          <button type="button" className={route.page === "dead-letters" ? "active" : ""} onClick={goDeadLetters}>
            Dead letters
          </button>
          <button type="button" className={partnersActive ? "active" : ""} onClick={goPartners}>
            Partners
          </button>
          <button type="button" className={route.page === "sandbox" ? "active" : ""} onClick={goSandbox}>
            Sandbox
          </button>
          <button
            type="button"
            className={route.page === "replay-approvals" ? "active" : ""}
            onClick={goReplayApprovals}
          >
            Approvals
          </button>
        </nav>
        <TokenInput />
      </header>
      <main className="app-main">
        {route.page === "deliveries" ? (
          <DeliveriesList onSelect={goDelivery} />
        ) : null}
        {route.page === "delivery" ? (
          <DeliveryDetail id={route.id} onBack={goDeliveries} />
        ) : null}
        {route.page === "dead-letters" ? (
          <DeadLetters onSelectDelivery={goDelivery} />
        ) : null}
        {route.page === "partners" ? (
          <PartnersList onSelectCompliance={goPartnerCompliance} />
        ) : null}
        {route.page === "partner-compliance" ? (
          <PartnerCompliance partnerId={route.id} onBack={goPartners} />
        ) : null}
        {route.page === "sandbox" ? <Sandbox /> : null}
        {route.page === "replay-approvals" ? (
          <ReplayApprovals onSelectDelivery={goDelivery} />
        ) : null}
      </main>
    </div>
  );
}
