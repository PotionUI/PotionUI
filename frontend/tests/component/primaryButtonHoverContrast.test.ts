// @vitest-environment jsdom
//
// Maintainer, live: the phrasebook value finder's "Use selected value" button
// lost its label on hover — white text on the white accent. Cause: the shared
// `.small-button:hover { color: fg }` rule ties `.primary:hover` on specificity
// and `.primary:hover` only restated the background, so the hover text colour
// fell through to the foreground. jsdom doesn't evaluate `:hover` for computed
// styles (same limitation as chipConfigFocusRing.test.ts), so this reads the
// stylesheet text: the accent hover rule must pin its own text colour.
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

const css = readFileSync('src/lib/styles/segment-composer.css', 'utf8');

function ruleBody(selector: string): string {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = css.match(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`));
	expect(match, `${selector} rule should exist`).not.toBeNull();
	return match![1];
}

describe('.primary hover contrast', () => {
	it('the small-button hover rule recolours text, so .primary:hover must restate its own', () => {
		expect(ruleBody('.small-button:hover')).toMatch(/color:\s*rgb\(var\(--fg\)\)/);
		expect(ruleBody('.primary:hover')).toMatch(/color:\s*rgb\(var\(--accent-contrast\)\)/);
	});

	it('keeps the accent border on hover instead of the neutral line-strong', () => {
		expect(ruleBody('.primary:hover')).toMatch(/border-color:\s*rgb\(var\(--accent-hover\)\)/);
	});
});
