import { describe, expect, it } from 'vitest';
import { adminTabHref } from './adminTabHref';

describe('adminTabHref', () => {
	it('links to the tab alone, dropping the previous page state', () => {
		expect(adminTabHref('/admin', 'models')).toBe('/admin?tab=models');
	});
});
