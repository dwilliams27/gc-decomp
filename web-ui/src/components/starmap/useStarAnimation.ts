import { useCallback, useEffect, useRef } from "react";
import { useStarMapStore } from "../../stores/starMapStore";
import type { StarPosition, Supernova } from "../../api/types";

// ─── Colors ─────────────────────────────────────────────────────────────────

function starCoreColor(m: number): string {
  if (m >= 100) return "#fff8e0";
  if (m >= 70)  return "#dde8ff";
  if (m >= 30)  return "#ffe0c0";
  return "#301818";
}

function starGlowColor(m: number): [number, number, number] {
  if (m >= 100) return [255, 200, 50];
  if (m >= 70)  return [140, 170, 255];
  if (m >= 30)  return [255, 150, 60];
  return [60, 25, 25];
}

// ─── Landscape (drawn once to offscreen cache) ─────────────────────────────

function drawLandscape(ctx: CanvasRenderingContext2D, w: number, h: number) {
  const horizonY = h * 0.65;

  const skyGrad = ctx.createLinearGradient(0, 0, 0, horizonY);
  skyGrad.addColorStop(0, "#050810");
  skyGrad.addColorStop(0.4, "#0a1020");
  skyGrad.addColorStop(0.7, "#101828");
  skyGrad.addColorStop(1, "#182030");
  ctx.fillStyle = skyGrad;
  ctx.fillRect(0, 0, w, horizonY);

  const horizGlow = ctx.createLinearGradient(0, horizonY - 60, 0, horizonY + 10);
  horizGlow.addColorStop(0, "transparent");
  horizGlow.addColorStop(0.7, "rgba(40,55,80,0.15)");
  horizGlow.addColorStop(1, "rgba(30,40,60,0.08)");
  ctx.fillStyle = horizGlow;
  ctx.fillRect(0, horizonY - 60, w, 70);

  const dark1 = "#0a0f18";
  const dark2 = "#070c14";

  // Far ridge
  ctx.fillStyle = "#0e1520";
  ctx.beginPath();
  ctx.moveTo(0, h);
  ctx.lineTo(0, horizonY + 10);
  ctx.quadraticCurveTo(w * 0.08, horizonY - 25, w * 0.15, horizonY + 5);
  ctx.quadraticCurveTo(w * 0.22, horizonY - 40, w * 0.30, horizonY - 10);
  ctx.quadraticCurveTo(w * 0.38, horizonY - 60, w * 0.44, horizonY - 35);
  ctx.quadraticCurveTo(w * 0.50, horizonY - 15, w * 0.55, horizonY + 5);
  ctx.quadraticCurveTo(w * 0.65, horizonY - 30, w * 0.72, horizonY - 5);
  ctx.quadraticCurveTo(w * 0.80, horizonY - 45, w * 0.88, horizonY - 15);
  ctx.quadraticCurveTo(w * 0.95, horizonY + 5, w, horizonY + 10);
  ctx.lineTo(w, h);
  ctx.closePath();
  ctx.fill();

  // ── Distant road (in the valley, below the near ridge) ─────────────────
  const roadY = horizonY + 75;
  ctx.strokeStyle = "#090e18";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, roadY + 3);
  ctx.quadraticCurveTo(w * 0.15, roadY - 1, w * 0.30, roadY + 2);
  ctx.quadraticCurveTo(w * 0.50, roadY + 5, w * 0.70, roadY + 1);
  ctx.quadraticCurveTo(w * 0.85, roadY - 2, w, roadY + 2);
  ctx.stroke();

  // ── Near ridge ──────────────────────────────────────────────────────────
  const domeX = w * 0.40;
  const domeY = horizonY - 30;

  // Sample the ridge contour so trees can sit on it
  const ridgeY = (fx: number): number => {
    // Piecewise approximation of the near ridge curve
    if (fx < 0.12) return horizonY + 40 - 25 * (fx / 0.12);
    if (fx < 0.28) return horizonY + 15 + 20 * ((fx - 0.12) / 0.16) * Math.sin(((fx - 0.12) / 0.16) * Math.PI);
    if (fx < 0.34) return horizonY + 25 - 55 * ((fx - 0.28) / 0.06);
    if (fx < 0.40) return horizonY - 30; // observatory peak
    if (fx < 0.52) return horizonY - 30 + 50 * ((fx - 0.40) / 0.12);
    if (fx < 0.60) return horizonY + 20 + 20 * ((fx - 0.52) / 0.08);
    if (fx < 0.68) return horizonY + 40 - 15 * ((fx - 0.60) / 0.08);
    if (fx < 0.82) return horizonY + 25 - 20 * Math.sin(((fx - 0.68) / 0.14) * Math.PI) + 5;
    if (fx < 0.95) return horizonY + 30 + 15 * Math.sin(((fx - 0.82) / 0.13) * Math.PI);
    return horizonY + 35;
  };

  ctx.fillStyle = dark1;
  ctx.beginPath();
  ctx.moveTo(0, h);
  ctx.lineTo(0, horizonY + 40);
  ctx.quadraticCurveTo(w * 0.05, horizonY + 15, w * 0.12, horizonY + 35);
  ctx.quadraticCurveTo(w * 0.20, horizonY + 10, w * 0.28, horizonY + 25);
  ctx.quadraticCurveTo(w * 0.34, horizonY - 20, w * 0.40, horizonY - 30);
  ctx.lineTo(domeX - 12, domeY);
  ctx.arc(domeX, domeY, 12, Math.PI, 0);
  ctx.lineTo(domeX + 12, domeY);
  ctx.quadraticCurveTo(w * 0.46, horizonY - 15, w * 0.52, horizonY + 20);
  ctx.quadraticCurveTo(w * 0.60, horizonY + 40, w * 0.68, horizonY + 25);
  ctx.quadraticCurveTo(w * 0.75, horizonY + 5, w * 0.82, horizonY + 30);
  ctx.quadraticCurveTo(w * 0.90, horizonY + 45, w * 0.95, horizonY + 35);
  ctx.lineTo(w, horizonY + 40);
  ctx.lineTo(w, h);
  ctx.closePath();
  ctx.fill();

  // ── Valley floor with subtle gradient ─────────────────────────────────
  const valleyTop = horizonY + 45;
  const valleyGrad = ctx.createLinearGradient(0, valleyTop, 0, h);
  valleyGrad.addColorStop(0, "#080d16");
  valleyGrad.addColorStop(0.4, "#060a12");
  valleyGrad.addColorStop(1, "#040810");
  ctx.fillStyle = valleyGrad;
  ctx.fillRect(0, valleyTop, w, h - valleyTop);

  // ── River winding through valley ──────────────────────────────────────
  const riverY = valleyTop + (h - valleyTop) * 0.35;
  ctx.save();
  // River body — very dark, barely visible
  ctx.strokeStyle = "#070b14";
  ctx.lineWidth = 10;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(-10, riverY + 15);
  ctx.quadraticCurveTo(w * 0.08, riverY - 5, w * 0.18, riverY + 10);
  ctx.quadraticCurveTo(w * 0.30, riverY + 30, w * 0.42, riverY + 5);
  ctx.quadraticCurveTo(w * 0.55, riverY - 25, w * 0.65, riverY - 10);
  ctx.quadraticCurveTo(w * 0.78, riverY + 15, w * 0.88, riverY - 5);
  ctx.quadraticCurveTo(w * 0.95, riverY - 15, w + 10, riverY);
  ctx.stroke();
  // Faint highlight along center
  ctx.strokeStyle = "rgba(40,55,80,0.04)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(-10, riverY + 15);
  ctx.quadraticCurveTo(w * 0.08, riverY - 5, w * 0.18, riverY + 10);
  ctx.quadraticCurveTo(w * 0.30, riverY + 30, w * 0.42, riverY + 5);
  ctx.quadraticCurveTo(w * 0.55, riverY - 25, w * 0.65, riverY - 10);
  ctx.quadraticCurveTo(w * 0.78, riverY + 15, w * 0.88, riverY - 5);
  ctx.quadraticCurveTo(w * 0.95, riverY - 15, w + 10, riverY);
  ctx.stroke();
  // Subtle moon reflection spots
  ctx.fillStyle = "rgba(40,55,80,0.02)";
  for (let rx = 0.1; rx < 0.95; rx += 0.15) {
    const ry = riverY + Math.sin(rx * 18) * 12;
    ctx.beginPath();
    ctx.ellipse(rx * w, ry, 15 + seededF(rx * 100) * 10, 2, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  // ── Observatory slit ──────────────────────────────────────────────────
  ctx.fillStyle = "rgba(200,180,120,0.12)";
  ctx.fillRect(domeX - 1, domeY - 10, 2, 10);

  // Background stars (static layer — twinkle animated separately)
  if (!bgStarsCache) initBgStars();
  ctx.fillStyle = "#c8d0e0";
  ctx.globalAlpha = 0.15;
  for (const s of bgStarsCache!) {
    ctx.beginPath();
    ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

// ─── Observatory beam (per-frame, aimed at active star) ─────────────────────

const DOME_FX = 0.40;
const DOME_HORIZON_F = 0.65;
const DOME_OFFSET = 30;

function drawObservatoryBeam(
  ctx: CanvasRenderingContext2D,
  starMap: Map<number, StarPosition>,
  pulsingStarIds: Set<number>,
  time: number,
  w: number, h: number,
) {
  if (pulsingStarIds.size === 0) return;

  const pulsingArr = pulsingCache;
  if (pulsingArr.length === 0) return;
  const idx = Math.floor(time / 8000) % pulsingArr.length;
  const targetStar = starMap.get(pulsingArr[idx]);
  if (!targetStar) return;

  const domeX = w * DOME_FX;
  const slitY = h * DOME_HORIZON_F - DOME_OFFSET - 10;
  const tx = targetStar.x * w;
  const ty = targetStar.y * h;

  const dx = tx - domeX;
  const dy = ty - slitY;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 1) return;

  const nx = -dy / dist;
  const ny = dx / dist;
  const nearW = 1.5;
  const farW = 12 + dist * 0.03;

  const pulse = 0.5 + 0.5 * Math.sin(time * 0.002);
  const baseAlpha = 0.04 + pulse * 0.03;

  ctx.save();

  // Main beam — single gradient, no core pass needed at this alpha
  const grad = ctx.createLinearGradient(domeX, slitY, tx, ty);
  grad.addColorStop(0, `rgba(200,180,120,${baseAlpha * 4})`);
  grad.addColorStop(0.4, `rgba(180,170,130,${baseAlpha * 1.5})`);
  grad.addColorStop(1, "transparent");
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.moveTo(domeX + nx * nearW, slitY + ny * nearW);
  ctx.lineTo(domeX - nx * nearW, slitY - ny * nearW);
  ctx.lineTo(tx - nx * farW, ty - ny * farW);
  ctx.lineTo(tx + nx * farW, ty + ny * farW);
  ctx.closePath();
  ctx.fill();

  // Spot on target
  ctx.globalAlpha = baseAlpha * 2;
  ctx.fillStyle = "#dcd08c";
  ctx.beginPath();
  ctx.arc(tx, ty, 15 + pulse * 8, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();
}

// ─── Stars ──────────────────────────────────────────────────────────────────

/** Fast path: dim stars get a single filled dot, no gradients. */
function drawStarSimple(
  ctx: CanvasRenderingContext2D,
  sx: number, sy: number,
  r: number, alpha: number,
  core: string,
) {
  ctx.globalAlpha = alpha;
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(sx, sy, Math.max(r * 0.6, 0.8), 0, Math.PI * 2);
  ctx.fill();
}

/** Full path: prominent/pulsing stars get gradient glow. */
function drawStarFull(
  ctx: CanvasRenderingContext2D,
  sx: number, sy: number,
  r: number, alpha: number,
  gr: number, gg: number, gb: number,
  core: string, pulsing: boolean,
) {
  // Outer halo
  const haloR = pulsing ? r * 10 + 14 : r * 4;
  ctx.globalAlpha = pulsing ? alpha * 0.3 : alpha * 0.12;
  const halo = ctx.createRadialGradient(sx, sy, 0, sx, sy, haloR);
  halo.addColorStop(0, `rgba(${gr},${gg},${gb},0.4)`);
  halo.addColorStop(0.3, `rgba(${gr},${gg},${gb},0.1)`);
  halo.addColorStop(1, "transparent");
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(sx, sy, haloR, 0, Math.PI * 2);
  ctx.fill();

  // Inner glow
  const glowR = r * 2.5;
  ctx.globalAlpha = pulsing ? alpha * 0.6 : alpha * 0.25;
  const glow = ctx.createRadialGradient(sx, sy, 0, sx, sy, glowR);
  glow.addColorStop(0, `rgba(${gr},${gg},${gb},0.8)`);
  glow.addColorStop(0.5, `rgba(${gr},${gg},${gb},0.2)`);
  glow.addColorStop(1, "transparent");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(sx, sy, glowR, 0, Math.PI * 2);
  ctx.fill();

  // Core disc
  ctx.globalAlpha = pulsing ? alpha * 0.9 : alpha * 0.5;
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(sx, sy, r * 0.8, 0, Math.PI * 2);
  ctx.fill();

  // Hot center
  ctx.globalAlpha = pulsing ? alpha : alpha * 0.4;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(sx, sy, r * 0.15, 0, Math.PI * 2);
  ctx.fill();

  // Diffraction spikes
  if (!pulsing && r >= 2.5) {
    ctx.globalAlpha = alpha * 0.15;
    ctx.strokeStyle = core;
    ctx.lineWidth = 0.5;
    const spikeLen = r * 3;
    ctx.beginPath();
    ctx.moveTo(sx - spikeLen, sy);
    ctx.lineTo(sx + spikeLen, sy);
    ctx.moveTo(sx, sy - spikeLen);
    ctx.lineTo(sx, sy + spikeLen);
    ctx.stroke();
  }
}

function drawAllStars(
  ctx: CanvasRenderingContext2D,
  stars: StarPosition[],
  pulsingStarIds: Set<number>,
  time: number,
  w: number, h: number,
) {
  ctx.save();

  // Non-pulsing stars: batch as tiny dots identical to background decoration stars
  ctx.fillStyle = "#c8d0e0";
  for (let i = 0; i < stars.length; i++) {
    const star = stars[i];
    if (pulsingStarIds.has(star.id)) continue;
    const sx = star.x * w;
    const sy = star.y * h;
    const phase = star.id * 2.31 + star.x * 7.1 + star.y * 11.3;
    const a = 0.08 + 0.05 * Math.sin(time * 0.0003 + phase);
    const horizonFade = Math.min(1, (0.62 - star.y) / 0.15);
    ctx.globalAlpha = a * Math.max(0.3, horizonFade);
    ctx.beginPath();
    ctx.arc(sx, sy, 0.4 + star.radius * 0.15, 0, Math.PI * 2);
    ctx.fill();
  }

  // Pulsing stars: full glow treatment
  for (let i = 0; i < stars.length; i++) {
    const star = stars[i];
    if (!pulsingStarIds.has(star.id)) continue;
    const sx = star.x * w;
    const sy = star.y * h;
    const pulseGlow = 0.5 + 0.5 * Math.sin(time * 0.003 + star.id);
    const alpha = 0.6 + pulseGlow * 0.4;
    drawStarFull(ctx, sx, sy, star.radius, alpha, 180, 220, 255, "#e0f0ff", true);
  }

  ctx.restore();
}

function drawEdges(
  ctx: CanvasRenderingContext2D,
  starMap: Map<number, StarPosition>,
  edges: { source: number; target: number }[],
  w: number, h: number,
) {
  ctx.save();
  ctx.strokeStyle = "rgba(80,120,180,0.06)";
  ctx.lineWidth = 0.5;
  ctx.beginPath();
  for (let i = 0; i < edges.length; i++) {
    const a = starMap.get(edges[i].source);
    const b = starMap.get(edges[i].target);
    if (!a || !b) continue;
    ctx.moveTo(a.x * w, a.y * h);
    ctx.lineTo(b.x * w, b.y * h);
  }
  ctx.stroke();
  ctx.restore();
}

function drawSupernovae(
  ctx: CanvasRenderingContext2D,
  supernovae: Supernova[],
  starMap: Map<number, StarPosition>,
  time: number,
  w: number, h: number,
) {
  for (let i = 0; i < supernovae.length; i++) {
    const sn = supernovae[i];
    const elapsed = time - sn.startTime;
    if (elapsed > sn.duration) continue;

    const star = starMap.get(sn.starId);
    if (!star) continue;
    const sx = star.x * w;
    const sy = star.y * h;
    const progress = elapsed / sn.duration;
    const fade = 1 - progress;

    // Flash (first 30%)
    if (progress < 0.3) {
      const flashA = 1 - progress / 0.3;
      const flashR = 4 + progress * 80;
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, flashR);
      grad.addColorStop(0, `rgba(255,255,255,${flashA * 0.8})`);
      grad.addColorStop(0.3, `rgba(255,230,150,${flashA * 0.4})`);
      grad.addColorStop(1, "transparent");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, flashR, 0, Math.PI * 2);
      ctx.fill();
    }

    // Rings
    const ringR = progress * 70;
    ctx.strokeStyle = `rgba(255,215,0,${fade * 0.4})`;
    ctx.lineWidth = Math.max(0.5, 2 * (1 - progress));
    ctx.beginPath();
    ctx.arc(sx, sy, ringR, 0, Math.PI * 2);
    ctx.stroke();

    ctx.strokeStyle = `rgba(200,180,255,${fade * 0.2})`;
    ctx.lineWidth = Math.max(0.5, 1.5 * (1 - progress));
    ctx.beginPath();
    ctx.arc(sx, sy, ringR * 0.7, 0, Math.PI * 2);
    ctx.stroke();
  }
}

// ─── Pre-computed caches ────────────────────────────────────────────────────

let treeCache: { x: number; h: number }[] | null = null;

function getTreeCache(): { x: number; h: number }[] {
  if (!treeCache) {
    treeCache = [];
    let tx = 0;
    let i = 0;
    while (tx < 1) {
      const gap = (6 + seededF(i * 2 + 100) * 4) / 1920;
      tx += gap;
      if (tx >= 1) break;
      const h = (4 + seededF(i * 2 + 101) * 8) / 1080;
      treeCache.push({ x: tx, h });
      i++;
    }
  }
  return treeCache;
}

let forestCache: { x: number; h: number }[] | null = null;

function getForestCache(): { x: number; h: number }[] {
  if (!forestCache) {
    forestCache = [];
    for (let i = 0; i < 120; i++) {
      forestCache.push({
        x: seededF(i * 2 + 500),
        h: seededF(i * 2 + 501),
      });
    }
  }
  return forestCache;
}

let bgStarsCache: { x: number; y: number; r: number; phase: number }[] | null = null;

function initBgStars() {
  bgStarsCache = [];
  for (let i = 0; i < 200; i++) {
    bgStarsCache.push({
      x: seededF(i * 3 + 0),
      y: seededF(i * 3 + 1) * 0.58,
      r: 0.3 + seededF(i * 3 + 2) * 0.8,
      phase: seededF(i * 5) * Math.PI * 2,
    });
  }
}

/** Lightweight per-frame twinkle — batch by alpha bucket to minimize state changes. */
function drawBackgroundStarsTwinkle(
  ctx: CanvasRenderingContext2D,
  time: number,
  w: number, h: number,
) {
  if (!bgStarsCache) return;
  ctx.save();
  ctx.fillStyle = "#c8d0e0";
  // Draw all in a single path per alpha-bucket (5 buckets)
  const buckets: number[][] = [[], [], [], [], []];
  for (let i = 0; i < bgStarsCache.length; i++) {
    const s = bgStarsCache[i];
    const a = 0.08 + 0.06 * Math.sin(time * 0.0003 + s.phase);
    const bucket = Math.min(4, (a * 30) | 0); // 0..4
    buckets[bucket].push(i);
  }
  const alphas = [0.05, 0.1, 0.15, 0.2, 0.25];
  for (let b = 0; b < 5; b++) {
    if (buckets[b].length === 0) continue;
    ctx.globalAlpha = alphas[b];
    ctx.beginPath();
    for (const idx of buckets[b]) {
      const s = bgStarsCache[idx];
      const sx = s.x * w;
      const sy = s.y * h;
      ctx.moveTo(sx + s.r, sy);
      ctx.arc(sx, sy, s.r, 0, Math.PI * 2);
    }
    ctx.fill();
  }
  ctx.restore();
}

/** Distant cars with headlights driving along the road. */
function drawCars(
  ctx: CanvasRenderingContext2D,
  time: number,
  w: number, h: number,
) {
  const horizonY = h * 0.65;
  const roadY = horizonY + 75;

  // Road curve sampler (matches the static road drawn in landscape)
  const roadAtX = (fx: number): number => {
    if (fx < 0.30) {
      const t = fx / 0.30;
      return roadY + 3 + (roadY - 1 - (roadY + 3)) * 2 * t * (1 - t) + ((roadY + 2) - (roadY + 3)) * t * t;
    }
    if (fx < 0.70) {
      const t = (fx - 0.30) / 0.40;
      return roadY + 2 + (roadY + 6 - (roadY + 2)) * 2 * t * (1 - t) + ((roadY + 1) - (roadY + 2)) * t * t;
    }
    const t = (fx - 0.70) / 0.30;
    return roadY + 1 + (roadY - 3 - (roadY + 1)) * 2 * t * (1 - t) + ((roadY + 2) - (roadY + 1)) * t * t;
  };

  ctx.save();

  // One car at a time, alternating direction each pass
  // Each pass: car crosses screen in ~62s, then ~10s gap before next
  const cycleDuration = 72; // seconds per full cycle (crossing + gap)
  const crossingTime = 62;
  const tSec = time / 1000;
  const cycleIndex = Math.floor(tSec / cycleDuration);
  const cycleProgress = (tSec % cycleDuration) / crossingTime;

  // Only draw if within the crossing portion (not the gap)
  if (cycleProgress <= 1) {
    const dir = cycleIndex % 2 === 0 ? 1 : -1;
    // Vary speed slightly per cycle
    const fx = dir > 0 ? cycleProgress : 1 - cycleProgress;

    const cx = fx * w;
    const cy = roadAtX(fx);

    // Headlights — two tiny warm dots, very small (distant)
    const spread = 2; // distance between headlights
    const hlDir = dir;

    // Headlight glow
    ctx.globalAlpha = 0.3;
    ctx.fillStyle = "#ffe8a0";
    ctx.beginPath();
    ctx.arc(cx + hlDir * spread, cy, 1.2, 0, Math.PI * 2);
    ctx.arc(cx - hlDir * spread, cy, 1.2, 0, Math.PI * 2);
    ctx.fill();

    // Tiny forward light spill
    ctx.globalAlpha = 0.06;
    const spillLen = 20 * hlDir;
    const grad = ctx.createRadialGradient(cx + spillLen * 0.5, cy, 0, cx + spillLen * 0.5, cy, Math.abs(spillLen));
    grad.addColorStop(0, "rgba(255,230,160,0.15)");
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx + spillLen * 0.5, cy, Math.abs(spillLen), 0, Math.PI * 2);
    ctx.fill();

    // Faint red taillight
    ctx.globalAlpha = 0.15;
    ctx.fillStyle = "#ff4030";
    ctx.beginPath();
    ctx.arc(cx - hlDir * 3, cy, 0.6, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

function seededF(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

// ─── Cached lookup structures (rebuilt when stars change) ───────────────────

let cachedStarMap: Map<number, StarPosition> | null = null;
let cachedStarCount = 0;
let pulsingCache: number[] = [];
let lastPulsingSize = -1;

function getStarMap(stars: StarPosition[]): Map<number, StarPosition> {
  if (!cachedStarMap || stars.length !== cachedStarCount) {
    cachedStarMap = new Map(stars.map((s) => [s.id, s]));
    cachedStarCount = stars.length;
  }
  return cachedStarMap;
}

function updatePulsingCache(pulsingStarIds: Set<number>) {
  if (pulsingStarIds.size !== lastPulsingSize) {
    pulsingCache = Array.from(pulsingStarIds);
    lastPulsingSize = pulsingStarIds.size;
  }
}

// ─── Animation loop ─────────────────────────────────────────────────────────

export function useStarAnimation() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const landscapeRef = useRef<{ canvas: OffscreenCanvas; w: number; h: number } | null>(null);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const state = useStarMapStore.getState();
    const { stars, edges, supernovae, pulsingStarIds } = state;
    const time = Date.now();

    const w = window.innerWidth;
    const h = window.innerHeight;
    const dpr = window.devicePixelRatio || 1;

    // Resize only when dimensions change
    if (sizeRef.current.w !== w || sizeRef.current.h !== h) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      sizeRef.current = { w, h };
      landscapeRef.current = null;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Cached landscape blit
    if (!landscapeRef.current || landscapeRef.current.w !== w || landscapeRef.current.h !== h) {
      const offscreen = new OffscreenCanvas(w * dpr, h * dpr);
      const offCtx = offscreen.getContext("2d")!;
      offCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawLandscape(offCtx as unknown as CanvasRenderingContext2D, w, h);
      landscapeRef.current = { canvas: offscreen, w, h };
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(landscapeRef.current.canvas, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Animated twinkle (batched)
    drawBackgroundStarsTwinkle(ctx, time, w, h);

    // Distant cars on the road
    drawCars(ctx, time, w, h);

    // Cached lookups
    const starMap = getStarMap(stars);
    updatePulsingCache(pulsingStarIds);

    // Observatory beam
    drawObservatoryBeam(ctx, starMap, pulsingStarIds, time, w, h);

    // Stars (dim ones get fast path)
    drawAllStars(ctx, stars, pulsingStarIds, time, w, h);

    // Supernovae
    drawSupernovae(ctx, supernovae, starMap, time, w, h);
    const expired = supernovae.filter((sn) => time - sn.startTime > sn.duration);
    for (const sn of expired) {
      useStarMapStore.getState().removeSupernova(sn.starId);
    }

    animRef.current = requestAnimationFrame(render);
  }, []);

  useEffect(() => {
    animRef.current = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animRef.current);
  }, [render]);

  return canvasRef;
}
