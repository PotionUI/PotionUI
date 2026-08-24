import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * Neither the editable segment content nor the read-only resolved-prompt
 * output carries a reading-measure cap: the editable card fills its card so
 * the typing area and click target aren't shrunk, and the resolved panel
 * fills its container so it can show the full expanded prompt.
 *
 * jsdom applies no stylesheet for a mounted Svelte component (getComputedStyle
 * returns "none" for everything), so this reads the components' own style
 * blocks — the only place the constraint would live if it existed.
 */
function styleBlock(path: string): string {
	const source = readFileSync(new URL(path, import.meta.url), 'utf8');
	const match = source.match(/<style>([\s\S]*)<\/style>/);
	if (!match) throw new Error(`no <style> block in ${path}`);
	return match[1];
}

function ruleBody(css: string, selector: string): string {
	const at = css.indexOf(selector);
	if (at === -1) throw new Error(`no rule for ${selector}`);
	return css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at));
}

describe('the segment card’s editable content', () => {
	const css = styleBlock('./PromptSegment.svelte');
	const rule = ruleBody(css, '.card-content :global(.inline-chip-editor)');

	it('is not width-capped — the editor fills its card', () => {
		expect(rule).not.toMatch(/max-width/);
	});

	it('still carries the design’s 15/26 content setting', () => {
		expect(rule).toMatch(/font-size:\s*0\.9375rem/);
		expect(rule).toMatch(/line-height:\s*1\.7333/);
	});
});

describe('the resolved panel', () => {
	it('is not width-capped — the resolved prompt takes the whole room', () => {
		const css = styleBlock('./SegmentedPromptEditor.svelte');
		expect(ruleBody(css, '.resolved-body')).not.toMatch(/max-width/);
	});
});
