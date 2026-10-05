// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

const css = readFileSync('src/lib/styles/segment-composer.css', 'utf8');
const editor = readFileSync('src/lib/components/InlineChipEditor.svelte', 'utf8');

function declarations(source: string, selector: string): Record<string, string> {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const out: Record<string, string> = {};
	const re = new RegExp(`(?:^|[}>])\\s*${escaped}\\s*\\{([^}]*)\\}`, 'g');
	for (const match of source.matchAll(re)) {
		for (const part of match[1].split(';')) {
			const idx = part.indexOf(':');
			if (idx > 0) out[part.slice(0, idx).trim()] = part.slice(idx + 1).trim();
		}
	}
	return out;
}

const px = (value: string | undefined) => parseFloat(value ?? 'NaN');

const editorRules: Array<[string, number]> = [
	['.segment-content .inline-chip-editor', 13],
	['.composer.compact .segment-content .inline-chip-editor', 12],
	['.prompt-editor.compact .segment-content .inline-chip-editor', 12]
];

describe('segment text line box vs inline chips', () => {
	const chip = declarations(css, '.segment-content .inline-chip-editor .chip');

	it('chip is bounded to a block size under the line box of every editor variant', () => {
		const chipHeight = px(chip['max-height']);
		expect(chipHeight).toBeGreaterThan(0);
		for (const [selector, fontSize] of editorRules) {
			const rule = declarations(css, selector);
			const lineHeight = parseFloat(rule['line-height']);
			expect(lineHeight, selector).toBeGreaterThanOrEqual(1.9);
			expect(lineHeight * fontSize, selector).toBeGreaterThanOrEqual(chipHeight);
		}
	});

	it('chip is vertically centred in the line with no transform offset', () => {
		expect(chip['vertical-align']).toBe('middle');
		expect(chip['transform']).toBe('none');
	});

	it('inner chip parts fit inside the chip height', () => {
		const chipHeight = px(chip['max-height']);
		for (const part of ['.chip-main', '.chip-config']) {
			const rule = declarations(css, `.segment-content .inline-chip-editor ${part}`);
			expect(px(rule['min-height']), part).toBeLessThanOrEqual(chipHeight - 2);
		}
	});

	it('the editor own line-height clears its chip types outside the composer', () => {
		const rule = declarations(editor, '.inline-chip-editor');
		const lineHeight = parseFloat(rule['line-height']);
		expect(lineHeight).toBeGreaterThanOrEqual(1.9);
		for (const cls of ['inline-chip', 'choice-group-chip', 'variable-usage-chip']) {
			const chipRule = declarations(editor, `.inline-chip-editor :global(.${cls})`);
			expect(chipRule['vertical-align'], cls).toBe('middle');
			expect(parseFloat(chipRule['max-height']) * 16, cls).toBeLessThanOrEqual(lineHeight * 14);
		}
	});
});
