// @vitest-environment jsdom
//
// SeedField must honor a configured min/max/step instead of treating a
// truthy-falsy zero bound as "unset", ignoring step when rolling a random
// seed, or offering Auto (-1) when the field's range excludes negatives.
import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: SeedField } = await import('../../src/lib/components/form-fields/SeedField.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountField(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onChange = vi.fn();
	const component = createClassComponent({
		component: SeedField as never,
		target,
		props: {
			name: 'seed',
			config: {},
			value: -1,
			onChange,
			...props
		}
	});
	return {
		target,
		component,
		onChange,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function randomizeButton(target: HTMLElement) {
	return target.querySelectorAll('button')[0] as HTMLButtonElement;
}

function autoButton(target: HTMLElement) {
	return Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Auto')) as
		| HTMLButtonElement
		| undefined;
}

let mounted: ReturnType<typeof mountField> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('SeedField range/step handling', () => {
	it('fixed min=max=0 randomizes to exactly 0 and offers no Auto control', async () => {
		mounted = mountField({ config: { minimum: 0, maximum: 0 }, value: 0 });
		expect(autoButton(mounted.target)).toBeUndefined();

		randomizeButton(mounted.target).click();
		await Promise.resolve();

		expect(mounted.onChange).toHaveBeenCalledWith('seed', 0);
	});

	it('rolls only on-step values within min/max, never an off-grid value like 137', async () => {
		let cursor = 0;
		const sequence = [0, 0.4, 0.99];
		const random = vi.fn(() => sequence[cursor++ % sequence.length]);
		mounted = mountField({
			config: { minimum: 100, maximum: 200, step: 50 },
			value: 100,
			random
		});

		const button = randomizeButton(mounted.target);
		button.click();
		await Promise.resolve();
		button.click();
		await Promise.resolve();
		button.click();
		await Promise.resolve();

		const rolled = mounted.onChange.mock.calls.map((call) => call[1]);
		expect(rolled).toEqual([100, 150, 200]);
		for (const seed of rolled) {
			expect(seed).not.toBe(137);
		}
	});

	it('hides Auto and preserves a persisted value for a positive-only field', () => {
		mounted = mountField({ config: { minimum: 100, maximum: 200, step: 50 }, value: 150 });

		expect(autoButton(mounted.target)).toBeUndefined();
		const input = mounted.target.querySelector('input') as HTMLInputElement;
		expect(input.value).toBe('150');
		expect(mounted.onChange).not.toHaveBeenCalled();
	});

	it('default field (min -1, uint32 max) randomizes in range and shows Auto for -1', async () => {
		mounted = mountField({ config: {}, value: -1, random: () => 0 });

		expect(autoButton(mounted.target)).toBeDefined();
		expect(mounted.target.textContent).toContain('random each run');

		randomizeButton(mounted.target).click();
		await Promise.resolve();
		expect(mounted.onChange).toHaveBeenCalledWith('seed', 0);
	});

	it('an unsatisfiable range does nothing on Randomize and shows a validation message', async () => {
		mounted = mountField({ config: { minimum: 101, maximum: 149, step: 50 }, value: 101 });

		randomizeButton(mounted.target).click();
		await Promise.resolve();

		expect(mounted.onChange).not.toHaveBeenCalled();
		expect(mounted.target.querySelector('[role="alert"]')?.textContent).toMatch(/no seed value/i);
	});
});
