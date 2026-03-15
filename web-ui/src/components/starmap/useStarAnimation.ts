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

  // ── Scattered trees on the far ridge ────────────────────────────────
  // Far ridge contour sampler
  const farRidgeY = (fx: number): number => {
    const quad = (t: number, p0: number, p1: number, p2: number) =>
      p0 + (p1 - p0) * 2 * t * (1 - t) + (p2 - p0) * t * t;
    if (fx < 0.15) {
      const t = fx / 0.15;
      return quad(t, horizonY + 10, horizonY - 25, horizonY + 5);
    }
    if (fx < 0.30) {
      const t = (fx - 0.15) / 0.15;
      return quad(t, horizonY + 5, horizonY - 40, horizonY - 10);
    }
    if (fx < 0.44) {
      const t = (fx - 0.30) / 0.14;
      return quad(t, horizonY - 10, horizonY - 60, horizonY - 35);
    }
    if (fx < 0.55) {
      const t = (fx - 0.44) / 0.11;
      return quad(t, horizonY - 35, horizonY - 15, horizonY + 5);
    }
    if (fx < 0.72) {
      const t = (fx - 0.55) / 0.17;
      return quad(t, horizonY + 5, horizonY - 30, horizonY - 5);
    }
    if (fx < 0.88) {
      const t = (fx - 0.72) / 0.16;
      return quad(t, horizonY - 5, horizonY - 45, horizonY - 15);
    }
    const t = (fx - 0.88) / 0.12;
    return quad(t, horizonY - 15, horizonY + 5, horizonY + 10);
  };
  const farTrees = getFarRidgeTreeCache();
  for (let i = 0; i < farTrees.length; i++) {
    const t = farTrees[i];
    const tx = t.fx * w;
    const baseY = farRidgeY(t.fx) + 4 + t.yOffset;
    ctx.fillStyle = t.shade;
    // Simple small triangles — distant, so less detail
    ctx.beginPath();
    ctx.moveTo(tx, baseY - t.height);
    ctx.lineTo(tx - t.width, baseY);
    ctx.lineTo(tx + t.width, baseY);
    ctx.closePath();
    ctx.fill();
  }

  // ── Dense forest on far-right background mound ─────────────────────────
  const bgForestTrees = getBgMoundForestCache();
  for (let i = 0; i < bgForestTrees.length; i++) {
    const t = bgForestTrees[i];
    const tx = t.fx * w;
    const baseY = farRidgeY(t.fx) + 4 + t.yOffset;
    ctx.fillStyle = t.shade;
    // Two-layer conifer silhouette
    ctx.beginPath();
    ctx.moveTo(tx, baseY - t.height);
    ctx.lineTo(tx - t.width, baseY);
    ctx.lineTo(tx + t.width, baseY);
    ctx.closePath();
    ctx.fill();
    if (t.height > 4) {
      ctx.beginPath();
      ctx.moveTo(tx, baseY - t.height * 1.15);
      ctx.lineTo(tx - t.width * 0.6, baseY - t.height * 0.35);
      ctx.lineTo(tx + t.width * 0.6, baseY - t.height * 0.35);
      ctx.closePath();
      ctx.fill();
    }
  }

  // ── Near ridge ──────────────────────────────────────────────────────────
  const domeX = w * 0.40;
  const domeY = horizonY - 26;

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

  // ── Mountain road switchbacks (drawn before forests so trees occlude) ─
  {
    const jxn = w * 0.38;
    const jny = horizonY + 42;
    ctx.save();
    ctx.strokeStyle = "rgba(14,21,32,0.8)";
    ctx.lineWidth = 1.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(jxn, jny);
    ctx.bezierCurveTo(w * 0.45, horizonY + 20, w * 0.44, horizonY + 10, w * 0.38, horizonY + 4);
    ctx.bezierCurveTo(w * 0.35, horizonY, w * 0.37, horizonY - 10, w * 0.42, horizonY - 14);
    ctx.bezierCurveTo(w * 0.44, horizonY - 16, w * 0.43, horizonY - 22, domeX + 14, domeY + 14);
    ctx.stroke();
    ctx.restore();
  }

  // ── Scattered trees on the near ridge ────────────────────────────────
  // Sparse coverage everywhere except observatory (0.34-0.46) and dense forest (0.68-0.86)
  const nearScatteredTrees = getNearScatteredTreeCache();
  for (let i = 0; i < nearScatteredTrees.length; i++) {
    const t = nearScatteredTrees[i];
    const tx = t.fx * w;
    const baseY = ridgeY(t.fx) + 16;
    ctx.fillStyle = t.shade;
    // Small conifers — single triangle + optional second layer
    ctx.beginPath();
    ctx.moveTo(tx, baseY - t.height);
    ctx.lineTo(tx - t.width, baseY);
    ctx.lineTo(tx + t.width, baseY);
    ctx.closePath();
    ctx.fill();
    if (t.height > 6) {
      ctx.beginPath();
      ctx.moveTo(tx, baseY - t.height * 1.15);
      ctx.lineTo(tx - t.width * 0.65, baseY - t.height * 0.4);
      ctx.lineTo(tx + t.width * 0.65, baseY - t.height * 0.4);
      ctx.closePath();
      ctx.fill();
    }
  }

  // ── Dense forest on the right side of the near ridge ──────────────────
  // Conifers sitting on the ridge contour, partially obscuring far ridge
  const forestTrees = getForestTreeCache();
  for (let i = 0; i < forestTrees.length; i++) {
    const t = forestTrees[i];
    const tx = t.fx * w;
    const baseY = ridgeY(t.fx) + 14 + t.yOffset;

    // Tree trunk
    ctx.fillStyle = "#070b13";
    ctx.fillRect(tx - 0.5, baseY - t.trunk, 1, t.trunk);

    // Conifer shape — 2-3 layered triangles tapering upward
    ctx.fillStyle = t.shade;
    const treeTop = baseY - t.trunk - t.height;
    const layers = t.layers;
    for (let l = 0; l < layers; l++) {
      const lf = l / layers;
      const layerTop = baseY - t.trunk - t.height * (lf + 1 / layers);
      const layerBot = baseY - t.trunk - t.height * lf * 0.6;
      const layerW = t.width * (1 - lf * 0.35);
      ctx.beginPath();
      ctx.moveTo(tx, layerTop);
      ctx.lineTo(tx - layerW, layerBot);
      ctx.lineTo(tx + layerW, layerBot);
      ctx.closePath();
      ctx.fill();
    }
  }

  // ── Dense forest on the observatory hill ────────────────────────────
  // Covers both slopes, leaving a gap for dome+annex (fx ~0.38-0.44)
  const obsForestTrees = getObsHillForestCache();
  for (let i = 0; i < obsForestTrees.length; i++) {
    const t = obsForestTrees[i];
    const tx = t.fx * w;
    const baseY = ridgeY(t.fx) + 14 + t.yOffset;

    ctx.fillStyle = "#070b13";
    ctx.fillRect(tx - 0.5, baseY - t.trunk, 1, t.trunk);

    ctx.fillStyle = t.shade;
    const layers = t.layers;
    for (let l = 0; l < layers; l++) {
      const lf = l / layers;
      const layerTop = baseY - t.trunk - t.height * (lf + 1 / layers);
      const layerBot = baseY - t.trunk - t.height * lf * 0.6;
      const layerW = t.width * (1 - lf * 0.35);
      ctx.beginPath();
      ctx.moveTo(tx, layerTop);
      ctx.lineTo(tx - layerW, layerBot);
      ctx.lineTo(tx + layerW, layerBot);
      ctx.closePath();
      ctx.fill();
    }
  }

  // ── Valley floor with subtle gradient ─────────────────────────────────
  const valleyTop = horizonY + 45;
  const valleyGrad = ctx.createLinearGradient(0, valleyTop, 0, h);
  valleyGrad.addColorStop(0, "#080d16");
  valleyGrad.addColorStop(0.4, "#060a12");
  valleyGrad.addColorStop(1, "#040810");
  ctx.fillStyle = valleyGrad;
  ctx.fillRect(0, valleyTop, w, h - valleyTop);

  // ── Distant road (winding through the valley) ────────────────────────
  const roadY = horizonY + 75;
  ctx.save();
  ctx.strokeStyle = "#0e1520";
  ctx.lineWidth = 2.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(-10, roadY + 8);
  ctx.quadraticCurveTo(w * 0.08, roadY - 4, w * 0.18, roadY + 6);
  ctx.quadraticCurveTo(w * 0.28, roadY + 16, w * 0.38, roadY + 2);
  ctx.quadraticCurveTo(w * 0.48, roadY - 12, w * 0.58, roadY + 4);
  ctx.quadraticCurveTo(w * 0.68, roadY + 18, w * 0.78, roadY - 2);
  ctx.quadraticCurveTo(w * 0.90, roadY - 14, w + 10, roadY + 4);
  ctx.stroke();
  // Faint center line
  ctx.strokeStyle = "rgba(60,75,100,0.12)";
  ctx.lineWidth = 0.5;
  ctx.beginPath();
  ctx.moveTo(-10, roadY + 8);
  ctx.quadraticCurveTo(w * 0.08, roadY - 4, w * 0.18, roadY + 6);
  ctx.quadraticCurveTo(w * 0.28, roadY + 16, w * 0.38, roadY + 2);
  ctx.quadraticCurveTo(w * 0.48, roadY - 12, w * 0.58, roadY + 4);
  ctx.quadraticCurveTo(w * 0.68, roadY + 18, w * 0.78, roadY - 2);
  ctx.quadraticCurveTo(w * 0.90, roadY - 14, w + 10, roadY + 4);
  ctx.stroke();
  ctx.restore();

  // ── Valley approach road to observatory ──────────────────────────────
  // Just the valley portion — mountain switchbacks drawn earlier, under the forests
  const junctionX = w * 0.35;
  const junctionY = roadY + 7;
  ctx.save();
  ctx.strokeStyle = "#0e1520";
  ctx.lineWidth = 1.8;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(junctionX, junctionY);
  ctx.bezierCurveTo(w * 0.36, roadY - 15, w * 0.37, horizonY + 45, w * 0.38, horizonY + 42);
  ctx.stroke();
  ctx.restore();

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

  // ── Valley floor trees (scattered, bigger toward bottom for depth) ───
  const valleyTrees = getValleyTreeCache();
  for (let i = 0; i < valleyTrees.length; i++) {
    const t = valleyTrees[i];
    const tx = t.fx * w;
    const ty = valleyTop + t.fy * (h - valleyTop);
    // Depth scale: trees further down (closer) are bigger
    const depth = t.fy; // 0=top of valley, 1=bottom
    const scale = 0.6 + depth * 1.0;
    const tHeight = t.height * scale;
    const tWidth = t.width * scale;

    // Trunk
    ctx.fillStyle = "#050910";
    ctx.fillRect(tx - 0.4 * scale, ty - t.trunk * scale, 0.8 * scale, t.trunk * scale);

    // Conifer layers
    ctx.fillStyle = t.shade;
    const layers = t.layers;
    for (let l = 0; l < layers; l++) {
      const lf = l / layers;
      const layerTop = ty - t.trunk * scale - tHeight * (lf + 1 / layers);
      const layerBot = ty - t.trunk * scale - tHeight * lf * 0.5;
      const layerW = tWidth * (1 - lf * 0.3);
      ctx.beginPath();
      ctx.moveTo(tx, layerTop);
      ctx.lineTo(tx - layerW, layerBot);
      ctx.lineTo(tx + layerW, layerBot);
      ctx.closePath();
      ctx.fill();
    }
  }

  // ── Observatory annex building (attached to the right of the dome) ───
  const annexL = domeX + 10;
  const annexR = domeX + 22;
  const annexTop = domeY - 2;
  const annexBot = domeY + 6;
  // Main body
  ctx.fillStyle = "#0c1119";
  ctx.fillRect(annexL, annexTop, annexR - annexL, annexBot - annexTop);
  // Flat roof overhang
  ctx.fillStyle = "#0a0f17";
  ctx.fillRect(annexL - 1, annexTop - 1, annexR - annexL + 2, 2);
  // Tiny window — faint warm glow
  ctx.fillStyle = "rgba(200,170,100,0.08)";
  ctx.fillRect(annexL + 3, annexTop + 3, 2, 2);
  // Door frame
  ctx.fillStyle = "#080c14";
  ctx.fillRect(annexL + 8, annexTop + 4, 2, annexBot - annexTop - 4);

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
const DOME_OFFSET = 26;

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
  const slitY = h * DOME_HORIZON_F - DOME_OFFSET - 8;
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
  const baseAlpha = 0.02 + pulse * 0.02;

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

