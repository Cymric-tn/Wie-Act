// Lumi logo + companion. Usage:
//   <LumiLogo />                      wordmark lockup for headers
//   <LumiSpark state="thinking" />    companion: "rest" | "thinking" | "mastered"
//   <LumiSpark halo={96} />           the same, inside a radial cyan halo of that size (it pulses while thinking)
const SPARK = 'M46 8 C49 36 56 44 92 52 C58 56 50 62 46 94 C42 62 34 56 10 50 C34 46 43 38 46 8 Z';

export function LumiSpark({ size = 32, state = 'rest', label, halo }) {
  const svg = (
    <svg
      width={size} height={size} viewBox="0 0 100 100"
      className={state === 'thinking' && !halo ? 'lumi-thinking' : undefined}
      role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}
    >
      {state === 'thinking' && !halo && <circle cx="46" cy="51" r="44" fill="var(--lumi-glow)" fillOpacity="0.18" />}
      <path d={SPARK} fill={state === 'mastered' ? 'var(--lumi-spark)' : '#fff'} />
      {state === 'mastered'
        ? <path d="M38 51l6 6 11-12" stroke="var(--lumi-void)" strokeWidth="4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        : <circle cx="46" cy="51" r="7" fill="var(--lumi-glow)" />}
    </svg>
  );
  if (!halo) return svg;
  return <span className={`lumi-halo lumi-halo-${state}`} style={{ '--halo': `${halo}px` }}>{svg}</span>;
}

export function LumiLogo({ height = 32 }) {
  return (
    <span aria-label="Lumi" role="img" dir="ltr" style={{ display: 'inline-flex', alignItems: 'center', gap: height * 0.3 }}>
      <LumiSpark size={height} />
      <span aria-hidden="true" style={{ font: `600 ${height * 1.1}px var(--lumi-font-display)`, letterSpacing: '-0.035em', lineHeight: 1 }}>lumi</span>
    </span>
  );
}
