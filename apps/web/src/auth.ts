// Operator sign-in against the stack's Cognito user pool, using plain fetch.
// Pool and client ids come from /api/config (real stack outputs). Tokens are
// kept in sessionStorage so they end with the browser tab.
export interface RuntimeConfig {
  auth: "dev" | "cognito";
  cognito: { region: string; userPoolId: string; clientId: string } | null;
  aws: { region: string; function: string; table: string } | null;
  version: string;
  revision: string;
}

const KEY = "proofplate.session.v1";
const DEV_KEY = "proofplate.devuser.v1";
let config: RuntimeConfig | null = null;
const listeners = new Set<() => void>();

interface Session {
  idToken: string;
  refreshToken?: string;
  expiresAt: number;
  email: string;
}

export function setConfig(c: RuntimeConfig): void {
  config = c;
}
export function getConfig(): RuntimeConfig | null {
  return config;
}
export function onAuthChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function emit(): void {
  listeners.forEach((fn) => fn());
}

function load(): Session | null {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}
function save(s: Session | null): void {
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
  emit();
}

export function devUser(): string | null {
  if (config?.auth !== "dev") return null;
  try {
    return sessionStorage.getItem(DEV_KEY);
  } catch {
    return null;
  }
}
export function signedInAs(): string | null {
  return load()?.email || devUser();
}
export function signOut(): void {
  try {
    sessionStorage.removeItem(DEV_KEY);
  } catch {
    /* ignore */
  }
  save(null);
}

async function cognito(target: string, payload: unknown): Promise<any> {
  if (!config?.cognito) throw new Error("Sign-in is not configured for this deployment.");
  const res = await fetch(`https://cognito-idp.${config.cognito.region}.amazonaws.com/`, {
    method: "POST",
    headers: { "content-type": "application/x-amz-json-1.1", "x-amz-target": `AWSCognitoIdentityProviderService.${target}` },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const kind = String(data.__type || "").split("#").pop();
    const friendly: Record<string, string> = {
      NotAuthorizedException: "Email or password is not correct.",
      UserNotFoundException: "Email or password is not correct.",
      InvalidPasswordException: "That password does not meet the policy: 10+ characters with upper case, lower case and a number.",
      PasswordResetRequiredException: "Your password must be reset by the site manager.",
      TooManyRequestsException: "Too many attempts. Wait a minute and try again.",
    };
    throw new Error(friendly[kind || ""] || data.message || "Sign-in failed.");
  }
  return data;
}

function store(result: any, email: string, refreshToken?: string): void {
  save({
    idToken: result.IdToken,
    refreshToken: result.RefreshToken || refreshToken,
    expiresAt: Date.now() + (result.ExpiresIn || 3600) * 1000,
    email,
  });
}

export type SignInResult = { status: "ok" } | { status: "new_password"; session: string };

export async function signIn(email: string, password: string): Promise<SignInResult> {
  if (config?.auth === "dev") {
    // Local development only: the server refuses to start with dev auth outside PROOFPLATE_ENV=local.
    sessionStorage.setItem(DEV_KEY, email.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40) || "dev");
    emit();
    return { status: "ok" };
  }
  const out = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: config!.cognito!.clientId,
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  if (out.ChallengeName === "NEW_PASSWORD_REQUIRED") return { status: "new_password", session: out.Session };
  if (!out.AuthenticationResult) throw new Error("This account needs a sign-in step this app does not support.");
  store(out.AuthenticationResult, email);
  return { status: "ok" };
}

export async function completeNewPassword(email: string, newPassword: string, session: string): Promise<void> {
  const out = await cognito("RespondToAuthChallenge", {
    ChallengeName: "NEW_PASSWORD_REQUIRED",
    ClientId: config!.cognito!.clientId,
    Session: session,
    ChallengeResponses: { USERNAME: email, NEW_PASSWORD: newPassword },
  });
  if (!out.AuthenticationResult) throw new Error("Could not set the new password.");
  store(out.AuthenticationResult, email);
}

export async function currentToken(): Promise<string | null> {
  const s = load();
  if (!s) return null;
  if (s.expiresAt - Date.now() > 60_000) return s.idToken;
  if (!s.refreshToken) {
    save(null);
    return null;
  }
  try {
    const out = await cognito("InitiateAuth", {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: config!.cognito!.clientId,
      AuthParameters: { REFRESH_TOKEN: s.refreshToken },
    });
    store(out.AuthenticationResult, s.email, s.refreshToken);
    return out.AuthenticationResult.IdToken;
  } catch {
    save(null);
    return null;
  }
}
