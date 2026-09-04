// @vitest-environment jsdom
//
// Maintainer, live: "when I press the settings icon on the phrasebook chip
// ... it adds this ugly browser default focus border which is making this
// cool effect we introduced barely visible." jsdom doesn't evaluate
// `:focus`/`:focus-visible` matching for computed styles (same limitation as
// segmentContentMeasure.test.ts), so this reads the stylesheet text directly:
// a pointer click's `:focus` must be silenced WITHOUT touching the real
// keyboard `:focus-visible` ring — a bare `:focus { outline: none }` would
// have to lose that specificity tie-break race by source order, so it has to
// be `:focus:not(:focus-visible)` specifically.
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

const css = readFileSync('src/lib/styles/segment-composer.css', 'utf8');

describe('chip-config focus ring', () => {
	it('silences :focus only for pointer clicks (:not(:focus-visible)), never bare :focus', () => {
		const silenceRule = /\.chip-config:focus:not\(:focus-visible\)[^{]*\{[^}]*outline:\s*none/;
		expect(css).toMatch(silenceRule);
		// A bare `.chip-config:focus { outline: none }` (no :not(:focus-visible))
		// would tie in specificity with the :focus-visible ring below and win by
		// source order, killing the keyboard ring too — must not exist.
		expect(css).not.toMatch(/\.chip-config:focus\s*\{/);
		expect(css).not.toMatch(/\.chip-config:focus,/);
	});

	it('keeps an explicit :focus-visible ring for the same chip buttons', () => {
		const visibleRule = /\.chip-config:focus-visible[^{]*\{[^}]*outline:\s*2px solid/;
		expect(css).toMatch(visibleRule);
		expect(css).toMatch(/\.chip-main:focus-visible/);
	});

	it('applies the same pointer-click silencing to picker/popover buttons', () => {
		for (const selector of ['.picker-row', '.popover-body button', '.popover-actions button', '.small-button']) {
			const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
			const re = new RegExp(`${escaped}:focus:not\\(:focus-visible\\)`);
			expect(css, `${selector} should silence :focus:not(:focus-visible)`).toMatch(re);
		}
	});
});
