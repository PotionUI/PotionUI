import { pchip } from './engine';

export type CurvePoint = readonly [number, number];

export function parsePoints(raw: unknown): CurvePoint[] | null {
	if (!Array.isArray(raw) || raw.length < 2) return null;
	const points: CurvePoint[] = [];
	for (const entry of raw) {
		if (!Array.isArray(entry) || entry.length !== 2) return null;
		const [x, y] = entry;
		if (typeof x !== 'number' || typeof y !== 'number') return null;
		points.push([x, y]);
	}
	return points;
}

export function sampleCurve(points: CurvePoint[], x: number): number {
	return pchip(points.map(([px, py]) => [px, py] as [number, number]))(x);
}

export function curvePath(points: CurvePoint[], width: number, height: number, samples = 48): string {
	const curve = pchip(points.map(([px, py]) => [px, py] as [number, number]));
	const parts: string[] = [];
	for (let i = 0; i <= samples; i++) {
		const x = i / samples;
		const y = Math.min(1, Math.max(0, curve(x)));
		parts.push(`${i === 0 ? 'M' : 'L'}${(x * width).toFixed(1)} ${((1 - y) * height).toFixed(1)}`);
	}
	return parts.join('');
}
