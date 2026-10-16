"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

/**
 * HYP-85: a small, playable Pac-Man-style maze for the 404 page.
 *
 * Written from scratch (no vendored game code). Everything lives in refs and is
 * drawn to a <canvas> from a single requestAnimationFrame loop; React state is
 * only used for the bits the DOM overlay needs (phase, score, lives).
 *
 * Input etiquette: nothing listens for -- or swallows -- arrow keys, WASD, or
 * touch gestures until the player presses Play. Pausing (Esc / P, tab hidden,
 * window blur) hands the keys back to the page.
 */

// '#' wall, '.' dot, 'o' power pellet, 'P' player start, 'G' ghost start.
const MAZE = [
  "###################",
  "#o.......#.......o#",
  "#.##.###.#.###.##.#",
  "#.......G.G.......#",
  "#.##.#.#####.#.##.#",
  "#....#...#...#....#",
  "####.###.#.###.####",
  "#.................#",
  "#.##.#.#####.#.##.#",
  "#....#...P...#....#",
  "#.##.###.#.###.##.#",
  "#o.......#.......o#",
  "###################"
];
const COLS = MAZE[0].length;
const ROWS = MAZE.length;
const MAX_TILE = 26;

const PAC_SPEED = 6.5; // tiles / second
const GHOST_SPEED = 5.2;
const FRIGHT_SPEED = 3.2;
const FRIGHT_MS = 6000;
const READY_MS = 1200;
const START_LIVES = 3;

type Dir = { x: number; y: number };
const NONE: Dir = { x: 0, y: 0 };
const UP: Dir = { x: 0, y: -1 };
const DOWN: Dir = { x: 0, y: 1 };
const LEFT: Dir = { x: -1, y: 0 };
const RIGHT: Dir = { x: 1, y: 0 };
const DIRS = [UP, LEFT, DOWN, RIGHT];

const KEY_DIRS: Record<string, Dir> = {
  ArrowUp: UP,
  ArrowDown: DOWN,
  ArrowLeft: LEFT,
  ArrowRight: RIGHT,
  w: UP,
  W: UP,
  s: DOWN,
  S: DOWN,
  a: LEFT,
  A: LEFT,
  d: RIGHT,
  D: RIGHT
};

type Phase = "idle" | "ready" | "playing" | "paused" | "won" | "lost";

type Actor = {
  // Tile we're leaving, and progress (0..1) toward tile + dir.
  tx: number;
  ty: number;
  t: number;
  dir: Dir;
};
type Pac = Actor & { want: Dir; facing: Dir };
type Ghost = Actor & {
  homeX: number;
  homeY: number;
  chase: number; // 0..1 likelihood of steering toward the player
  color: "destructive" | "info";
  eaten: boolean;
};

type Palette = {
  bg: string;
  wall: string;
  dot: string;
  pac: string;
  destructive: string;
  info: string;
  fright: string;
  eye: string;
};

function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => {
    const raw = css.getPropertyValue(name).trim();
    return raw ? `hsl(${raw})` : fallback;
  };
  return {
    bg: v("--card", "#111113"),
    wall: v("--border", "#2A2A2F"),
    dot: v("--muted-foreground", "#A7A7AF"),
    pac: v("--warning", "#E9C46A"),
    destructive: v("--destructive", "#E8928A"),
    info: v("--info", "#8DB8E8"),
    fright: v("--stone-gray", "#686872"),
    eye: v("--foreground", "#F7F6F4")
  };
}

const isWall = (x: number, y: number) =>
  x < 0 || y < 0 || x >= COLS || y >= ROWS || MAZE[y][x] === "#";
const open = (x: number, y: number, d: Dir) =>
  (d.x !== 0 || d.y !== 0) && !isWall(x + d.x, y + d.y);
const same = (a: Dir, b: Dir) => a.x === b.x && a.y === b.y;
const opposite = (a: Dir, b: Dir) => a.x === -b.x && a.y === -b.y && (a.x !== 0 || a.y !== 0);
const pos = (a: Actor) => ({ x: a.tx + a.dir.x * a.t, y: a.ty + a.dir.y * a.t });

