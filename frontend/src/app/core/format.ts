import { Pipe, PipeTransform } from '@angular/core';

/** Format a number with `digits` significant digits after multiplying by `scale`. */
export function sig(value: number | null | undefined, digits = 4, scale = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '–';
  }
  const v = value * scale;
  if (v === 0) {
    return '0';
  }
  const abs = Math.abs(v);
  if (abs >= 1e6 || abs < 1e-4) {
    return v.toExponential(Math.max(digits - 1, 0)).replace('e+', 'e');
  }
  const decimals = Math.max(0, digits - 1 - Math.floor(Math.log10(abs)));
  return v.toFixed(Math.min(decimals, 8));
}

/** Signed percentage, e.g. +12.3 % */
export function pct(value: number | null | undefined, digits = 1, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '–';
  }
  const s = (value * 100).toFixed(digits);
  return signed && value > 0 ? `+${s} %` : `${s} %`;
}

/** `value | n:4:1e3` -> 4 significant digits of value*1000 */
@Pipe({ name: 'n' })
export class NumPipe implements PipeTransform {
  transform(value: number | null | undefined, digits = 4, scale = 1): string {
    return sig(value, digits, scale);
  }
}

/** `ratio | pct:1:true` -> "+12.3 %" */
@Pipe({ name: 'pct' })
export class PctPipe implements PipeTransform {
  transform(value: number | null | undefined, digits = 1, signed = false): string {
    return pct(value, digits, signed);
  }
}

export function parseNumberList(text: string): number[] {
  return text
    .split(/[\s,;]+/)
    .filter((t) => t.trim() !== '')
    .map((t) => Number(t))
    .filter((v) => Number.isFinite(v));
}

/** Inclusive range from, to with step (rounded to avoid 0.30000000004). */
export function range(from: number, to: number, step: number): number[] {
  const out: number[] = [];
  if (step <= 0 || !Number.isFinite(from) || !Number.isFinite(to)) {
    return out;
  }
  const n = Math.floor((to - from) / step + 1e-9);
  for (let k = 0; k <= n && k < 500; k++) {
    out.push(Math.round((from + k * step) * 1e9) / 1e9);
  }
  return out;
}

export function download(filename: string, content: string, mime = 'text/plain'): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function thetaDeg(aHot: number, aCold: number, height: number): number {
  return (Math.atan((aCold - aHot) / (2 * height)) * 180) / Math.PI;
}

export function aHotFromAngle(aCold: number, height: number, thetaDegValue: number): number {
  return aCold - 2 * height * Math.tan((thetaDegValue * Math.PI) / 180);
}
