import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';

const getGenerationById = vi.fn();
const getGenerationParams = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		getGenerationById: (...args: unknown[]) => getGenerationById(...args),
		getGenerationParams: (...args: unknown[]) => getGenerationParams(...args),
		getClient: vi.fn(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() })),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		listPresets: vi.fn().mockResolvedValue({ success: true, data: [] }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

const { default: LibraryItemModal } = await import('$lib/components/library/LibraryItemModal.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { forgetDetailsPrefs } = await import('$lib/components/generation/detailsPrefs');
const { PARAMETERS_FOLD_KEY } = await import('$lib/components/generation/parametersFold');

const flush = async () => {
	for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function item(id: string, over: Record<string, unknown> = {}) {
	return {
		id,
		filename: `${id}.png`,
		original_filename: `${id}-name.png`,
		media_type: 'image',
		mime_type: 'image/png',
		url: `/api/media/uploads/${id}.png`,
		width: 1024,
		height: 512,
		size: 2048,
		created_at: '2026-11-12T20:49:00Z',
		tags: [],
		is_favorite: false,
		...over
	};
}

let component: { $destroy: () => void } | undefined;
let target: HTMLElement | undefined;

async function mountModal(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: LibraryItemModal as never,
		target,
		props: { item: item('a'), onClose: vi.fn(), onDeleteRequest: vi.fn(), ...props }
	});
	await flush();
}

const sectionIds = () =>
	[...document.body.querySelectorAll('[data-details-section]')].map((el) => el.getAttribute('data-details-section'));
const section = (id: string) => document.body.querySelector(`[data-details-section="${id}"]`) as HTMLElement | null;
const button = (label: string) => document.body.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement | null;
const press = (key: string) => window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));

beforeEach(() => {
	localStorage.clear();
	forgetDetailsPrefs();
	getGenerationById.mockReset();
	getGenerationParams.mockReset();
	getGenerationById.mockResolvedValue({ success: true, data: { form_data: { prompt: 'a fox' } } });
	getGenerationParams.mockResolvedValue({ success: true, data: { parameters: { steps: 24 }, models: [] } });
});

afterEach(() => {
	component?.$destroy();
	target?.remove();
	component = undefined;
	document.body.innerHTML = '';
});