function freshDots(): Uint8Array {
  // 0 none, 1 dot, 2 pellet
  const dots = new Uint8Array(COLS * ROWS);
  MAZE.forEach((row, y) =>
    [...row].forEach((c, x) => {
      if (c === ".") dots[y * COLS + x] = 1;
      if (c === "o") dots[y * COLS + x] = 2;
    })
  );
  return dots;
}

function find(ch: string): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  MAZE.forEach((row, y) => [...row].forEach((c, x) => c === ch && out.push({ x, y })));
  return out;
}

function makeActors(): { pac: Pac; ghosts: Ghost[] } {
  const p = find("P")[0];
  const g = find("G");
  return {
    pac: { tx: p.x, ty: p.y, t: 0, dir: NONE, want: NONE, facing: LEFT },
    ghosts: g.map((s, i) => ({
      tx: s.x,
      ty: s.y,
      t: 0,
      dir: i === 0 ? LEFT : RIGHT,
      homeX: s.x,
      homeY: s.y,
      chase: i === 0 ? 0.8 : 0.35,
      color: i === 0 ? "destructive" : "info",
      eaten: false
    }))
  };
}

export function NotFoundGame() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [score, setScore] = useState(0);
  const [lives, setLives] = useState(START_LIVES);
  const [reducedMotion, setReducedMotion] = useState(false);

  // Mutable game state, read by the RAF loop.
  const g = useRef({
    phase: "idle" as Phase,
    dots: freshDots(),
    remaining: 0,
    score: 0,
    lives: START_LIVES,
    frightUntil: 0,
    readyUntil: 0,
    clock: 0, // game-time ms (stops while paused)
    ...makeActors(),
    tile: 20,
    palette: null as Palette | null,
    reduced: false
  });

  const setGamePhase = useCallback((p: Phase) => {
    g.current.phase = p;
    setPhase(p);
  }, []);

  const resetLevel = useCallback(() => {
    const s = g.current;
    s.dots = freshDots();
    s.remaining = s.dots.reduce((n, d) => n + (d ? 1 : 0), 0);
    s.score = 0;
    s.lives = START_LIVES;
    s.frightUntil = 0;
    Object.assign(s, makeActors());
    setScore(0);
    setLives(START_LIVES);
  }, []);

  const start = useCallback(() => {
    const s = g.current;
    if (s.phase === "idle" || s.phase === "won" || s.phase === "lost") resetLevel();
    s.readyUntil = s.clock + (s.phase === "paused" ? 400 : READY_MS);
    setGamePhase("ready");
    canvasRef.current?.focus({ preventScroll: true });
  }, [resetLevel, setGamePhase]);

  // ---- one-time setup: palette, sizing, motion preference, render loop ----
  useEffect(() => {
    const s = g.current;
    s.remaining = s.dots.reduce((n, d) => n + (d ? 1 : 0), 0);
    s.palette = readPalette();

    // Theme flips toggle a class on <html>; repaint with the new tokens.
    const themeObs = new MutationObserver(() => {
      s.palette = readPalette();
    });
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });

    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onMq = () => {
      s.reduced = mq.matches;
      setReducedMotion(mq.matches);
    };
    onMq();
    mq.addEventListener("change", onMq);

    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const size = () => {
      const w = wrapRef.current?.clientWidth ?? COLS * 20;
      const tile = Math.max(10, Math.min(MAX_TILE, Math.floor(w / COLS)));
      const dpr = window.devicePixelRatio || 1;
      s.tile = tile;
      canvas.width = COLS * tile * dpr;
      canvas.height = ROWS * tile * dpr;
      canvas.style.width = `${COLS * tile}px`;
      canvas.style.height = `${ROWS * tile}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    const ro = new ResizeObserver(size);
    if (wrapRef.current) ro.observe(wrapRef.current);

    let raf = 0;
    let last = performance.now();
    let wall = 0; // real time for idle chomp animation

    const loseLife = () => {
      s.lives -= 1;
      setLives(s.lives);
      if (s.lives <= 0) {
        setGamePhase("lost");
        return;
      }
      const fresh = makeActors();
      s.pac = fresh.pac;
      s.ghosts = fresh.ghosts;
      s.frightUntil = 0;
      s.readyUntil = s.clock + READY_MS;
      setGamePhase("ready");
    };

    const addScore = (n: number) => {
      s.score += n;
      setScore(s.score);
    };

    const stepPac = (dt: number) => {
      const p = s.pac;
      // Instant reversal mid-corridor feels right; everything else turns at tile centers.
      if (p.t > 0 && opposite(p.want, p.dir)) {
        p.tx += p.dir.x;
        p.ty += p.dir.y;
        p.t = 1 - p.t;
        p.dir = p.want;
        p.facing = p.dir;
      }
      if (p.t === 0) {
        if (open(p.tx, p.ty, p.want)) p.dir = p.want;
        else if (!open(p.tx, p.ty, p.dir)) p.dir = NONE;
        if (p.dir !== NONE) p.facing = p.dir;
      }
      if (same(p.dir, NONE)) return;
      p.t += PAC_SPEED * dt;
      while (p.t >= 1) {
        p.tx += p.dir.x;
        p.ty += p.dir.y;
        p.t -= 1;
        const i = p.ty * COLS + p.tx;
        const d = s.dots[i];
        if (d) {
          s.dots[i] = 0;
          s.remaining -= 1;
          addScore(d === 2 ? 50 : 10);
          if (d === 2) {
            s.frightUntil = s.clock + FRIGHT_MS;
            s.ghosts.forEach((gh) => (gh.eaten = false));
          }
        }
        // Re-evaluate at the new tile center.
        if (open(p.tx, p.ty, p.want)) p.dir = p.want;
        else if (!open(p.tx, p.ty, p.dir)) {
          p.dir = NONE;
          p.t = 0;
          break;
        }
        p.facing = p.dir;
      }
    };

    const chooseGhostDir = (gh: Ghost, frightened: boolean) => {
      let options = DIRS.filter((d) => open(gh.tx, gh.ty, d) && !opposite(d, gh.dir));
      if (options.length === 0) options = DIRS.filter((d) => open(gh.tx, gh.ty, d));
      if (options.length === 0) return NONE;
      const target = { x: s.pac.tx, y: s.pac.ty };
      if (Math.random() < gh.chase) {
        const dist = (d: Dir) => (gh.tx + d.x - target.x) ** 2 + (gh.ty + d.y - target.y) ** 2;
        // Chase: minimize distance. Frightened: maximize it (run away).
        options.sort((a, b) => (frightened ? dist(b) - dist(a) : dist(a) - dist(b)));
        return options[0];
      }
      return options[Math.floor(Math.random() * options.length)];
    };

    const stepGhosts = (dt: number) => {
      const frightened = s.clock < s.frightUntil;
      for (const gh of s.ghosts) {
        const speed = frightened && !gh.eaten ? FRIGHT_SPEED : GHOST_SPEED;
        // Sitting exactly on a tile center (round start / respawn): pick a way out.
        if (gh.t === 0) gh.dir = chooseGhostDir(gh, frightened && !gh.eaten);
        gh.t += speed * dt;
        while (gh.t >= 1) {
          gh.tx += gh.dir.x;
          gh.ty += gh.dir.y;
          gh.t -= 1;
          gh.dir = chooseGhostDir(gh, frightened && !gh.eaten);
        }
      }
    };

    const checkCollisions = () => {
      const pp = pos(s.pac);
      const frightened = s.clock < s.frightUntil;
      for (const gh of s.ghosts) {
        const gp = pos(gh);
        if (Math.abs(gp.x - pp.x) + Math.abs(gp.y - pp.y) < 0.7) {
          if (frightened && !gh.eaten) {
            addScore(200);
            gh.eaten = true;
            gh.tx = gh.homeX;
            gh.ty = gh.homeY;
            gh.t = 0;
            gh.dir = NONE;
          } else {
            loseLife();
            return;
          }
        }
      }
    };

    const update = (dt: number) => {
      if (s.phase === "ready") {
        s.clock += dt * 1000;
        if (s.clock >= s.readyUntil) setGamePhase("playing");
        return;
      }
      if (s.phase !== "playing") return;
      s.clock += dt * 1000;
      stepPac(dt);
      checkCollisions();
      if (s.phase !== "playing") return;
      stepGhosts(dt);
      checkCollisions();
      if (s.phase === "playing" && s.remaining <= 0) setGamePhase("won");
    };

    const draw = () => {
      const T = s.tile;
      const pal = s.palette ?? readPalette();
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, COLS * T, ROWS * T);

      // Walls: solid blocks pulled back from the corridors, so neighbouring
      // wall tiles merge into clean shapes (no hairline seams between tiles).
      ctx.fillStyle = pal.wall;
      const inset = Math.round(T * 0.2);
      for (let y = 0; y < ROWS; y++) {
        for (let x = 0; x < COLS; x++) {
          if (!isWall(x, y)) continue;
          const l = x * T + (isWall(x - 1, y) ? 0 : inset);
          const r = (x + 1) * T - (isWall(x + 1, y) ? 0 : inset);
          const t = y * T + (isWall(x, y - 1) ? 0 : inset);
          const b = (y + 1) * T - (isWall(x, y + 1) ? 0 : inset);
          ctx.fillRect(l, t, r - l, b - t);
        }
      }

      // Dots.
      ctx.fillStyle = pal.dot;
      const blink = s.reduced || Math.floor(wall / 250) % 2 === 0;
      for (let y = 0; y < ROWS; y++) {
        for (let x = 0; x < COLS; x++) {
          const d = s.dots[y * COLS + x];
          if (!d || (d === 2 && !blink && s.phase === "playing")) continue;
          ctx.beginPath();
          ctx.arc((x + 0.5) * T, (y + 0.5) * T, d === 2 ? T * 0.22 : T * 0.08, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // Ghosts.
      const frightened = s.clock < s.frightUntil;
      const flashing = frightened && s.frightUntil - s.clock < 1500 && Math.floor(wall / 200) % 2 === 0;
      for (const gh of s.ghosts) {
        const p = pos(gh);
        const cx = (p.x + 0.5) * T;
        const cy = (p.y + 0.5) * T;
        const r = T * 0.42;
        const scared = frightened && !gh.eaten;
        ctx.fillStyle = scared ? (flashing ? pal.eye : pal.fright) : pal[gh.color];
        ctx.beginPath();
        ctx.arc(cx, cy - r * 0.15, r, Math.PI, 0);
        const base = cy + r * 0.85;
        ctx.lineTo(cx + r, base);
        const wiggle = s.reduced ? 0 : Math.floor(wall / 150) % 2;
        for (let i = 0; i < 3; i++) {
          const x0 = cx + r - (i * 2 * r) / 3;
          ctx.lineTo(x0 - r / 3, base - r * (0.25 + 0.1 * wiggle));
          ctx.lineTo(x0 - (2 * r) / 3, base);
        }
        ctx.closePath();
        ctx.fill();
        // Eyes look where the ghost is heading.
        ctx.fillStyle = pal.bg;
        for (const ex of [-0.35, 0.35]) {
          ctx.beginPath();
          ctx.arc(cx + ex * r + gh.dir.x * r * 0.12, cy - r * 0.2 + gh.dir.y * r * 0.12, r * 0.16, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // Pac.
      const pp = pos(s.pac);
      const cx = (pp.x + 0.5) * T;
      const cy = (pp.y + 0.5) * T;
      const moving = s.phase === "playing" ? !same(s.pac.dir, NONE) : s.phase === "idle";
      const chomp = s.reduced || !moving ? 0.6 : 0.08 + 0.55 * Math.abs(Math.sin(wall / 70));
      const angle = Math.atan2(s.pac.facing.y, s.pac.facing.x);
      ctx.fillStyle = pal.pac;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, T * 0.42, angle + chomp / 2 + 0.0001, angle - chomp / 2 + Math.PI * 2);
      ctx.closePath();
      ctx.fill();
    };

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!s.reduced || s.phase !== "idle") wall += dt * 1000;
      update(dt);
      draw();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      themeObs.disconnect();
      mq.removeEventListener("change", onMq);
    };
  }, [setGamePhase]);

  // ---- input: only bound while a round is live ----
  const live = phase === "playing" || phase === "ready";
  useEffect(() => {
    if (!live) return;
    const s = g.current;
    const pause = () => {
      if (s.phase === "playing" || s.phase === "ready") setGamePhase("paused");
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // Don't hijack typing in a form field, should one ever exist on this page.
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      const d = KEY_DIRS[e.key];
      if (d) {
        e.preventDefault();
        s.pac.want = d;
      } else if (e.key === "Escape" || e.key === "p" || e.key === "P") {
        e.preventDefault();
        pause();
      }
    };
    const onVis = () => {
      if (document.hidden) pause();
    };

    const canvas = canvasRef.current!;
    let sx = 0;
    let sy = 0;
    const onTouchStart = (e: TouchEvent) => {
      const t = e.touches[0];
      sx = t.clientX;
      sy = t.clientY;
    };
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault(); // keep the page from scrolling while swiping the maze
      const t = e.touches[0];
      const dx = t.clientX - sx;
      const dy = t.clientY - sy;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 18) return;
      s.pac.want = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? RIGHT : LEFT) : dy > 0 ? DOWN : UP;
      sx = t.clientX;
      sy = t.clientY;
    };

    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", pause);
    document.addEventListener("visibilitychange", onVis);
    canvas.addEventListener("touchstart", onTouchStart, { passive: true });
    canvas.addEventListener("touchmove", onTouchMove, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", pause);
      document.removeEventListener("visibilitychange", onVis);
      canvas.removeEventListener("touchstart", onTouchStart);
      canvas.removeEventListener("touchmove", onTouchMove);
    };
  }, [live, setGamePhase]);

  const overlay: { title: string; body?: string; cta: string } | null =
    phase === "idle"
      ? {
          title: "Lost? Have a snack.",
          body: reducedMotion
            ? "Arrow keys / WASD or swipe to move. Motion starts only when you press Play."
            : "Arrow keys / WASD or swipe to move. Esc pauses.",
          cta: "Play"
        }
      : phase === "paused"
        ? { title: "Paused", cta: "Resume" }
        : phase === "won"
          ? { title: "Maze cleared", body: `Final score ${score}.`, cta: "Play again" }
          : phase === "lost"
            ? { title: "Game over", body: `Final score ${score}.`, cta: "Play again" }
            : null;

  return (
    <div className="w-full max-w-[494px]">
      <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          Score <span className="font-mono text-foreground" data-testid="pacman-score">{score}</span>
        </span>
        <span aria-label={`${lives} lives left`}>
          Lives <span className="font-mono text-foreground">{lives}</span>
        </span>
      </div>
      <div ref={wrapRef} className="relative w-full overflow-hidden rounded-lg border border-border bg-border">
        <canvas
          ref={canvasRef}
          tabIndex={-1}
          role="img"
          aria-label="Pac-Man style maze game"
          className="mx-auto block outline-none"
          style={{ touchAction: live ? "none" : "auto" }}
        />
        {phase === "ready" && (
          <p className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center font-display text-sm font-medium tracking-label text-warning">
            READY
          </p>
        )}
        {overlay && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background/80 px-6 text-center">
            <p className="font-display text-lg font-medium" role="status">
              {overlay.title}
            </p>
            {overlay.body && <p className="max-w-xs text-xs text-muted-foreground">{overlay.body}</p>}
            <Button size="sm" onClick={start}>
              {overlay.cta}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
