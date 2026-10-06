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

function slopes(points: CurvePoint[]): number[] {
	const count = points.length;
	const delta: number[] = [];
	for (let i = 0; i < count - 1; i++) {
		delta.push((points[i + 1][1] - points[i][1]) / (points[i + 1][0] - points[i][0]));
	}
	const tangent = new Array<number>(count).fill(0);
	tangent[0] = delta[0];
	tangent[count - 1] = delta[count - 2];
	for (let i = 1; i < count - 1; i++) {
		if (delta[i - 1] * delta[i] <= 0) tangent[i] = 0;
		else {
			const h0 = points[i][0] - points[i - 1][0];
			const h1 = points[i + 1][0] - points[i][0];
			const w1 = 2 * h1 + h0;
			const w2 = h1 + 2 * h0;
			tangent[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
		}
	}
	return tangent;
}

export function sampleCurve(points: CurvePoint[], x: number): number {
	const count = points.length;
	if (x <= points[0][0]) return points[0][1] + (x - points[0][0]) * slopes(points)[0];
	if (x >= points[count - 1][0]) {
		return points[count - 1][1] + (x - points[count - 1][0]) * slopes(points)[count - 1];
	}
	const tangent = slopes(points);
	let i = 0;
	while (i < count - 2 && x > points[i + 1][0]) i++;
	const h = points[i + 1][0] - points[i][0];
	const t = (x - points[i][0]) / h;
	const t2 = t * t;
	const t3 = t2 * t;
	return (
		(2 * t3 - 3 * t2 + 1) * points[i][1] +
		(t3 - 2 * t2 + t) * h * tangent[i] +
		(-2 * t3 + 3 * t2) * points[i + 1][1] +
		(t3 - t2) * h * tangent[i + 1]
	);
}

export function curvePath(points: CurvePoint[], width: number, height: number, samples = 48): string {
	const parts: string[] = [];
	for (let i = 0; i <= samples; i++) {
		const x = i / samples;
		const y = Math.min(1, Math.max(0, sampleCurve(points, x)));
		parts.push(`${i === 0 ? 'M' : 'L'}${(x * width).toFixed(1)} ${((1 - y) * height).toFixed(1)}`);
	}
	return parts.join('');
}
