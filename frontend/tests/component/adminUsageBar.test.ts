import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import UsageBar from '../../src/routes/admin/components/plans/UsageBar.svelte';

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement | undefined;

function render(props: { used: number; limit: number | null; compact?: boolean }) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(UsageBar, { target, props: { kind: { value_type: 'bytes' }, ...props } });
	flushSync();
	return target;
}

function spans(el: HTMLElement): string[] {
	return Array.from(el.querySelectorAll('[data-usage-bar] > span > span')).map((s) => s.textContent?.trim() ?? '');
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
});

describe('UsageBar', () => {
	it('shows only the used value in a compact cell without a limit', () => {
		const el = render({ used: 34.8 * 1024 ** 3, limit: null, compact: true });
		expect(spans(el)).toHaveLength(1);
		expect(el.textContent).not.toContain('no limit');
	});

	it('keeps the used value and the no limit note in separate elements', () => {
		const el = render({ used: 1024 ** 3, limit: null });
		const parts = spans(el);
		expect(parts).toHaveLength(2);
		expect(parts[1]).toBe('no limit');
	});

	it('shows the limit as its own element after the used value', () => {
		const el = render({ used: 1024 ** 3, limit: 10 * 1024 ** 3, compact: true });
		const parts = spans(el);
		expect(parts).toHaveLength(2);
		expect(parts[1].startsWith('/ ')).toBe(true);
	});
});
