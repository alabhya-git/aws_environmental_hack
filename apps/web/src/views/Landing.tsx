import { Footer, Link, Logo } from "../App";
import { RuntimeConfig } from "../auth";
import { Plate } from "../Plate";

export function Landing({ cfg }: { cfg: RuntimeConfig }) {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-in">
          <Link to="/" className="brand"><Logo /> ProofPlate</Link>
          <span className="spacer" />
          <div className="row">
            <Link to="/signin" className="btn sm">Pilot sign-in</Link>
            <Link to="/demo/serve" className="btn sm primary">Open the demo</Link>
          </div>
        </div>
      </header>
      <main className="page">
        <section className="hero">
          <div>
            <h1>Serve a better first portion. Keep seconds easy. Show what changed.</h1>
            <p className="lede">
              For the person at the serving counter and the kitchen manager behind them. ProofPlate learns how much of a dish each
              diner chooses to eat, suggests the first portion, and turns what comes back on the plates into the next meal's plan.
            </p>
            <p style={{ marginTop: 14, maxWidth: "34em" }}>
              Food that is never wasted is food that never needs to be collected, sorted or processed, so this works at the start of the waste chain rather than the end.
            </p>
            <div className="row cta">
              <Link to="/demo/serve" className="btn big primary">Serve a plate in the demo</Link>
              <Link to="/signin" className="btn big">Pilot workspace</Link>
            </div>
            <p className="small muted" style={{ marginTop: 12 }}>
              The demo runs on generated data and says so on every screen. The pilot workspace starts empty and holds only what your operators record.
            </p>
          </div>
          <div className="stack">
            <h3>The same empty plate, read two ways</h3>
            <div className="duo">
              <div>
                <Plate scoops={3} size={118} tone="ghost" label="An empty plate after three scoops" />
                <p style={{ fontWeight: 600, marginTop: 8 }}>“That was enough.”</p>
                <p className="small muted">Appetite was about three scoops.</p>
              </div>
              <div>
                <Plate scoops={3} size={118} tone="ghost" label="The same empty plate after three scoops" />
                <p style={{ fontWeight: 600, marginTop: 8 }}>“I wanted more.”</p>
                <p className="small muted">Appetite was at least three scoops.</p>
              </div>
            </div>
            <p className="small">
              A scale cannot tell these apart, and a system that treats both as “three scoops was right” keeps under-serving the
              hungry diner. ProofPlate records which it was and learns from each correctly.
            </p>
          </div>
        </section>

        <section className="card" style={{ marginTop: 40 }}>
          <h2>How a plate becomes the next suggestion</h2>
          <ol className="loop" style={{ marginTop: 12 }}>
            <li><b>1</b><span><strong>Calibrate the scoop.</strong> <span className="muted">Weigh about twenty scoops of the cooked dish, once per recipe.</span></span></li>
            <li><b>2</b><span><strong>Suggest a first portion.</strong> <span className="muted">With the likely range and the reason. The diner can always choose differently.</span></span></li>
            <li><b>3</b><span><strong>Record seconds and the return.</strong> <span className="muted">Grams left, from the scale or from before and after photos, and “was that enough?”.</span></span></li>
            <li><b>4</b><span><strong>Update the estimate.</strong> <span className="muted">“Wanted more” is a lower bound, not a target. Unclear plates are not used.</span></span></li>
            <li><b>5</b><span><strong>Plan the kitchen.</strong> <span className="muted">Initial batch, reserve, and a plain warning when a plan cannot work.</span></span></li>
          </ol>
        </section>

        <section className="card" style={{ marginTop: 40 }}>
          <h2>Limits worth knowing before you rely on it</h2>
          <dl className="facts" style={{ marginTop: 12 }}>
            <dt>Not a health tool</dt><dd>No names, faces, calories or body data. A suggestion never overrides what a diner asks for.</dd>
            <dt>Numbers are labelled</dt><dd>Every figure is observed, predicted or simulated, with its sample size. A missing return is missing, not zero waste.</dd>
            <dt>Photos stay on the device</dt><dd>Plate photos are analysed in the browser. Only the estimated grams, its uncertainty and the area figures are saved.</dd>
            <dt>No field results yet</dt><dd>Nothing here has been measured in a real kitchen. Simulated comparisons are not savings.</dd>
            <dt>Runs on AWS</dt><dd>Lambda runs the model, API Gateway fronts it, DynamoDB keeps pilot records and Cognito signs operators in.</dd>
          </dl>
        </section>
      </main>
      <Footer cfg={cfg} />
    </div>
  );
}
