import { createContext, MouseEvent, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, call, getJson, lastCall, Mode, OpArgs, OpName, resetDemo, sandboxSize } from "./api";
import { getConfig, onAuthChange, RuntimeConfig, setConfig, signedInAs, signOut } from "./auth";
import { Evidence } from "./views/Evidence";
import { Kitchen } from "./views/Kitchen";
import { Landing } from "./views/Landing";
import { Learn } from "./views/Learn";
import { Serve } from "./views/Serve";
import { Setup } from "./views/Setup";
import { SignIn } from "./views/SignIn";
import { ErrorNote, Loading } from "./ui";

// ---------------------------------------------------------------- routing
function usePath(): [string, (to: string) => void] {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const go = useCallback((to: string) => {
    if (to !== window.location.pathname) window.history.pushState({}, "", to);
    setPath(to);
    window.scrollTo(0, 0);
  }, []);
  return [path, go];
}

const Nav = createContext<(to: string) => void>(() => undefined);
export const useNav = () => useContext(Nav);

export function Link({ to, className, children, current }: { to: string; className?: string; children: ReactNode; current?: boolean }) {
  const go = useNav();
  const onClick = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    go(to);
  };
  return (
    <a href={to} className={className} onClick={onClick} aria-current={current ? "page" : undefined}>
      {children}
    </a>
  );
}

// ---------------------------------------------------------------- workspace
export interface Boot {
  mode: Mode;
  role: string;
  operator: string;
  site_id: string;
  evidence_class: string;
  settings: any;
  foods: any[];
  calibrations: any[];
  sessions: any[];
  diners: any[];
  model_version: string;
  policy_version: string;
}

interface Workspace {
  mode: Mode;
  boot: Boot;
  reload: () => Promise<void>;
  api: <O extends OpName>(op: O, args: OpArgs[O]) => Promise<any>;
  sessionId: string | null;
  setSessionId: (id: string) => void;
  foodId: string | null;
  setFoodId: (id: string) => void;
  token: string;
  setToken: (t: string) => void;
  session: any | null;
  calibration: any | null;
}
const Ws = createContext<Workspace | null>(null);
export function useWs(): Workspace {
  const w = useContext(Ws);
  if (!w) throw new Error("no workspace");
  return w;
}

function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden>
      <circle cx="32" cy="32" r="30" fill="#fcfcfb" stroke="#17201a" strokeWidth="3" />
      <circle cx="32" cy="32" r="20" fill="none" stroke="#c3c2b7" strokeWidth="2" />
      <path d="M32 32 L32 12 A20 20 0 0 1 49.3 42 Z" fill="#1f6b43" />
    </svg>
  );
}

function Footer({ cfg }: { cfg: RuntimeConfig | null }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 1500);
    return () => window.clearInterval(id);
  }, []);
  return (
    <footer className="foot">
      {cfg?.aws ? (
        <>
          <span>Inference on AWS Lambda <code>{cfg.aws.function}</code> in <code>{cfg.aws.region}</code></span>
          <span>Pilot records in DynamoDB <code>{cfg.aws.table}</code></span>
        </>
      ) : (
        <span>Running against a local development server (not AWS)</span>
      )}
      {lastCall.requestId ? <span>Last request <code>{lastCall.requestId}</code> · {lastCall.ms} ms</span> : null}
      <span>v{cfg?.version} · rev <code>{cfg?.revision}</code></span>
    </footer>
  );
}

const TABS: Array<[string, string]> = [["serve", "Serve"], ["kitchen", "Kitchen"], ["learn", "Learn"], ["evidence", "Evidence"], ["setup", "Setup"]];

