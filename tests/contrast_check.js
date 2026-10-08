// Self-contained WCAG 2.x contrast checker — no external library.
// Walks visible leaf text nodes, finds each one's effective background
// (nearest ancestor with a non-transparent background-color, else white),
// computes the WCAG relative-luminance contrast ratio, and flags anything
// below the AA threshold (4.5:1 normal text, 3:1 "large" text: >=24px, or
// >=18.66px(14pt) and bold).
function relLum([r, g, b]) {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function parseColor(s) {
    const m = s.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3]), m[4] !== undefined ? parseFloat(m[4]) : 1];
}
function contrastRatio(rgb1, rgb2) {
    const l1 = relLum(rgb1), l2 = relLum(rgb2);
    const [lighter, darker] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (lighter + 0.05) / (darker + 0.05);
}
function effectiveBackground(el) {
    let node = el;
    while (node) {
        const bg = parseColor(getComputedStyle(node).backgroundColor);
        if (bg && bg[3] > 0) {
            if (bg[3] === 1) return bg.slice(0, 3);
            // Non-opaque background — approximate by compositing over white.
            return bg.slice(0, 3).map(c => c * bg[3] + 255 * (1 - bg[3]));
        }
        node = node.parentElement;
    }
    return [255, 255, 255];
}
function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0;
}

const violations = [];
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
        if (!node.textContent.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
    }
});
const seen = new Set();
let node;
while ((node = walker.nextNode())) {
    const el = node.parentElement;
    if (!el || seen.has(el) || !isVisible(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    const fg = parseColor(cs.color);
    if (!fg) continue;
    const bg = effectiveBackground(el);
    const ratio = contrastRatio(fg, bg);
    const px = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const isLarge = px >= 24 || (px >= 18.66 && weight >= 700);
    const threshold = isLarge ? 3.0 : 4.5;
    if (ratio < threshold) {
        violations.push({
            text: node.textContent.trim().slice(0, 50),
            tag: el.tagName,
            cls: el.className?.toString().slice(0, 80),
            color: cs.color,
            bg: `rgb(${bg.map(Math.round).join(',')})`,
            ratio: Math.round(ratio * 100) / 100,
            threshold,
            fontSize: px,
        });
    }
}
violations.sort((a, b) => a.ratio - b.ratio);
JSON.stringify(violations, null, 2);
