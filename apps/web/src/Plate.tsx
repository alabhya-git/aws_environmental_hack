// A plate drawn from real quantities: each mound is one calibrated scoop
// (a half mound is half a scoop). The amber rim arc is the share of what was
// served that came back. Nothing here animates on its own.

function moundCentres(n: number): Array<[number, number]> {
  if (n <= 0) return [];
  if (n === 1) return [[100, 100]];
  if (n === 2) return [[80, 100], [120, 100]];
  if (n <= 6) {
    const r = n === 3 ? 25 : n === 4 ? 29 : 34;
    return Array.from({ length: n }, (_, i) => {
      const a = (2 * Math.PI * i) / n - Math.PI / 2;
      return [100 + r * Math.cos(a), 100 + r * Math.sin(a)] as [number, number];
    });
  }
  const ring = n - 1;
  return [[100, 100] as [number, number]].concat(
    Array.from({ length: ring }, (_, i) => {
      const a = (2 * Math.PI * i) / ring - Math.PI / 2;
      return [100 + 41 * Math.cos(a), 100 + 41 * Math.sin(a)] as [number, number];
    }),
  );
}

function arc(cx: number, cy: number, r: number, frac: number): string {
  const f = Math.min(Math.max(frac, 0), 0.9999);
  const a = 2 * Math.PI * f - Math.PI / 2;
  const x = cx + r * Math.cos(a);
  const y = cy + r * Math.sin(a);
  return `M ${cx} ${cy - r} A ${r} ${r} 0 ${f > 0.5 ? 1 : 0} 1 ${x} ${y}`;
}

export function Plate({ scoops, returnedShare, size = 190, tone = "leaf", label }: { scoops: number; returnedShare?: number | null; size?: number; tone?: "leaf" | "ink" | "ghost"; label: string }) {
  const full = Math.floor(scoops + 1e-6);
  const half = scoops - full >= 0.24;
  const n = Math.min(full + (half ? 1 : 0), 9);
  const pts = moundCentres(n);
  const rad = n <= 2 ? 24 : n <= 4 ? 21 : n <= 6 ? 18 : 15.5;
  const fill = tone === "leaf" ? "#1f6b43" : tone === "ink" ? "#52514e" : "none";
  const stroke = tone === "ghost" ? "#898781" : "none";
  return (
    <svg viewBox="0 0 200 200" width={size} height={size} role="img" aria-label={label} style={{ maxWidth: "100%" }}>
      <circle cx="100" cy="100" r="96" fill="#fbfbf9" stroke="#b3b8ae" strokeWidth="2" />
      <circle cx="100" cy="100" r="72" fill="#e9ece6" stroke="#cfd3cb" strokeWidth="1.5" />
      {pts.map(([x, y], i) => {
        const isHalf = half && i === n - 1;
        if (isHalf) {
          return <path key={i} d={`M ${x - rad} ${y} A ${rad} ${rad} 0 0 1 ${x + rad} ${y} Z`} fill={fill} stroke={stroke} strokeWidth="2" strokeDasharray={tone === "ghost" ? "4 3" : undefined} opacity={tone === "leaf" ? 0.92 : 1} />;
        }
        return <circle key={i} cx={x} cy={y} r={rad} fill={fill} stroke={stroke} strokeWidth="2" strokeDasharray={tone === "ghost" ? "4 3" : undefined} opacity={tone === "leaf" ? 0.92 : 1} />;
      })}
      {returnedShare != null && returnedShare > 0.004 ? (
        <path d={arc(100, 100, 84, returnedShare)} fill="none" stroke="#e9b949" strokeWidth="9" strokeLinecap="round" />
      ) : null}
    </svg>
  );
}