function WorkspaceShell({ mode, view, cfg }: { mode: Mode; view: string; cfg: RuntimeConfig | null }) {
  const go = useNav();
  const [boot, setBoot] = useState<Boot | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [foodId, setFoodId] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);

  const reload = useCallback(async () => {
    try {
      const b: Boot = await call(mode, "bootstrap", {});
      setBoot(b);
      setError(null);
      setSessionId((cur) => (cur && b.sessions.some((s) => s.id === cur) ? cur : (b.sessions.find((s) => s.status === "open") || b.sessions[0])?.id || null));
      setFoodId((cur) => (cur && b.foods.some((f) => f.id === cur) ? cur : b.foods[0]?.id || null));
    } catch (e) {
      const err = e as ApiError;
      if (mode === "pilot" && err.code === "UNAUTHENTICATED") go("/signin");
      else setError(e);
    }
  }, [mode, go]);

  useEffect(() => {
    setBoot(null);
    setToken("");
    reload();
  }, [reload]);
  useEffect(() => onAuthChange(() => { if (mode === "pilot" && !signedInAs()) go("/signin"); }), [mode, go]);

  // keep the selected food on the selected service's menu, preferring one whose scoop is calibrated
  useEffect(() => {
    if (!boot) return;
    const session = boot.sessions.find((s) => s.id === sessionId);
    const menu: string[] = session ? session.food_ids : boot.foods.map((f) => f.id);
    if (foodId && menu.includes(foodId)) return;
    const calibrated = menu.find((id) => boot.calibrations.some((c) => c.food_id === id));
    setFoodId(calibrated || menu[0] || null);
  }, [boot, sessionId, foodId]);

  const api = useCallback(<O extends OpName>(op: O, args: OpArgs[O]) => call(mode, op, args), [mode]);
  const ws = useMemo<Workspace | null>(() => {
    if (!boot) return null;
    const session = boot.sessions.find((s) => s.id === sessionId) || null;
    const calibration = boot.calibrations.find((c) => c.food_id === foodId) || null;
    return { mode, boot, reload, api, sessionId, setSessionId, foodId, setFoodId, token, setToken, session, calibration };
  }, [mode, boot, reload, api, sessionId, foodId, token]);

  const doReset = () => {
    resetDemo();
    setConfirmReset(false);
    setToken("");
    reload();
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-in">
          <Link to="/" className="brand"><Logo /> ProofPlate</Link>
          <nav className="tabs" aria-label="Sections">
            {TABS.map(([k, label]) => (
              <Link key={k} to={`/${mode}/${k}`} className="tab" current={view === k}>{label}</Link>
            ))}
          </nav>
          <span className="spacer" />
          <div className="modebar">
            {mode === "demo" ? (
              <>
                <span className="chip sim"><span className="dot" aria-hidden /> Demo · simulated data</span>
                {confirmReset ? (
                  <>
                    <span>Clear your demo entries?</span>
                    <button className="btn sm dark" onClick={doReset}>Yes, reset</button>
                    <button className="btn sm ghost" onClick={() => setConfirmReset(false)}>Cancel</button>
                  </>
                ) : (
                  <button className="btn sm" onClick={() => setConfirmReset(true)} title="Clears only this browser's demo entries">Reset demo{sandboxSize() ? ` (${sandboxSize()})` : ""}</button>
                )}
              </>
            ) : (
              <>
                <span className="chip obs"><span className="dot" aria-hidden /> Pilot · {boot?.site_id || "…"}</span>
                <span className="muted">{boot?.operator}{boot ? ` · ${boot.role}` : ""}</span>
                <button className="btn sm" onClick={() => { signOut(); go("/"); }}>Sign out</button>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="page">
        {error ? (
          <div className="stack">
            <ErrorNote error={error} onRetry={reload} />
            {(error as ApiError).code === "NOT_A_MEMBER" ? <p className="muted">You are signed in, but no dining site is attached to this account. A site manager adds operators with <code>scripts/create_operator.ps1</code>.</p> : null}
          </div>
        ) : !ws ? (
          <Loading label="Opening workspace…" />
        ) : (
          <Ws.Provider value={ws}>
            {view === "serve" ? <Serve /> : view === "kitchen" ? <Kitchen /> : view === "learn" ? <Learn /> : view === "evidence" ? <Evidence /> : view === "setup" ? <Setup /> : <NotFound />}
          </Ws.Provider>
        )}
      </main>
      <Footer cfg={cfg} />
    </div>
  );
}

function NotFound() {
  return (
    <div className="empty">
      <h2>Page not found</h2>
      <p style={{ marginTop: 8 }}><Link to="/">Back to the start</Link></p>
    </div>
  );
}

export function App() {
  const [path, go] = usePath();
  const [cfg, setCfg] = useState<RuntimeConfig | null>(getConfig());
  const [cfgError, setCfgError] = useState<unknown>(null);
  useEffect(() => {
    getJson<RuntimeConfig>("/api/config")
      .then((c) => { setConfig(c); setCfg(c); })
      .catch(setCfgError);
  }, []);

  const parts = path.split("/").filter(Boolean);
  let body: ReactNode;
  if (cfgError) {
    body = <div className="page"><ErrorNote error={cfgError} onRetry={() => window.location.reload()} /></div>;
  } else if (!cfg) {
    body = <div className="page"><Loading label="Connecting…" /></div>;
  } else if (parts.length === 0) {
    body = <Landing cfg={cfg} />;
  } else if (parts[0] === "signin") {
    body = <SignIn />;
  } else if ((parts[0] === "demo" || parts[0] === "pilot") && parts.length <= 2) {
    body = <WorkspaceShell key={parts[0]} mode={parts[0]} view={parts[1] || "serve"} cfg={cfg} />;
  } else {
    body = <div className="page"><NotFound /></div>;
  }
  return <Nav.Provider value={go}>{body}</Nav.Provider>;
}

export { Footer, Logo };
