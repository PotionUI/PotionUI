// @vitest-environment jsdom
//
// Segment details now edit in a modal instead of the old inline reveal under
// the card. Its footer and keyboard shortcuts must match the house
// confirm-modal standard: a secondary Cancel hinting Esc, a primary Save
// hinting Enter, Escape/Enter wired through the same keydown path every
// other confirm dialog uses, and Cancel/Esc must discard edits rather than
// apply them.
//
// BaseModal portals its dialog onto <body>, so assertions read from
// `document`, not the mount target.
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Segment } from '../../src/lib/types/segments';

const { default: PromptSegmentDetailsModal } = await import(
	'../../src/lib/components/PromptSegmentDetailsModal.svelte'
);

function segment(partial: Partial<Segment> = {}): Segment {
	return {
		id: 'seg-1',
		content: 'a lighthouse keeper',
		type: 'content',
		chips: {},
		enabled: true,
		name: 'Subject',
		color: '#3B82F6',
		description: 'A quick mood note',
		...partial
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;
let onClose: ReturnType<typeof import('vitest').vi.fn>;
let onSave: ReturnType<typeof import('vitest').vi.fn>;

async function mountModal(props: Record<string, unknown> = {}) {
	const { vi } = await import('vitest');
	target = document.createElement('div');
	document.body.appendChild(target);
	onClose = vi.fn();
	onSave = vi.fn();
	component = mount(PromptSegmentDetailsModal, {
		target,
		props: { isOpen: true, segment: segment(), onClose, onSave, ...props }
	});
	flushSync();
}

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
});

describe('PromptSegmentDetailsModal footer', () => {
	it('renders a Cancel button hinting Esc and a Save button hinting Enter', async () => {
		await mountModal();

		const buttons = Array.from(document.querySelectorAll('button'));
		const cancelBtn = buttons.find((b) => b.textContent?.includes('Cancel'));
		const saveBtn = buttons.find((b) => b.textContent?.includes('Save'));
		expect(cancelBtn?.textContent).toContain('Esc');
		expect(saveBtn?.textContent).toContain('Enter');
	});

	it('pre-fills the fields from the segment', async () => {
		await mountModal();

		const nameInput = document.querySelector('input[placeholder="Optional segment name"]') as HTMLInputElement;
		const descriptionInput = document.querySelector('textarea') as HTMLTextAreaElement;
		expect(nameInput.value).toBe('Subject');
		expect(descriptionInput.value).toBe('A quick mood note');
	});
});

describe('PromptSegmentDetailsModal confirm keyboard', () => {
	it('Escape cancels without saving', async () => {
		await mountModal();

		const nameInput = document.querySelector('input[placeholder="Optional segment name"]') as HTMLInputElement;
		nameInput.value = 'Changed name';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(onClose).toHaveBeenCalledTimes(1);
		expect(onSave).not.toHaveBeenCalled();
	});

	it('Enter saves the edited fields and closes', async () => {
		await mountModal();

		const nameInput = document.querySelector('input[placeholder="Optional segment name"]') as HTMLInputElement;
		nameInput.value = 'Changed name';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(onSave).toHaveBeenCalledTimes(1);
		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({ name: 'Changed name', color: '#3B82F6', description: 'A quick mood note' })
		);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('re-opening after a cancel starts from the real segment again, not the discarded draft', async () => {
		await mountModal();
		const nameInput = () =>
			document.querySelector('input[placeholder="Optional segment name"]') as HTMLInputElement;

		nameInput().value = 'Discarded';
		nameInput().dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		unmount(component);
		await mountModal();

		expect(nameInput().value).toBe('Subject');
	});
});

describe('PromptSegmentDetailsModal template provenance', () => {
	it('shows the template origin as a read-only note when the segment came from one', async () => {
		await mountModal({
			segment: segment({ template: { id: 't1', name: 'Portrait', slot: 'subject', position: 0 } })
		});

		expect(document.body.textContent).toContain('Portrait');
		expect(document.body.textContent).toContain('subject');
	});

	it('shows nothing extra when the segment has no template origin', async () => {
		await mountModal();
		expect(document.body.textContent).not.toContain('From template');
	});
});
