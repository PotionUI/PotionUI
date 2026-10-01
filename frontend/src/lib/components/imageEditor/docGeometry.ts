import type { DocGeometry } from './types';

const EPSILON = 1e-6;

export function identityGeometry(width: number, height: number): DocGeometry {
	return {
		originalWidth: width,
		originalHeight: height,
		width,
		height,
		a: 1,
		b: 0,
		c: 0,
		d: 1,
		e: 0,
		f: 0
	};
}

export function flipGeometry(g: DocGeometry, axis: 'horizontal' | 'vertical'): DocGeometry {
	if (axis === 'horizontal') {
		return { ...g, a: -g.a, c: -g.c, e: g.width - g.e };
	}
	return { ...g, b: -g.b, d: -g.d, f: g.height - g.f };
}

export function rotateGeometry(g: DocGeometry, direction: 'cw' | 'ccw'): DocGeometry {
	if (direction === 'cw') {
		return {
			...g,
			a: -g.b,
			c: -g.d,
			e: g.height - g.f,
			b: g.a,
			d: g.c,
			f: g.e,
			width: g.height,
			height: g.width
		};
	}
	return {
		...g,
		a: g.b,
		c: g.d,
		e: g.f,
		b: -g.a,
		d: -g.c,
		f: g.width - g.e,
		width: g.height,
		height: g.width
	};
}

export function translateGeometry(
	g: DocGeometry,
	dx: number,
	dy: number,
	width: number,
	height: number
): DocGeometry {
	return { ...g, e: g.e + dx, f: g.f + dy, width, height };
}

export function scaleGeometry(g: DocGeometry, width: number, height: number): DocGeometry {
	const fx = width / g.width;
	const fy = height / g.height;
	return {
		...g,
		a: g.a * fx,
		c: g.c * fx,
		e: g.e * fx,
		b: g.b * fy,
		d: g.d * fy,
		f: g.f * fy,
		width,
		height
	};
}

export function isIdentityGeometry(g: DocGeometry): boolean {
	return (
		g.width === g.originalWidth &&
		g.height === g.originalHeight &&
		Math.abs(g.a - 1) < EPSILON &&
		Math.abs(g.d - 1) < EPSILON &&
		Math.abs(g.b) < EPSILON &&
		Math.abs(g.c) < EPSILON &&
		Math.abs(g.e) < EPSILON &&
		Math.abs(g.f) < EPSILON
	);
}
