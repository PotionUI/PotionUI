// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import { fire, press, setRange } from './helpers/schemaForm';
import { trackOffset } from '$lib/components/form-fields/sliderGeometry';

const { default: SliderField } = await import('$lib/components/form-fields/SliderField.svelte');

const SIGNED = { title: 'Brightness', minimum: -100, maximum: 100, step: 1, default: 0 };

function mountSlider(config: Record<string, unknown>, value: number | undefined, extra: Record<string, unknown> = {}) {
	const onChange = vi.fn((_name: string, next: number) => mounted.rerender({ value: next }));
	const onCommit = vi.fn();
	const mounted = mountComponent(SliderField, { name: 'level', config, value, onChange, onCommit, ...extra });
	return {
		...mounted,
		onChange,
		onCommit,
		range: () => mounted.target.querySelector<HTMLInputElement>('input[type="range"]')!,
		root: () => mounted.target.firstElementChild as HTMLElement
	};
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('SliderField reset to default', () => {
	it('resets on a double-click of the track and reports one commit', () => {
		const slider = mountSlider(SIGNED, 40);

		slider.range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		flushSync();

		expect(slider.onChange).toHaveBeenCalledWith('level', 0);
		expect(slider.onCommit).toHaveBeenCalledTimes(1);
		expect(slider.onCommit).toHaveBeenCalledWith('level');
		expect(slider.range().value).toBe('0');
	});

	it.each(['Backspace', 'Delete'])('resets on %s while the slider has focus, announcing it', (key) => {
		const slider = mountSlider(SIGNED, -25);

		const event = press(slider.range(), 'keydown', key);

		expect(event.defaultPrevented).toBe(true);
		expect(slider.onChange).toHaveBeenCalledWith('level', 0);
		expect(slider.onCommit).toHaveBeenCalledTimes(1);
		expect(slider.target.querySelector('[role="status"]')!.textContent).toBe('Brightness reset to default 0');
	});

	it('keeps the key from reaching window shortcuts such as delete-generation', () => {
		const slider = mountSlider(SIGNED, 10);
		const windowListener = vi.fn();
		window.addEventListener('keydown', windowListener);

		press(slider.range(), 'keydown', 'Delete');
		window.removeEventListener('keydown', windowListener);

		expect(windowListener).not.toHaveBeenCalled();
	});

	it('does nothing when the value already is the default', () => {
		const slider = mountSlider(SIGNED, 0);

		slider.range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		press(slider.range(), 'keydown', 'Backspace');

		expect(slider.onChange).not.toHaveBeenCalled();
		expect(slider.onCommit).not.toHaveBeenCalled();
	});

	it('does nothing when the field declares no default', () => {
		const { default: _omit, ...noDefault } = SIGNED;
		const slider = mountSlider(noDefault, 40);

		slider.range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		const event = press(slider.range(), 'keydown', 'Backspace');

		expect(slider.onChange).not.toHaveBeenCalled();
		expect(event.defaultPrevented).toBe(false);
	});

	it('does nothing when the config opts out with reset: false', () => {
		const slider = mountSlider({ ...SIGNED, reset: false }, 40);

		slider.range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		press(slider.range(), 'keydown', 'Delete');

		expect(slider.onChange).not.toHaveBeenCalled();
	});

	it('does not reset when double-clicking the value readout', () => {
		const slider = mountSlider(SIGNED, 40);

		slider.target.querySelector('[data-slider-value]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));

		expect(slider.onChange).not.toHaveBeenCalled();
	});

	it('reads the opt-out from the configuration block too', () => {
		const slider = mountSlider({ ...SIGNED, configuration: { reset: false } }, 40);

		slider.range().dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));

		expect(slider.onChange).not.toHaveBeenCalled();
	});
});

