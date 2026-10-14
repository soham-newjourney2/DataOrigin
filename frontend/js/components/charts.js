// Hand-written SVG charts (no libraries).
import { scoreClass } from "./dom.js";

const NS = "http://www.w3.org/2000/svg";

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of children.flat()) if (c !== null && c !== undefined) el.append(c instanceof Node ? c : String(c));
  return el;
}

/** Semicircle gauge for a 0–100 score. */
export function gauge(score, label = "Quality score") {
  const cx = 110, cy = 110, r = 88;
  const value = Math.max(0, Math.min(100, score ?? 0));
  const theta = Math.PI * (1 - value / 100);
  const x = cx + r * Math.cos(theta), y = cy - r * Math.sin(theta);
  const arc = (x2, y2) => `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  return svg("svg", { class: "gauge", viewBox: "0 0 220 130", role: "img", "aria-label": `${label}: ${score ?? "n/a"} of 100` },
    svg("path", { d: arc(cx + r, cy), class: "gauge-track" }),
    value > 0 ? svg("path", { d: arc(x, y), class: `gauge-arc ${scoreClass(score)}`, pathLength: 100 }) : null,
    svg("text", { x: cx, y: cy - 14, "text-anchor": "middle", class: "gauge-value" }, score === null || score === undefined ? "—" : score.toFixed(1)),
    svg("text", { x: cx, y: cy + 12, "text-anchor": "middle", class: "gauge-label" }, label),
  );
}

/** Horizontal bar chart. items: [{label, value}]; format(value) → string. */
export function barChart(items, { format = (v) => `${v}`, maxValue, title = "Bar chart" } = {}) {
  const rowH = 24, top = 6, labelW = 150, valueW = 64, width = 640;
  const plotW = width - labelW - valueW;
  const max = maxValue ?? Math.max(1, ...items.map((i) => i.value));
  const height = top * 2 + items.length * rowH;
  const truncate = (s) => (s.length > 22 ? s.slice(0, 21) + "…" : s);
  const rows = items.map((item, i) => {
    const y = top + i * rowH;
    const w = item.value > 0 ? Math.max(2, (item.value / max) * plotW) : 0;
    return svg("g", {},
      svg("title", {}, `${item.label}: ${format(item.value)}`),
      svg("text", { x: labelW - 8, y: y + rowH / 2 + 4, "text-anchor": "end" }, truncate(item.label)),
      svg("rect", { x: labelW, y: y + 5, width: plotW, height: rowH - 10, rx: 3, class: "track" }),
      w ? svg("rect", { x: labelW, y: y + 5, width: w.toFixed(1), height: rowH - 10, rx: 3, class: "bar" }) : null,
      svg("text", { x: labelW + plotW + 8, y: y + rowH / 2 + 4, class: item.value > 0 ? "val" : null }, format(item.value)),
    );
  });
  return svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": title }, rows);
}
