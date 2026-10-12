import { FormEvent, useState } from "react";
import { Link, Logo, useNav } from "../App";
import { completeNewPassword, getConfig, signIn } from "../auth";
import { Field } from "../ui";

export function SignIn() {
  const go = useNav();
  const cfg = getConfig();
  const dev = cfg?.auth === "dev";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (challenge) {
        await completeNewPassword(email, newPassword, challenge);
        go("/pilot/serve");
      } else {
        const r = await signIn(email, password);
        if (r.status === "new_password") setChallenge(r.session);
        else go("/pilot/serve");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-in">
          <Link to="/" className="brand"><Logo /> ProofPlate</Link>
          <span className="spacer" />
          <Link to="/demo/serve" className="btn sm">Try the demo instead</Link>
        </div>
      </header>
      <main className="page" style={{ maxWidth: 460 }}>
        <form className="card stack" onSubmit={submit} style={{ marginTop: 30 }}>
          <h2>{challenge ? "Choose a new password" : "Pilot workspace sign-in"}</h2>
          <p className="muted small">
            {dev
              ? "Local development server: type any short name to sign in as that development user. This mode cannot run on a deployed stack."
              : "For invited operators and kitchen managers. Accounts are created by the site manager; there is no public sign-up."}
          </p>
          {challenge ? (
            <Field label="New password" hint="At least 10 characters with upper case, lower case and a number.">
              <input className="input" type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required minLength={10} autoFocus />
            </Field>
          ) : (
            <>
              <Field label={dev ? "Development user name" : "Email"}>
                <input className="input" type={dev ? "text" : "email"} autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
              </Field>
              {dev ? null : (
                <Field label="Password">
                  <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                </Field>
              )}
            </>
          )}
          {error ? <div className="note bad" role="alert">{error}</div> : null}
          <button className="btn primary big" disabled={busy}>{busy ? "Signing in…" : challenge ? "Save and continue" : "Sign in"}</button>
        </form>
      </main>
    </div>
  );
}
