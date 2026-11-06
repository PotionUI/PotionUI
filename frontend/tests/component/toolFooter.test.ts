import { describe, it, expect, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';

const { default: ToolFooter } = await import('$lib/media-tools/ToolFooter.svelte');

function props(overrides: Record<string, unknown> = {}) {
	return {
		mode: 'new',
		showModes: true,
		note: null,
		applyLabel: 'Apply',
		applyIcon: true,
		disabled: false,
		working: false,
		progress: 0,
		statusLabel: null,
		cancelling: false,
		failure: null,
		blocked: null,
		onMode: vi.fn(),
		onCancel: vi.fn(),
		onApply: vi.fn(),
		onCancelRun: vi.fn(),
		...overrides
	};
}

function labels(root: ParentNode): string[] {
	return Array.from(root.querySelectorAll('button')).map((button) => (button.textContent || '').trim());
}

describe('ToolFooter', () => {
	it('puts the save choice on the left and Cancel and Apply on the right', () => {
		const { target } = mountComponent(ToolFooter, props());
		expect(labels(target.querySelector('[role="group"]')!)).toEqual(['Save as new', 'Replace original']);
		expect(labels(target.querySelector('.ml-auto')!)).toEqual(['Cancel', 'Apply']);
	});

	it('marks the chosen mode and reports the other one', () => {
		const onMode = vi.fn();
		const { target } = mountComponent(ToolFooter, props({ mode: 'replace', onMode }));
		const [first, second] = Array.from(target.querySelectorAll('[role="group"] button'));
		expect(first.getAttribute('aria-pressed')).toBe('false');
		expect(second.getAttribute('aria-pressed')).toBe('true');
		(first as HTMLButtonElement).click();
		expect(onMode).toHaveBeenCalledWith('new');
		(second as HTMLButtonElement).click();
		expect(onMode).toHaveBeenLastCalledWith('replace');
	});

	it('says a copy is saved and the original is not changed while Save as new is chosen', () => {
		const { target } = mountComponent(ToolFooter, props({ mode: 'new' }));
		expect(target.querySelector('[data-tool-note]')?.textContent).toBe(
			'Saves a copy. The original is not changed.'
		);
	});

	it('drops that line when Replace original is chosen', () => {
		const { target } = mountComponent(ToolFooter, props({ mode: 'replace' }));
		expect(target.querySelector('[data-tool-note]')).toBeNull();
	});

	it('shows the note instead of the choice when replacing is not offered', () => {
		const { target } = mountComponent(ToolFooter, props({ showModes: false, note: 'Saved as a new video.' }));
		expect(target.querySelector('[role="group"]')).toBeNull();
		expect(target.querySelector('[data-tool-note]')?.textContent).toBe('Saved as a new video.');
	});

	it('leaves the left side empty with neither a choice nor a note', () => {
		const { target } = mountComponent(ToolFooter, props({ showModes: false }));
		expect(labels(target)).toEqual(['Cancel', 'Apply']);
		expect(target.querySelector('[data-tool-note]')).toBeNull();
	});

	it('calls the right handler for each button', () => {
		const onCancel = vi.fn();
		const onApply = vi.fn();
		const { target } = mountComponent(ToolFooter, props({ onCancel, onApply }));
		const [cancel, apply] = Array.from(target.querySelectorAll('.ml-auto button')) as HTMLButtonElement[];
		cancel.click();
		apply.click();
		expect(onCancel).toHaveBeenCalledTimes(1);
		expect(onApply).toHaveBeenCalledTimes(1);
	});

	it('disables Apply while the edit cannot be applied and says why', () => {
		const { target } = mountComponent(ToolFooter, props({ disabled: true, blocked: 'Pick a size smaller than the original.' }));
		expect((target.querySelectorAll('.ml-auto button')[1] as HTMLButtonElement).disabled).toBe(true);
		expect(target.querySelector('[data-tool-blocked]')?.textContent).toBe('Pick a size smaller than the original.');
	});

	it('shows a failure in place of the reason and keeps Apply as it was', () => {
		const { target } = mountComponent(ToolFooter, props({ failure: 'The encoder stopped', blocked: 'Nothing to apply yet.' }));
		expect(target.querySelector('[data-tool-failure]')?.textContent).toBe('The encoder stopped');
		expect(target.querySelector('[data-tool-blocked]')).toBeNull();
	});

	it('names the action it was given and draws the check only for the plain Apply', () => {
		const plain = mountComponent(ToolFooter, props());
		expect(plain.target.querySelector('.ml-auto button:last-child svg')).not.toBeNull();
		const named = mountComponent(ToolFooter, props({ applyLabel: 'Save frame', applyIcon: false }));
		expect(labels(named.target.querySelector('.ml-auto')!)).toEqual(['Cancel', 'Save frame']);
		expect(named.target.querySelector('.ml-auto button:last-child svg')).toBeNull();
	});

	it('swaps the choice for a progress bar and Cancel edit while a run goes', () => {
		const onCancelRun = vi.fn();
		const { target } = mountComponent(ToolFooter, props({ working: true, progress: 0.42, statusLabel: 'Encoding', onCancelRun }));

		expect(target.querySelector('[role="group"]')).toBeNull();
		expect(target.querySelector('[data-tool-progress]')?.textContent).toContain('Encoding · 42%');
		expect(target.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('42');
		expect(labels(target)).toEqual(['Cancel edit']);
		(target.querySelector('button') as HTMLButtonElement).click();
		expect(onCancelRun).toHaveBeenCalledTimes(1);
	});

	it('keeps the progress between 0 and 100', () => {
		const over = mountComponent(ToolFooter, props({ working: true, progress: 7 }));
		expect(over.target.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('100');
		const under = mountComponent(ToolFooter, props({ working: true, progress: -2 }));
		expect(under.target.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('0');
	});

	it('disables Cancel edit once it was pressed and says it is cancelling', () => {
		const { target } = mountComponent(ToolFooter, props({ working: true, cancelling: true }));
		const button = target.querySelector('button') as HTMLButtonElement;
		expect(button.disabled).toBe(true);
		expect(button.textContent?.trim()).toBe('Cancelling');
	});

	it('shows plain Working with a disabled Cancel when there is no run to follow', () => {
		const { target } = mountComponent(ToolFooter, props({ working: true, cancellable: false }));
		expect(target.querySelector('[data-tool-working]')?.textContent).toContain('Working…');
		expect(target.querySelector('[data-tool-progress]')).toBeNull();
		expect(labels(target)).toEqual(['Cancel']);
		expect((target.querySelector('button') as HTMLButtonElement).disabled).toBe(true);
	});

	it('draws its own padding and surface unless it sits in a footer that has them', () => {
		const framed = mountComponent(ToolFooter, props());
		expect(framed.target.querySelector('[data-tool-footer]')?.className).toContain('px-4');
		const flush = mountComponent(ToolFooter, props({ flush: true }));
		expect(flush.target.querySelector('[data-tool-footer]')?.className).not.toContain('px-4');
		flushSync();
	});

	it('keeps a line for the message once it clears when asked to, so the footer does not change height', () => {
		const blocked = mountComponent(ToolFooter, props({ blocked: 'Paint over the area to repaint.', reserveMessage: true }));
		const cleared = mountComponent(ToolFooter, props({ blocked: null, reserveMessage: true }));
		expect(blocked.target.querySelector('[data-tool-blocked]')).not.toBeNull();
		expect(cleared.target.querySelector('[data-tool-blocked]')).toBeNull();
		const space = cleared.target.querySelector('[data-tool-message-space]');
		expect(space).not.toBeNull();
		expect(space?.className).toContain('text-sm');
		expect(space?.className).toContain('invisible');
	});

	it('reserves no line by default', () => {
		const { target } = mountComponent(ToolFooter, props({ blocked: null }));
		expect(target.querySelector('[data-tool-message-space]')).toBeNull();
	});
});
