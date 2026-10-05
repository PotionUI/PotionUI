// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ChipsEditor from '$lib/components/compare/editors/ChipsEditor.svelte';
import NumberEditor from '$lib/components/compare/editors/NumberEditor.svelte';
import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
});

function candidate(overrides: Partial<AxisCandidate>): AxisCandidate {
	return {
		field: 'f',
		label: 'F',
		type: 'select',
		group: 'G',
		editor: 'chips',
		unavailableReason: null,
		options: [],
		min: null,
		max: null,
		step: null,
		currentValue: undefined,
		modelType: null,
		config: {},
		...overrides
	};
}

function render(component: any, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(component, { target, props });
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

describe('ChipsEditor', () => {
	const options = [
		{ value: 'euler', label: 'euler' },
		{ value: 'heun', label: 'heun' },
		{ value: 'uni_pc', label: 'uni_pc' }
	];

	it('toggles a chip through onChange', () => {
		const onChange = vi.fn();
		const target = render(ChipsEditor, { candidate: candidate({ options }), axis: null, tabId: 't', onChange });
		const chip = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'heun')!;
		chip.click();
		expect(onChange).toHaveBeenCalledWith([{ value: 'heun', label: 'heun' }]);
	});

	it('All selects every option', () => {
		const onChange = vi.fn();
		const target = render(ChipsEditor, { candidate: candidate({ options }), axis: null, tabId: 't', onChange });
		const all = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'All')!;
		all.click();
		expect(onChange.mock.calls[0][0]).toHaveLength(3);
	});

	it('shows the selected count', () => {
		const axis: CompareAxis = { field: 'f', type: 'select', label: 'F', values: [{ value: 'euler', label: 'euler' }] };
		const target = render(ChipsEditor, { candidate: candidate({ options }), axis, tabId: 't', onChange: vi.fn() });
		expect(target.querySelector('[data-testid="chips-count"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('1 of 3');
	});
});

describe('NumberEditor', () => {
	it('emits a range clamped to the field max', () => {
		let last: CompareAxisValue[] = [];
		const onChange = (values: CompareAxisValue[]) => (last = values);
		const target = render(NumberEditor, {
			candidate: candidate({ type: 'slider', editor: 'number', min: 1, max: 10, step: 1, currentValue: 8 }),
			axis: null,
			tabId: 't',
			onChange
		});
		expect(last.map((v) => v.value)).toEqual([8, 9, 10]);
		const to = target.querySelector('input[aria-label="To"]') as HTMLInputElement;
		to.value = '99';
		to.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(last.map((v) => v.value)).toEqual([8, 9, 10]);
		const from = target.querySelector('input[aria-label="From"]') as HTMLInputElement;
		from.value = '-5';
		from.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(last[0].value).toBe(1);
		expect(last[last.length - 1].value).toBe(10);
	});
});