describe('navigation', () => {
	const items = ['a', 'b', 'c'].map((id) => item(id));

	it('shows the position and walks to the neighbours with buttons', async () => {
		const onSelect = vi.fn();
		await mountModal({ item: items[1], navigation: { items, onSelect } });
		expect(document.body.querySelector('[data-library-position]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('2 / 3');
		button('Next item')!.click();
		expect(onSelect).toHaveBeenLastCalledWith(items[2]);
		button('Previous item')!.click();
		expect(onSelect).toHaveBeenLastCalledWith(items[0]);
	});

	it('does not navigate past the ends or from a disabled button', async () => {
		const onSelect = vi.fn();
		await mountModal({ item: items[2], navigation: { items, onSelect } });
		expect(button('Next item')!.disabled).toBe(true);
		button('Next item')!.click();
		press('ArrowRight');
		expect(onSelect).not.toHaveBeenCalled();
		press('ArrowLeft');
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect).toHaveBeenCalledWith(items[1]);
	});

	it('walks with the arrow keys and disables the ends', async () => {
		const onSelect = vi.fn();
		await mountModal({ item: items[0], navigation: { items, onSelect } });
		expect(button('Previous item')!.disabled).toBe(true);
		press('ArrowLeft');
		expect(onSelect).not.toHaveBeenCalled();
		press('ArrowRight');
		expect(onSelect).toHaveBeenCalledWith(items[1]);
	});

	it('renders no arrows without a list or when the item is not in it', async () => {
		await mountModal({});
		expect(button('Next item')).toBeNull();
		expect(document.body.querySelector('[data-library-position]')).toBeNull();
		component?.$destroy();
		target?.remove();
		await mountModal({ item: item('zzz'), navigation: { items, onSelect: vi.fn() } });
		expect(button('Next item')).toBeNull();
	});
});

describe('sections by data', () => {
	it('shows only the file facts that exist', async () => {
		await mountModal({ item: item('a', { duration_seconds: undefined, fps: undefined, mime_type: undefined, width: undefined, height: undefined, size: undefined }) });
		expect(section('library-file')).toBeNull();
		component?.$destroy();
		target?.remove();
		await mountModal({ item: item('v', { media_type: 'video', mime_type: 'video/mp4', duration_seconds: 4, fps: 24 }) });
		const labels = [...document.body.querySelectorAll('[data-fact-grid] dt')].map((el) => el.textContent?.trim());
		expect(labels).toEqual(['Type', 'Dimensions', 'Duration', 'Frame rate', 'Size']);
	});

	it('lists tags as chips under Tags, with an empty state', async () => {
		await mountModal({ item: item('a', { tags: [{ id: 't1', name: 'keepers' }] }) });
		expect(document.body.querySelector('[data-library-item-tags]')?.textContent).toContain('keepers');
		component?.$destroy();
		target?.remove();
		await mountModal({});
		expect(document.body.querySelector('[data-library-item-tags]')?.textContent).toContain('No tags yet');
	});

	it('puts the name, favourite mark and added time in the header, not in the column', async () => {
		await mountModal({ item: item('a', { is_favorite: true }) });
		const header = document.body.querySelector('[data-details-header]') as HTMLElement;
		expect(header.querySelector('h2')?.textContent).toBe('a-name.png');
		expect(header.querySelector('[data-library-favorite-mark]')?.getAttribute('aria-label')).toBe('Favorite');
		expect(header.querySelector('button[aria-label="Copy file name"]')).not.toBeNull();
		expect(header.querySelector('[data-library-kind]')?.textContent).toBe('image');
		const added = header.querySelector('[data-testid="library-added"]') as HTMLButtonElement;
		expect(added.textContent).toContain('Added');
		expect(added.querySelector('[aria-hidden="true"] svg')).not.toBeNull();
		expect(added.hasAttribute('data-has-tooltip')).toBe(true);
		added.focus();
		expect(document.activeElement).toBe(added);
		const column = document.body.querySelector('[data-details-info]') as HTMLElement;
		expect(column.querySelector('[data-library-item-heading]')).toBeNull();
		expect(column.querySelector('[data-details-section]')?.getAttribute('data-details-section')).toBe('library-file');
	});

	it('shows no favourite mark for an item that is not a favourite', async () => {
		await mountModal({});
		expect(document.body.querySelector('[data-library-favorite-mark]')).toBeNull();
	});

	it('gives the Library sections a decorative icon', async () => {
		await mountModal({ item: item('a', { source_generation_id: 'gen-1' }) });
		for (const id of ['library-file', 'library-tags', 'library-source', 'library-parameters']) {
			const icon = section(id)?.querySelector('span[aria-hidden="true"] svg');
			expect(icon, id).not.toBeNull();
		}
	});

	it('starts the Library column with a strip header and falls back to Tags without facts', async () => {
		await mountModal({});
		expect(section('library-file')?.hasAttribute('data-details-strip')).toBe(true);
		expect(section('library-tags')?.hasAttribute('data-details-strip')).toBe(false);
		component?.$destroy();
		target?.remove();
		await mountModal({ item: item('a', { mime_type: undefined, width: undefined, height: undefined, size: undefined }) });
		expect(section('library-tags')?.hasAttribute('data-details-strip')).toBe(true);
	});

	it('has no Source or Parameters for a plain upload', async () => {
		await mountModal({});
		expect(sectionIds()).toEqual(['library-file', 'library-tags']);
	});

	it('shows Source and a folded Parameters section for an item from a generation', async () => {
		await mountModal({ item: item('a', { source_generation_id: 'gen-1' }) });
		expect(sectionIds()).toEqual(['library-file', 'library-tags', 'library-source', 'library-parameters']);
		expect(document.body.querySelector('[data-back-to-generation]')).not.toBeNull();
		expect(section('library-parameters')?.querySelector('button[aria-expanded]')?.getAttribute('aria-expanded')).toBe('false');
	});

	it('fetches the parameters only once the section is opened, read-only and labelled', async () => {
		await mountModal({ item: item('a', { source_generation_id: 'gen-1' }) });
		expect(getGenerationParams).not.toHaveBeenCalled();
		(section('library-parameters')!.querySelector('button[aria-expanded]') as HTMLButtonElement).click();
		await flush();
		expect(getGenerationParams).toHaveBeenCalledWith('gen-1', 0);
		const text = section('library-parameters')!.textContent ?? '';
		expect(text).toContain('From the source generation');
		expect(text).toContain('Steps');
		expect(text).toContain('24');
		expect(localStorage.getItem(PARAMETERS_FOLD_KEY)).toBeNull();
	});

	it('uses the file index of a generation link', async () => {
		await mountModal({ generationLink: { generationId: 'gen-9', fileIndex: 2 } });
		(section('library-parameters')!.querySelector('button[aria-expanded]') as HTMLButtonElement).click();
		await flush();
		expect(getGenerationParams).toHaveBeenCalledWith('gen-9', 2);
	});

	it('says the generation was deleted and offers no Parameters', async () => {
		getGenerationById.mockResolvedValue({ success: false });
		await mountModal({ item: item('a', { source_generation_id: 'gen-1' }) });
		expect(document.body.querySelector('[data-back-to-generation]')?.textContent).toContain('Generation deleted');
		expect(section('library-parameters')).toBeNull();
		expect(getGenerationParams).not.toHaveBeenCalled();
	});

	it('keeps the slice origin line in Source', async () => {
		await mountModal({ item: item('a', { provenance: { slice: { source_name: 'sheet.png', index: 3, count: 6 } } }) });
		expect(section('library-source')).not.toBeNull();
		expect(document.body.querySelector('[data-slice-origin]')?.textContent?.trim()).toBe('From sheet · cell 4 of 6');
	});
});

describe('stage and bar', () => {
	it('renders the shared stage with the file facts and keeps the library action bar', async () => {
		await mountModal({});
		expect(document.body.querySelector('[data-details-stage] img')?.getAttribute('src')).toBe('/api/media/uploads/a.png');
		expect(document.body.querySelector('[data-stage-meta]')?.textContent).toContain('1024 × 512');
		expect(document.body.querySelector('[data-details-action-bar]')).not.toBeNull();
		flushSync();
	});
});