describe('SliderField commit', () => {
	it('commits on the change that ends a drag and not on the inputs before it', () => {
		const slider = mountSlider(SIGNED, 0);

		setRange(slider.range(), 10);
		setRange(slider.range(), 20);
		expect(slider.onCommit).not.toHaveBeenCalled();
		fire(slider.range(), 'change');

		expect(slider.onChange).toHaveBeenCalledTimes(2);
		expect(slider.onCommit).toHaveBeenCalledTimes(1);
	});

	it('commits a value typed into the readout', () => {
		const slider = mountSlider(SIGNED, 0);

		slider.target.querySelector<HTMLButtonElement>('[data-slider-value]')!.click();
		flushSync();
		const input = slider.target.querySelector<HTMLInputElement>('input[type="text"]')!;
		input.value = '42';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		press(input, 'keydown', 'Enter');

		expect(slider.onChange).toHaveBeenCalledWith('level', 42);
		expect(slider.onCommit).toHaveBeenCalledTimes(1);
	});

	it('works with no onCommit given', () => {
		const onChange = vi.fn();
		const { target } = mountComponent(SliderField, { name: 'level', config: SIGNED, value: 5, onChange });

		const range = target.querySelector<HTMLInputElement>('input[type="range"]')!;
		setRange(range, 9);
		fire(range, 'change');
		range.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));

		expect(onChange).toHaveBeenCalled();
	});
});

describe('SliderField two-sided fill', () => {
	const fillOf = (slider: ReturnType<typeof mountSlider>) => slider.range().getAttribute('style') ?? '';

	it.each([
		[-50, 0.25, 0.5],
		[0, 0.5, 0.5],
		[25, 0.5, 0.625]
	])('paints the default to the value at %s', (value, from, to) => {
		const slider = mountSlider({ ...SIGNED, fill: 'from-default' }, value);
		const style = fillOf(slider);

		expect(style).toContain(trackOffset(from));
		expect(style).toContain(trackOffset(to));
	});

	it('paints from the start of the track by default, as before', () => {
		const slider = mountSlider(SIGNED, -50);
		const style = fillOf(slider);

		expect(style).toContain(`rgb(var(--signal)) ${trackOffset(0.25)}`);
		expect(style).toContain(`rgb(var(--surface-3)) ${trackOffset(0.25)}`);
		expect(style).not.toContain(trackOffset(0.5));
	});

	it('falls back to the start-anchored fill when the default is out of range', () => {
		const slider = mountSlider({ ...SIGNED, default: 500, fill: 'from-default' }, 50);

		expect(fillOf(slider)).toContain(`rgb(var(--signal)) ${trackOffset(0.75)}`);
	});
});

describe('SliderField layout', () => {
	it('keeps the card with its range legend and tick note by default', () => {
		const slider = mountSlider(SIGNED, 10);

		expect(slider.root().classList.contains('field-card')).toBe(true);
		expect(slider.root().getAttribute('data-slider-layout')).toBeNull();
		expect(slider.target.textContent).toContain('tick = default');
		expect(slider.target.textContent).toContain('-100');
	});

	it('puts label, track and value on one line when the layout is inline', () => {
		const slider = mountSlider({ ...SIGNED, layout: 'inline' }, 10);
		const root = slider.root();

		expect(root.getAttribute('data-slider-layout')).toBe('inline');
		expect(root.classList.contains('field-card')).toBe(false);
		expect(root.classList.contains('flex')).toBe(true);
		expect(root.classList.contains('items-center')).toBe(true);
		expect(slider.target.textContent).not.toContain('tick = default');
		const label = root.querySelector('label')!;
		const value = root.querySelector('[data-slider-value]')!;
		const track = slider.range().parentElement!;
		expect(label.closest('.order-1')).toBeTruthy();
		expect(track.classList.contains('order-2')).toBe(true);
		expect(value.classList.contains('order-3')).toBe(true);
		expect(value.textContent?.trim()).toBe('10');
	});

	it('marks a changed value and signs positive readouts when asked', () => {
		const slider = mountSlider({ ...SIGNED, layout: 'inline', signed: true }, 25);

		expect(slider.root().getAttribute('data-changed')).toBe('true');
		expect(slider.target.querySelector('[data-slider-value]')!.textContent?.trim()).toBe('+25');
		expect(slider.target.querySelector('[data-slider-value]')!.className).toContain('tabular-nums');
		expect(slider.target.querySelector('[data-slider-value]')!.className).toContain('font-mono');
	});

	it('does not mark a value at its default as changed', () => {
		const slider = mountSlider({ ...SIGNED, layout: 'inline' }, 0);

		expect(slider.root().getAttribute('data-changed')).toBeNull();
	});

	it('still draws the default tick', () => {
		const slider = mountSlider({ ...SIGNED, layout: 'inline' }, 25);

		expect(slider.target.querySelector('span.pointer-events-none')).toBeTruthy();
	});
});
