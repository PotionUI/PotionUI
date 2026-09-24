import { describe, it, expect } from 'vitest';
import { detailLayoutColumnsClass } from './detailLayout';

describe('detailLayoutColumnsClass', () => {
	it('is the two-column class when an aside is given', () => {
		expect(detailLayoutColumnsClass(true)).toBe('detail-layout-columns');
	});

	it('adds the main-only modifier when there is no aside', () => {
		expect(detailLayoutColumnsClass(false)).toBe('detail-layout-columns detail-layout-columns--main-only');
	});
});
