import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';
import type { ContinueOutcome } from '$lib/tool-tabs/continueEdit';

const outcome = vi.hoisted(() => ({ current: { kind: 'opened', tabId: 't1' } as ContinueOutcome }));

vi.mock('$lib/tool-tabs/continueEdit', () => ({
	continueEditInNewTab: vi.fn(async () => outcome.current)
}));

const { default: EditorOriginChip } = await import('$lib/components/EditorOriginChip.svelte');
const { continueEditInNewTab } = await import('$lib/tool-tabs/continueEdit');
const { toasts } = await import('$lib/stores/toast');
const { get } = await import('svelte/store');

const ORIGIN = { editId: 'ed_1', stepId: 's7', task: 'edit_region', runId: 'er_1' };

beforeEach(() => {
	outcome.current = { kind: 'opened', tabId: 't1' };
	vi.mocked(continueEditInNewTab).mockClear();
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the Made in the editor chip', () => {
	it('reads Made in the editor and opens the edit, then tells its owner', async () => {
		const onopened = vi.fn();
		const view = mountComponent(EditorOriginChip, { origin: ORIGIN, onopened });
		const chip = view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!;
		expect(chip.textContent?.trim()).toBe('Made in the editor');
		chip.click();
		await settle();
		expect(continueEditInNewTab).toHaveBeenCalledWith('ed_1', null);
		expect(onopened).toHaveBeenCalledTimes(1);
		view.destroy();
	});

	it('turns into a quiet "The edit was deleted" when the edit no longer exists', async () => {
		outcome.current = { kind: 'missing' };
		const onopened = vi.fn();
		const view = mountComponent(EditorOriginChip, { origin: ORIGIN, onopened });
		view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!.click();
		await settle();
		const gone = view.target.querySelector('[data-editor-origin="deleted"]')!;
		expect(gone.textContent?.trim()).toBe('The edit was deleted');
		expect(view.target.querySelector('button')).toBeNull();
		expect(JSON.stringify(get(toasts))).toContain('That edit no longer exists.');
		expect(onopened).not.toHaveBeenCalled();
		view.destroy();
	});

	it('names a lost picture with a notice and keeps the chip', async () => {
		outcome.current = { kind: 'source-missing' };
		const view = mountComponent(EditorOriginChip, { origin: ORIGIN });
		view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!.click();
		await settle();
		expect(view.target.querySelector('[data-editor-origin="open"]')).not.toBeNull();
		expect(JSON.stringify(get(toasts))).toContain('The picture for this edit is no longer available.');
		view.destroy();
	});

	it('keeps its label for the screen reader when drawn as an icon', async () => {
		const view = mountComponent(EditorOriginChip, { origin: ORIGIN, iconOnly: true });
		const chip = view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!;
		expect(chip.getAttribute('aria-label')).toBe('Made in the editor');
		expect(chip.textContent?.trim()).toBe('');
		view.destroy();
	});

	it('does not let the click reach the card behind it', async () => {
		const outer = vi.fn();
		const card = document.body.appendChild(document.createElement('div'));
		card.addEventListener('click', outer);
		const view = mountComponent(EditorOriginChip, { origin: ORIGIN }, { target: card.appendChild(document.createElement('div')) });
		view.target.querySelector<HTMLButtonElement>('[data-editor-origin="open"]')!.click();
		await settle();
		expect(outer).not.toHaveBeenCalled();
		view.destroy();
	});
});
