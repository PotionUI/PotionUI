// @vitest-environment jsdom
//
// The phrasebook replace modal previews a find/replace across selected
// values and applies it through the batch endpoint. Its footer and keyboard
// shortcuts must match the house confirm-modal standard: a secondary Cancel
// hinting Esc, a primary Apply hinting Enter, and Escape/Enter wired through
// the same keydown path every other confirm dialog uses.
//
// BaseModal portals its dialog onto <body>, so assertions read from
// `document`, not the mount target.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		previewPhrasebookBatch: vi.fn(),
		runPhrasebookBatch: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: PhrasebookReplaceModal } = await import(
	'../../src/routes/phrasebook/components/PhrasebookReplaceModal.svelte'
);
const { defaultFilters } = await import('../../src/routes/phrasebook/phrasebookSearch');

function hit(id: string, label: string) {
	return {
		id,
		category_id: 'cat-1',
		label,
		value: 'a dog',
		sort_order: 0,
		is_active: true,
		created_at: '',
		updated_at: '',
		category_path: 'animals.dogs',
		category_name: 'Dogs',
		category_is_active: true,
		matches: []
	};
}

const VALUES = [hit('v1', 'Puppy'), hit('v2', 'Hound')];

const PREVIEW = {
	items: [{ id: 'v1', field: 'value' as const, before: 'a dog', after: 'a cat' }],
	changed: 1,
	unchanged: ['v2']
};

const EMPTY_PREVIEW = { items: [], changed: 0, unchanged: ['v1', 'v2'] };

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;
let onClose: ReturnType<typeof vi.fn>;
let onApplied: ReturnType<typeof vi.fn>;

function mountModal() {
	target = document.createElement('div');
	document.body.appendChild(target);
	onClose = vi.fn();
	onApplied = vi.fn();
	component = mount(PhrasebookReplaceModal, {
		target,
		props: {
			isOpen: true,
			values: VALUES,
			filters: { ...defaultFilters(), query: 'dog' },
			onClose,
			onApplied
		}
	});
	flushSync();
}

async function flushAsync() {
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	if (!Element.prototype.animate) {
		Element.prototype.animate = () => {
			const animation = {
				cancel() {},
				finished: Promise.resolve(),
				onfinish: null as (() => void) | null,
				currentTime: 0,
				playbackRate: 1
			};
			setTimeout(() => animation.onfinish?.(), 0);
			return animation as never;
		};
	}
	vi.mocked(api.previewPhrasebookBatch).mockResolvedValue({ success: true, data: PREVIEW });
	vi.mocked(api.runPhrasebookBatch).mockResolvedValue({
		success: true,
		data: { updated: [], skipped: [], deleted: [], message: 'ok' }
	});
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('PhrasebookReplaceModal footer', () => {
	it('renders a Cancel button hinting Esc and an Apply button hinting Enter, padded', async () => {
		mountModal();
		await flushAsync();

		const footerWrapper = document.querySelector('.border-t.border-line > div');
		expect(footerWrapper?.className).toContain('px-6');
		expect(footerWrapper?.className).toContain('py-4');

		const buttons = Array.from(document.querySelectorAll('button')).filter((b) =>
			['Cancel', 'Apply'].some((label) => b.textContent?.includes(label))
		);
		const cancelBtn = buttons.find((b) => b.textContent?.includes('Cancel'));
		const applyBtn = buttons.find((b) => b.textContent?.includes('Apply'));
		expect(cancelBtn?.textContent).toContain('Esc');
		expect(applyBtn?.textContent).toContain('Enter');
	});

	it('Escape cancels the modal', async () => {
		mountModal();
		await flushAsync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();

		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('Enter applies the replace once a preview shows changes', async () => {
		mountModal();
		await flushAsync();
		expect(document.querySelector('[data-replace-count]')?.textContent).toContain('1 will change');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await flushAsync();

		expect(api.runPhrasebookBatch).toHaveBeenCalledTimes(1);
	});

	it('Enter does nothing when the preview has no changes', async () => {
		vi.mocked(api.previewPhrasebookBatch).mockResolvedValue({ success: true, data: EMPTY_PREVIEW });
		mountModal();
		await flushAsync();
		expect(document.querySelector('[data-replace-count]')?.textContent).toContain('0 will change');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await flushAsync();

		expect(api.runPhrasebookBatch).not.toHaveBeenCalled();
	});
});
