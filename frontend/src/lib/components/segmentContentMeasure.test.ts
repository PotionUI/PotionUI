import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * Neither the editable segment content nor the read-only resolved-prompt
 * output carries a reading-measure cap: the editable card fills its card so
 * the typing area and click target aren't shrunk, and the resolved panel
 * fills its container so it can show the full expanded prompt.
 *
 * The literal port of prompt-segments-concept.html (see
 * PromptSegment.svelte / SegmentedPromptEditor.svelte) moved both rules out
 * of the components' own `<style>` blocks into the ported, globally-imported
 * segment-composer.css — this reads that file instead, and checks the mock's
 * own 13px/1.85 comfortable content setting (12px/1.65 compact) rather than
 * the pre-port 15px/1.6.
 */
function readCss(): string {
	return readFileSync(new URL('../styles/segment-composer.css', import.meta.url), 'utf8');
}

function ruleBody(css: string, selector: string): string {
	const at = css.indexOf(selector);
	if (at === -1) throw new Error(`no rule for ${selector}`);
	return css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at));
}

describe('the segment card’s editable content', () => {
	const css = readCss();
	const rule = ruleBody(css, '.segment-content .inline-chip-editor {');
	const compactRule = ruleBody(css, '.composer.compact .segment-content .inline-chip-editor {');

	it('is not width-capped — the editor fills its card', () => {
		expect(rule).not.toMatch(/max-width/);
		expect(compactRule).not.toMatch(/max-width/);
	});

	it('carries the mock’s comfortable/compact content settings', () => {
		expect(rule).toMatch(/font-size:\s*13px/);
		expect(rule).toMatch(/line-height:\s*1\.85\b/);
		expect(compactRule).toMatch(/font-size:\s*12px/);
		expect(compactRule).toMatch(/line-height:\s*1\.65\b/);
	});
});

describe('the resolved panel', () => {
	it('is not width-capped — the resolved prompt takes the whole room', () => {
		expect(ruleBody(readCss(), '.resolved-body {')).not.toMatch(/max-width/);
	});
});
