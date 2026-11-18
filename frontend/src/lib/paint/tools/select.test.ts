import { describe, expect, it } from 'vitest';
import { createLassoTool } from './select';

describe('lasso select hint', () => {
	it('points mask marking to Mark the area', () => {
		expect(createLassoTool().hint).toContain('To mark an area for Edit a region, use Mark the area there.');
	});
});