// ─── Radio telescope (per-frame, dish tracks active star) ────────────────────

function drawRadioTelescope(
  ctx: CanvasRenderingContext2D,
  starMap: Map<number, StarPosition>,
  pulsingStarIds: Set<number>,
  time: number,
  w: number, h: number,
) {
  const horizonY = h * DOME_HORIZON_F;
  const domeY = horizonY - 26; // must match drawLandscape
  const dishX = w * DOME_FX - 30;
  const dishBase = domeY + 6;
  const dishPivotY = dishBase - 18;
  const dishRadius = 14;

  // Compute dish tilt angle toward target star (or default upward)
  let angle = -Math.PI / 2; // default: pointing straight up
  if (pulsingStarIds.size > 0 && pulsingCache.length > 0) {
    const idx = Math.floor(time / 8000) % pulsingCache.length;
    const target = starMap.get(pulsingCache[idx]);
    if (target) {
      const tx = target.x * w;
      const ty = target.y * h;
      angle = Math.atan2(ty - dishPivotY, tx - dishX);
    }
  }

  ctx.save();

  // Support tower
  ctx.fillStyle = "#0a0f17";
  ctx.fillRect(dishX - 1.5, dishBase - 18, 3, 18);
  // Tower cross-braces
  ctx.strokeStyle = "#0a0f17";
  ctx.lineWidth = 0.7;
  ctx.beginPath();
  ctx.moveTo(dishX - 4, dishBase);
  ctx.lineTo(dishX + 4, dishBase);
  ctx.moveTo(dishX - 3, dishBase - 6);
  ctx.lineTo(dishX + 3, dishBase - 6);
  ctx.moveTo(dishX - 4, dishBase);
  ctx.lineTo(dishX, dishBase - 12);
  ctx.moveTo(dishX + 4, dishBase);
  ctx.lineTo(dishX, dishBase - 12);
  ctx.stroke();

  // Draw dish rotated around pivot point
  ctx.translate(dishX, dishPivotY);
  ctx.rotate(angle + Math.PI / 2); // +90° so 0 = pointing up

  // Parabolic dish
  ctx.fillStyle = "#0c1119";
  ctx.beginPath();
  ctx.moveTo(-dishRadius, 4);
  ctx.quadraticCurveTo(0, 10, dishRadius, 4);
  ctx.quadraticCurveTo(0, -2, -dishRadius, 4);
  ctx.closePath();
  ctx.fill();
  // Dish rim highlight
  ctx.strokeStyle = "rgba(60,80,110,0.12)";
  ctx.lineWidth = 0.8;
  ctx.beginPath();
  ctx.moveTo(-dishRadius, 4);
  ctx.quadraticCurveTo(0, -2, dishRadius, 4);
  ctx.stroke();
  // Feed arm struts
  ctx.strokeStyle = "#0a0f17";
  ctx.lineWidth = 0.8;
  ctx.beginPath();
  ctx.moveTo(-dishRadius + 2, 3);
  ctx.lineTo(0, -8);
  ctx.lineTo(dishRadius - 2, 3);
  ctx.stroke();
  // Receiver
  ctx.fillStyle = "#0e1420";
  ctx.beginPath();
  ctx.arc(0, -8, 1.5, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();

  // Pulsing red indicator light on top of tower (not rotated)
  const redPulse = 0.06 + 0.06 * Math.sin(time * 0.0015);
  ctx.fillStyle = `rgba(255,50,30,${redPulse})`;
  ctx.beginPath();
  ctx.arc(dishX, dishPivotY - 2, 1.5, 0, Math.PI * 2);
  ctx.fill();
  // Soft red glow around it
  ctx.fillStyle = `rgba(255,50,30,${redPulse * 0.3})`;
  ctx.beginPath();
  ctx.arc(dishX, dishPivotY - 2, 4, 0, Math.PI * 2);
  ctx.fill();
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

interface ForestTree {
  fx: number;
  yOffset: number; // additional vertical offset for depth rows
  height: number;
  width: number;
  trunk: number;
  layers: number;
  shade: string;
}

let forestTreeCache: ForestTree[] | null = null;

function getForestTreeCache(): ForestTree[] {
  if (!forestTreeCache) {
    forestTreeCache = [];
    // Multiple depth rows of trees, back to front
    // Rows further back are shorter/darker, rows in front taller/slightly lighter
    const rowCount = 8;
    let globalIdx = 0;
    for (let row = 0; row < rowCount; row++) {
      const rowF = row / rowCount; // 0=back, 1=front
      const yOff = -6 + row * 4; // back rows higher, front rows lower
      const heightScale = 0.5 + rowF * 0.6; // back trees shorter
      const shadeIdx = Math.min(3, Math.floor(row / 2));
      const shades = ["#050910", "#060a12", "#070b14", "#080c15"];
      const shade = shades[shadeIdx];
      // Slightly different x range per row for natural look
      // Front (lower) rows start further right so the left edge doesn't dip down the ridge
      const xStart = 0.68 + rowF * 0.06 + seededF(row * 7 + 800) * 0.02;
      const xEnd = 0.84 + seededF(row * 7 + 801) * 0.02;

      let fx = xStart;
      while (fx < xEnd) {
        const gap = 0.002 + seededF(globalIdx * 3 + 700) * 0.004;
        fx += gap;
        if (fx >= xEnd) break;
        // Jitter x slightly per row so trees don't align vertically
        const jitteredFx = fx + (seededF(globalIdx * 3 + 706) - 0.5) * 0.004;
        const height = (6 + seededF(globalIdx * 3 + 701) * 12) * heightScale;
        const width = (2 + seededF(globalIdx * 3 + 702) * 2.5) * (0.7 + heightScale * 0.3);
        const trunk = (1.5 + seededF(globalIdx * 3 + 703) * 2) * heightScale;
        const layers = 2 + Math.floor(seededF(globalIdx * 3 + 704) * 2);
        forestTreeCache.push({ fx: jitteredFx, yOffset: yOff, height, width, trunk, layers, shade });
        globalIdx++;
      }
    }
    // Sort back-to-front so front trees draw on top
    forestTreeCache.sort((a, b) => a.yOffset - b.yOffset);
  }
  return forestTreeCache;
}

let obsHillForestCache: ForestTree[] | null = null;

function getObsHillForestCache(): ForestTree[] {
  if (!obsHillForestCache) {
    obsHillForestCache = [];
    const rowCount = 12;
    let globalIdx = 0;
    for (let row = 0; row < rowCount; row++) {
      const rowF = row / rowCount;
      const yOff = -6 + row * 6;
      const heightScale = 0.5 + rowF * 0.6;
      const shadeIdx = Math.min(3, Math.floor(row / 2));
      const shades = ["#050910", "#060a12", "#070b14", "#080c15"];
      const shade = shades[shadeIdx];
      // Left boundary tracks the slope: top rows start near the peak, lower rows extend further left
      // This prevents trees from floating above the ridge on the left where it drops steeply
      const xStart = 0.38 - rowF * 0.50 + seededF(row * 7 + 2000) * 0.01;
      const xEnd = 0.70 + seededF(row * 7 + 2001) * 0.01;
      let fx = xStart;
      {
        while (fx < xEnd) {
          const gap = 0.0015 + seededF(globalIdx * 3 + 2100) * 0.003;
          fx += gap;
          if (fx >= xEnd) break;
          // Skip the dome/annex clearing
          if (fx > 0.3995 && fx < 0.4015) { globalIdx++; continue; }
          const jitteredFx = fx + (seededF(globalIdx * 3 + 2106) - 0.5) * 0.004;
          const height = (6 + seededF(globalIdx * 3 + 2101) * 12) * heightScale;
          const width = (2 + seededF(globalIdx * 3 + 2102) * 2.5) * (0.7 + heightScale * 0.3);
          const trunk = (1.5 + seededF(globalIdx * 3 + 2103) * 2) * heightScale;
          const layers = 2 + Math.floor(seededF(globalIdx * 3 + 2104) * 2);
          obsHillForestCache.push({ fx: jitteredFx, yOffset: yOff, height, width, trunk, layers, shade });
          globalIdx++;
        }
      }
    }
    obsHillForestCache.sort((a, b) => a.yOffset - b.yOffset);
  }
  return obsHillForestCache;
}

interface SimpleTree {
  fx: number;
  yOffset: number;
  height: number;
  width: number;
  shade: string;
}

let bgMoundForestCache: SimpleTree[] | null = null;

function getBgMoundForestCache(): SimpleTree[] {
  if (!bgMoundForestCache) {
    bgMoundForestCache = [];
    // Dense forest on the tall far-right background mound (~fx 0.73-0.90)
    // Fainter shades since it's distant
    const rowCount = 12;
    let globalIdx = 0;
    for (let row = 0; row < rowCount; row++) {
      const rowF = row / rowCount;
      const yOffBase = row * 6;
      const shades = [
        ["#0b1018", "#0c111a", "#0a0f17"],
        ["#0a0e16", "#0b1018", "#090e15"],
        ["#090d14", "#0a0f16", "#080c13"],
      ][Math.min(2, Math.floor(row / 2))];
      // All rows start well left of the peak; bottom rows extend even further
      const xStart = 0.72 - rowF * 0.12 + seededF(row * 7 + 3000) * 0.02;
      const xEnd = 0.88 + rowF * 0.12 + seededF(row * 7 + 3001) * 0.02;
      let fx = xStart;
      while (fx < xEnd) {
        const gap = 0.0015 + seededF(globalIdx * 3 + 3100) * 0.003;
        fx += gap;
        if (fx >= xEnd) break;
        // Heavy per-tree y jitter to break up ring patterns
        const yJitter = (seededF(globalIdx * 3 + 3105) - 0.5) * 10;
        const yOff = yOffBase + yJitter;
        const height = 2.5 + seededF(globalIdx * 3 + 3101) * 5;
        const width = 1 + seededF(globalIdx * 3 + 3102) * 1.2;
        const shade = shades[Math.floor(seededF(globalIdx * 3 + 3104) * shades.length)];
        bgMoundForestCache.push({ fx, yOffset: yOff, height, width, shade });
        globalIdx++;
      }
    }
    bgMoundForestCache.sort((a, b) => a.yOffset - b.yOffset);
  }
  return bgMoundForestCache;
}

let farRidgeTreeCache: SimpleTree[] | null = null;

function getFarRidgeTreeCache(): SimpleTree[] {
  if (!farRidgeTreeCache) {
    farRidgeTreeCache = [];
    // Dense forest covering ALL back mounds — 10 rows from crest downward
    const rowCount = 10;
    let globalIdx = 0;
    for (let row = 0; row < rowCount; row++) {
      const rowF = row / rowCount;
      const yOffBase = row * 6;
      const shades = [
        ["#0b1018", "#0c111a", "#0a0f17", "#0d1219"],
        ["#0a0e16", "#0b1018", "#090e15", "#0c1118"],
        ["#090d14", "#0a0f16", "#080c13", "#0b1017"],
      ][Math.min(2, Math.floor(row / 3))];
      let fx = -0.02;
      while (fx < 1.02) {
        const gap = 0.0007 + seededF(globalIdx * 3 + 900) * 0.0016;
        fx += gap;
        if (fx >= 1.02) break;
        // 10% skip for slight natural gaps
        if (seededF(globalIdx * 3 + 903) < 0.10) { globalIdx++; continue; }
        // Per-tree y jitter to avoid ring patterns
        const yJitter = (seededF(globalIdx * 3 + 905) - 0.5) * 8;
        const yOff = yOffBase + yJitter;
        const height = 3 + seededF(globalIdx * 3 + 901) * 6;
        const width = 1.2 + seededF(globalIdx * 3 + 902) * 1.5;
        const shade = shades[Math.floor(seededF(globalIdx * 3 + 904) * shades.length)];
        farRidgeTreeCache.push({ fx, yOffset: yOff, height, width, shade });
        globalIdx++;
      }
    }
    farRidgeTreeCache.sort((a, b) => a.yOffset - b.yOffset);
  }
  return farRidgeTreeCache;
}

let nearScatteredTreeCache: SimpleTree[] | null = null;

function getNearScatteredTreeCache(): SimpleTree[] {
  if (!nearScatteredTreeCache) {
    nearScatteredTreeCache = [];
    // Scattered across near ridge, avoiding observatory (0.34-0.46) and dense forest (0.68-0.86)
    const shades = ["#060a12", "#070b14", "#080c15", "#050910"];
    const zones = [
      { start: 0.04, end: 0.32 },
      { start: 0.48, end: 0.66 },
      { start: 0.88, end: 0.97 },
    ];
    let globalIdx = 0;
    for (const zone of zones) {
      let fx = zone.start;
      while (fx < zone.end) {
        const density = 0.4 + 0.6 * Math.abs(Math.sin(fx * 15 + 1.2));
        const gap = 0.003 + seededF(globalIdx * 3 + 1100) * 0.009 / density;
        fx += gap;
        if (fx >= zone.end) break;
        // Random skip for sparse natural feel
        if (seededF(globalIdx * 3 + 1103) < 0.1) { globalIdx++; continue; }
        const height = 4 + seededF(globalIdx * 3 + 1101) * 10;
        const width = 1.8 + seededF(globalIdx * 3 + 1102) * 2.2;
        const shade = shades[Math.floor(seededF(globalIdx * 3 + 1104) * shades.length)];
        nearScatteredTreeCache.push({ fx, yOffset: 0, height, width, shade });
        globalIdx++;
      }
    }
  }
  return nearScatteredTreeCache;
}

interface ValleyTree {
  fx: number;
  fy: number; // 0=top of valley, 1=bottom
  height: number;
  width: number;
  trunk: number;
  layers: number;
  shade: string;
}

let valleyTreeCache: ValleyTree[] | null = null;

function getValleyTreeCache(): ValleyTree[] {
  if (!valleyTreeCache) {
    valleyTreeCache = [];
    // Scatter trees across the valley floor, avoiding a band around the river
    // fy 0.0-0.25 = above river, 0.30-0.40 = river zone (skip), 0.45-0.85 = below river
    const bands = [
      { fyStart: 0.05, fyEnd: 0.25 },
      { fyStart: 0.45, fyEnd: 0.85 },
    ];
    const shades = ["#040810", "#050910", "#060a11", "#040710"];
    let globalIdx = 0;
    for (const band of bands) {
      // Multiple depth rows per band
      const rowCount = 4;
      for (let row = 0; row < rowCount; row++) {
        const fy = band.fyStart + (band.fyEnd - band.fyStart) * (row + seededF(globalIdx + 1300) * 0.5) / rowCount;
        let fx = 0.02;
        while (fx < 0.98) {
          const gap = 0.01 + seededF(globalIdx * 3 + 1200) * 0.035;
          fx += gap;
          if (fx >= 0.98) break;
          // Random skip for sparse natural feel
          if (seededF(globalIdx * 3 + 1205) < 0.35) { globalIdx++; continue; }
          const height = 4 + seededF(globalIdx * 3 + 1201) * 8;
          const width = 1.5 + seededF(globalIdx * 3 + 1202) * 2;
          const trunk = 1.5 + seededF(globalIdx * 3 + 1203) * 2;
          const layers = 2 + Math.floor(seededF(globalIdx * 3 + 1204) * 2);
          const shade = shades[Math.floor(seededF(globalIdx * 3 + 1206) * shades.length)];
          valleyTreeCache.push({ fx, fy, height, width, trunk, layers, shade });
          globalIdx++;
        }
      }
    }
    // Sort by fy so back trees draw first
    valleyTreeCache.sort((a, b) => a.fy - b.fy);
  }
  return valleyTreeCache;
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

  // Road curve sampler (matches the windier static road drawn in landscape)
  const roadAtX = (fx: number): number => {
    const quad = (t: number, p0: number, p1: number, p2: number) =>
      p0 + (p1 - p0) * 2 * t * (1 - t) + (p2 - p0) * t * t;
    if (fx < 0.18) {
      const t = fx / 0.18;
      return quad(t, roadY + 8, roadY - 4, roadY + 6);
    }
    if (fx < 0.38) {
      const t = (fx - 0.18) / 0.20;
      return quad(t, roadY + 6, roadY + 16, roadY + 2);
    }
    if (fx < 0.58) {
      const t = (fx - 0.38) / 0.20;
      return quad(t, roadY + 2, roadY - 12, roadY + 4);
    }
    if (fx < 0.78) {
      const t = (fx - 0.58) / 0.20;
      return quad(t, roadY + 4, roadY + 18, roadY - 2);
    }
    const t = (fx - 0.78) / 0.22;
    return quad(t, roadY - 2, roadY - 14, roadY + 4);
  };

  ctx.save();

  // One car at a time, alternating direction each pass
  const cycleDuration = 140;
  const crossingTime = 124;
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

// ─── Steamboat on the river ─────────────────────────────────────────────────

interface SteamParticle {
  startX: number;
  startY: number;
  vx: number;
  vy: number;
  startTime: number;
  lifetime: number;
  startSize: number;
}

let steamParticles: SteamParticle[] = [];
let lastSteamEmit = 0;

/** Sample the river's y-coordinate at fractional x (matching drawLandscape curves). */
function riverAtX(fx: number, riverY: number): number {
  const quad = (t: number, p0: number, p1: number, p2: number) =>
    p0 + (p1 - p0) * 2 * t * (1 - t) + (p2 - p0) * t * t;

  if (fx < 0.18) {
    const t = fx / 0.18;
    return quad(t, riverY + 15, riverY - 5, riverY + 10);
  }
  if (fx < 0.42) {
    const t = (fx - 0.18) / 0.24;
    return quad(t, riverY + 10, riverY + 30, riverY + 5);
  }
  if (fx < 0.65) {
    const t = (fx - 0.42) / 0.23;
    return quad(t, riverY + 5, riverY - 25, riverY - 10);
  }
  if (fx < 0.88) {
    const t = (fx - 0.65) / 0.23;
    return quad(t, riverY - 10, riverY + 15, riverY - 5);
  }
  const t = (fx - 0.88) / 0.12;
  return quad(t, riverY - 5, riverY - 15, riverY);
}

/** Distant steamboat traversing the river with warm light and steam. */
function drawSteamboat(
  ctx: CanvasRenderingContext2D,
  time: number,
  w: number, h: number,
) {
  const horizonY = h * 0.65;
  const valleyTop = horizonY + 45;
  const riverY = valleyTop + (h - valleyTop) * 0.35;

  const cycleDuration = 225;
  const crossingTime = 225;
  const tSec = time / 1000;
  const cycleIndex = Math.floor(tSec / cycleDuration);
  const cycleProgress = (tSec % cycleDuration) / crossingTime;

  // Update and draw surviving steam particles (even during gap)
  const drawSteam = () => {
    if (steamParticles.length === 0) return;
    ctx.save();
    ctx.fillStyle = "rgba(180,190,200,1)";
    for (let i = steamParticles.length - 1; i >= 0; i--) {
      const p = steamParticles[i];
      const elapsed = time - p.startTime;
      const life = elapsed / p.lifetime;
      if (life >= 1) {
        steamParticles.splice(i, 1);
        continue;
      }
      const x = p.startX + p.vx * elapsed;
      const y = p.startY + p.vy * elapsed;
      const size = p.startSize + elapsed * 0.0012;
      const pulse = 0.04 + 0.02 * Math.sin(time * 0.0008 + p.startTime * 0.3);
      ctx.globalAlpha = pulse * (1 - life);
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  };

  if (cycleProgress > 1) {
    drawSteam();
    return;
  }

  const dir = cycleIndex % 2 === 0 ? 1 : -1;
  const fx = dir > 0 ? cycleProgress : 1 - cycleProgress;
  const bx = fx * w;
  const by = riverAtX(fx, riverY);

  ctx.save();

  // Hull — tiny trapezoid silhouette
  ctx.fillStyle = "#1a1a2a";
  ctx.beginPath();
  ctx.moveTo(bx - 4, by);
  ctx.lineTo(bx - 3, by + 2);
  ctx.lineTo(bx + 3, by + 2);
  ctx.lineTo(bx + 4, by);
  ctx.closePath();
  ctx.fill();

  // Cabin
  ctx.fillStyle = "#151525";
  ctx.fillRect(bx - 1.5, by - 2, 3, 2);

  // Smokestack
  const stackX = bx + dir * 0.5;
  const stackTop = by - 5;
  ctx.fillStyle = "#101020";
  ctx.fillRect(stackX - 0.5, stackTop, 1, 3);

  // Warm cabin light
  ctx.globalAlpha = 0.15;
  const glow = ctx.createRadialGradient(bx, by - 1, 0, bx, by - 1, 5);
  glow.addColorStop(0, "rgba(255,200,100,0.3)");
  glow.addColorStop(1, "transparent");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(bx, by - 1, 5, 0, Math.PI * 2);
  ctx.fill();

  // Water reflection — faint warm shimmer below the hull
  ctx.globalAlpha = 0.06;
  ctx.fillStyle = "#ffe0a0";
  ctx.beginPath();
  ctx.ellipse(bx, by + 4, 3, 1.5, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore();

  // Emit steam from smokestack
  if (steamParticles.length < 30 && time - lastSteamEmit > 80) {
    lastSteamEmit = time;
    steamParticles.push({
      startX: stackX,
      startY: stackTop,
      vx: -0.003 + (Math.random() - 0.3) * 0.005,
      vy: -0.002 - Math.random() * 0.003,
      startTime: time,
      lifetime: 3500 + Math.random() * 3000,
      startSize: 0.4 + Math.random() * 0.5,
    });
  }

  drawSteam();
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

    // Steamboat on the river
    drawSteamboat(ctx, time, w, h);

    // Cached lookups
    const starMap = getStarMap(stars);
    updatePulsingCache(pulsingStarIds);

    // Observatory beam + radio telescope
    drawObservatoryBeam(ctx, starMap, pulsingStarIds, time, w, h);
    drawRadioTelescope(ctx, starMap, pulsingStarIds, time, w, h);

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
