// A small, safe math parser for the built-in fake engine. Like the real
// engine's sympy path, learner text is first reduced to a whitelist (digits, x,
// operators, brackets, sqrt ln exp sin cos tan; ≤ 80 characters); anything else
// is "not math" and goes to the text grader. Nothing is ever evaluated as code.

const FUNCS = {
  sqrt: Math.sqrt, ln: Math.log, log: Math.log, exp: Math.exp,
  sin: Math.sin, cos: Math.cos, tan: Math.tan
};
const MAX_CHARS = 80;

/** Normalizes Unicode math and strips an "f'(x) =" / "y =" prefix. */
function normalize(text) {
  let s = String(text ?? "").trim();
  s = s.replace(/^\s*(?:[a-zA-Z]\s*['′’]*\s*\(\s*x\s*\)|y\s*['′’]*|d\s*y\s*\/\s*d\s*x)\s*=\s*/, "");
  s = s
    .replace(/[−–—]/g, "-").replace(/[·×∙⋅]/g, "*").replace(/÷/g, "/")
    .replace(/²/g, "^2").replace(/³/g, "^3").replace(/⁴/g, "^4").replace(/√/g, "sqrt")
    .replace(/\*\*/g, "^")
    .replace(/(\d),(\d)/g, "$1.$2")
    .replace(/\s+/g, "");
  return s;
}

function tokenize(s) {
  const tokens = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/[0-9.]/.test(c)) {
      let j = i;
      while (j < s.length && /[0-9.]/.test(s[j])) j++;
      const n = Number(s.slice(i, j));
      if (!Number.isFinite(n)) return null;
      tokens.push({ t: "num", v: n });
      i = j;
    } else if (/[a-z]/i.test(c)) {
      const fn = Object.keys(FUNCS).sort((a, b) => b.length - a.length).find((f) => s.startsWith(f, i));
      if (fn) { tokens.push({ t: "fn", v: fn }); i += fn.length; }
      else if (c === "x") { tokens.push({ t: "x" }); i++; }
      else if (c === "e" && !/[a-z]/i.test(s[i + 1] || "")) { tokens.push({ t: "num", v: Math.E }); i++; }
      else if (s.startsWith("pi", i)) { tokens.push({ t: "num", v: Math.PI }); i += 2; }
      else return null;
    } else if ("+-*/^()".includes(c)) {
      tokens.push({ t: c });
      i++;
    } else return null;
  }
  // Implicit multiplication: 2x, x(…), )(, )x, 2sqrt(…)
  const out = [];
  tokens.forEach((tok, k) => {
    const prev = out[out.length - 1];
    const prevEnds = prev && (prev.t === "num" || prev.t === "x" || prev.t === ")");
    const starts = tok.t === "num" || tok.t === "x" || tok.t === "(" || tok.t === "fn";
    if (k > 0 && prevEnds && starts) out.push({ t: "*" });
    out.push(tok);
  });
  return out;
}

/** Recursive-descent parser → a tree of plain objects. */
function parseTokens(tokens) {
  let pos = 0;
  const peek = () => tokens[pos];
  const eat = (t) => (peek()?.t === t ? tokens[pos++] : null);

  function expr() {
    let node = term();
    while (node && (peek()?.t === "+" || peek()?.t === "-")) {
      const op = tokens[pos++].t;
      const right = term();
      if (!right) return null;
      node = { op, a: node, b: right };
    }
    return node;
  }
  function term() {
    let node = unary();
    while (node && (peek()?.t === "*" || peek()?.t === "/")) {
      const op = tokens[pos++].t;
      const right = unary();
      if (!right) return null;
      node = { op, a: node, b: right };
    }
    return node;
  }
  function unary() {
    if (eat("-")) { const n = unary(); return n && { op: "neg", a: n }; }
    if (eat("+")) return unary();
    return power();
  }
  function power() {
    const base = atom();
    if (base && eat("^")) {
      const exp = unary();
      return exp && { op: "^", a: base, b: exp };
    }
    return base;
  }
  function atom() {
    const tok = peek();
    if (!tok) return null;
    if (tok.t === "num") { pos++; return { num: tok.v }; }
    if (tok.t === "x") { pos++; return { x: true }; }
    if (tok.t === "fn") {
      pos++;
      if (!eat("(")) return null;
      const arg = expr();
      return arg && eat(")") ? { fn: tok.v, a: arg } : null;
    }
    if (eat("(")) {
      const inner = expr();
      return inner && eat(")") ? inner : null;
    }
    return null;
  }
  const tree = expr();
  return tree && pos === tokens.length ? tree : null;
}

function evaluate(node, x) {
  if ("num" in node) return node.num;
  if (node.x) return x;
  if (node.fn) return FUNCS[node.fn](evaluate(node.a, x));
  const a = evaluate(node.a, x);
  switch (node.op) {
    case "neg": return -a;
    case "+": return a + evaluate(node.b, x);
    case "-": return a - evaluate(node.b, x);
    case "*": return a * evaluate(node.b, x);
    case "/": return a / evaluate(node.b, x);
    case "^": return Math.pow(a, evaluate(node.b, x));
    default: return NaN;
  }
}

/** Parses learner math; returns a function of x, or null when it is not math. */
function parse(text) {
  const s = normalize(text);
  if (!s || s.length > MAX_CHARS || !/^[0-9x.+\-*/^()a-z]+$/i.test(s)) return null;
  const tokens = tokenize(s);
  if (!tokens || !tokens.length) return null;
  const tree = parseTokens(tokens);
  if (!tree) return null;
  return (x) => evaluate(tree, x);
}

const SAMPLES = [0.37, 0.81, 1.3, 1.9, 2.6, 3.4, 0.55, 4.1];

/** True when f and g agree at the sample points where both are defined. */
function sameFunction(f, g) {
  let compared = 0;
  for (const x of SAMPLES) {
    const a = f(x);
    const b = g(x);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    compared++;
    if (Math.abs(a - b) > 1e-6 * Math.max(1, Math.abs(a), Math.abs(b))) return false;
  }
  return compared >= 4;
}

/** Numeric derivative (central difference), for checking derivative claims. */
const derivative = (f) => (x) => {
  const h = 1e-5;
  return (f(x + h) - f(x - h)) / (2 * h);
};

function sameLoose(f, g) {
  let compared = 0;
  for (const x of SAMPLES) {
    const a = f(x);
    const b = g(x);
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    compared++;
    if (Math.abs(a - b) > 1e-4 * Math.max(1, Math.abs(a), Math.abs(b))) return false;
  }
  return compared >= 4;
}

/** Checks that `claimed` is the derivative of `fn` (both texts). null = cannot tell. */
function isDerivative(fnText, claimedText) {
  const f = parse(fnText);
  const d = parse(claimedText);
  if (!f || !d) return null;
  return sameLoose(derivative(f), d);
}

module.exports = { parse, normalize, sameFunction, sameLoose, derivative, isDerivative, MAX_CHARS };
