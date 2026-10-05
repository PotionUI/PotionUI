// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

const { default: SeedField } = await import('../../src/lib/components/form-fields/SeedField.svelte');
const { createClassComponent } = await import('svelte/legacy');

const css = readFileSync('src/app.css', 'utf8');

describe('seed field dice button focus', () => {
	it('sits inside the framed container that carries the ring', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: SeedField as never,
			target,
			props: { name: 'seed', config: {}, value: 5, onChange: () => {} }
		});
		const frame = target.querySelector('.field-frame');
		expect(frame).not.toBeNull();
		expect(frame!.querySelector('button')).not.toBeNull();
		component.$destroy();
		target.remove();
	});

	it('rings the frame when an inner button takes keyboard focus', () => {
		expect(css).toMatch(/\.field-frame:has\(button:focus-visible\)/);
		expect(css).toMatch(/\.input:has\(button:focus-visible\)/);
	});

	it('draws no outline on a button inside a framed input', () => {
		const rule = /:is\(\.input, \.field-frame\) button:focus-visible\s*\{([^}]*)\}/.exec(css);
		expect(rule).not.toBeNull();
		expect(rule![1]).toMatch(/outline:\s*none/);
	});
});
