import { describe, expect, it } from 'vitest';
import {
	flipGeometry,
	identityGeometry,
	isIdentityGeometry,
	rotateGeometry,
	scaleGeometry,
	translateGeometry
} from './docGeometry';

const start = identityGeometry(160, 100);

describe('docGeometry', () => {
	it('starts as the identity', () => {
		expect(isIdentityGeometry(start)).toBe(true);
	});

	it('is changed by a single flip and restored by flipping back', () => {
		const flipped = flipGeometry(start, 'horizontal');
		expect(isIdentityGeometry(flipped)).toBe(false);
		expect(isIdentityGeometry(flipGeometry(flipped, 'horizontal'))).toBe(true);
		expect(isIdentityGeometry(flipGeometry(flipGeometry(start, 'vertical'), 'vertical'))).toBe(
			true
		);
	});

	it('is changed by a rotation and restored by rotating back', () => {
		const turned = rotateGeometry(start, 'cw');
		expect(turned.width).toBe(100);
		expect(turned.height).toBe(160);
		expect(isIdentityGeometry(turned)).toBe(false);
		expect(isIdentityGeometry(rotateGeometry(turned, 'ccw'))).toBe(true);
	});

	it('returns to the identity after four turns the same way', () => {
		let g = start;
		for (let i = 0; i < 4; i++) g = rotateGeometry(g, 'cw');
		expect(isIdentityGeometry(g)).toBe(true);
	});

	it('is not the identity after a half turn even though the size matches', () => {
		const half = rotateGeometry(rotateGeometry(start, 'cw'), 'cw');
		expect(half.width).toBe(160);
		expect(half.height).toBe(100);
		expect(isIdentityGeometry(half)).toBe(false);
	});

	it('equals the identity after a half turn plus both flips', () => {
		const half = rotateGeometry(rotateGeometry(start, 'cw'), 'cw');
		expect(isIdentityGeometry(flipGeometry(flipGeometry(half, 'horizontal'), 'vertical'))).toBe(
			true
		);
	});

	it('is changed by a crop and restored by extending back over the same area', () => {
		const cropped = translateGeometry(start, -20, -10, 80, 60);
		expect(isIdentityGeometry(cropped)).toBe(false);
		expect(isIdentityGeometry(translateGeometry(cropped, 20, 10, 160, 100))).toBe(true);
	});

	it('is changed by a scale and restored by scaling back', () => {
		const scaled = scaleGeometry(start, 80, 50);
		expect(isIdentityGeometry(scaled)).toBe(false);
		expect(isIdentityGeometry(scaleGeometry(scaled, 160, 100))).toBe(true);
	});

	it('tracks a crop followed by a flip and the reverse order as different', () => {
		const a = flipGeometry(translateGeometry(start, -20, 0, 80, 100), 'horizontal');
		const b = translateGeometry(flipGeometry(start, 'horizontal'), -20, 0, 80, 100);
		expect(a.e).not.toBe(b.e);
	});
});
