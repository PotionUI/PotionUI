import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const goto = vi.fn(() => Promise.resolve());
vi.mock('$app/navigation', () => ({ goto: (...a: unknown[]) => (goto as any)(...a) }));

import { replaceUrl } from './replaceUrl';

describe('replaceUrl', () => {
	beforeEach(() => goto.mockClear());

	it('replaces the history entry through SvelteKit without stealing focus or scrolling', () => {
		replaceUrl('/phrasebook?category=a');
		expect(goto).toHaveBeenCalledWith('/phrasebook?category=a', {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	});

	it('swallows a rejected navigation', async () => {
		goto.mockReturnValueOnce(Promise.reject(new Error('x')));
		expect(() => replaceUrl('/models')).not.toThrow();
		await Promise.resolve();
	});
});

describe('page URL sync', () => {
	it.each(['src/routes/models/+page.svelte', 'src/routes/phrasebook/+page.svelte'])(
		'%s does not call the History API directly',
		(file) => {
			const source = readFileSync(resolve(process.cwd(), file), 'utf8');
			expect(source).not.toMatch(/history\.(push|replace)State/);
			expect(source).toContain('replaceUrl(');
		}
	);
});
