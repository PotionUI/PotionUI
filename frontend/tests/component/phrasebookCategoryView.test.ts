// @vitest-environment jsdom
//
// The phrasebook category detail panel (right-hand pane, no value selected)
// renders an Overview tab (Details, Subcategories) and a Preview images tab
// (the generation flow), and drives the "Select missing" shortcut and the
// Generate button off the real Values-pane selection.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getPhrasebookCategories: vi.fn(),
		getCategoryChildren: vi.fn(),
		getPhrasebookCategory: vi.fn(),
		exportPhrasebookCategory: vi.fn(),
		toggleCategoryActive: vi.fn(),
		listPresets: vi.fn(),
		getSessionsForPreset: vi.fn(),
		getPresetModes: vi.fn(),
		generatePreviews: vi.fn(),
		getFileURL: vi.fn(() => ''),
		setOnAuthExpired: vi.fn(),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null)
	}
}));

const { api } = await import('$lib/services/api/index');
const { phrasebookStore } = await import('$lib/stores/phrasebook');
const { previewGenerationStore } = await import('$lib/stores/previewGeneration');
const { default: CategoryInfoView } = await import(
	'../../src/routes/phrasebook/components/CategoryInfoView.svelte'
);

const CATEGORY = {
	id: 'cat-1',
	name: 'lighting',
	path: 'style.lighting',
	parent_id: undefined,
	description: 'Lighting setups for portrait and product shots.',
	is_active: true,
	created_at: '2026-08-01T00:00:00Z',
	updated_at: '2026-08-30T00:00:00Z'
};

function value(id: string, label: string, opts: { active?: boolean; preview?: boolean } = {}) {
	return {
		id,
		category_id: 'cat-1',
		label,
		value: label.toLowerCase(),
		sort_order: 0,
		is_active: opts.active ?? true,
		preview_file_id: opts.preview ? `file-${id}` : undefined,
		created_at: '',
		updated_at: ''
	};
}

const VALUES = [
	value('v1', 'Golden hour', { preview: true }),
	value('v2', 'Rim light'),
	value('v3', 'Softbox'),
	value('v4', 'Hard noon sun', { active: false })
];

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function settle() {
	flushSync();
	await flush(0);
	flushSync();
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;

async function mountView() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(CategoryInfoView, { target });
	await settle();
}

beforeEach(async () => {
	vi.clearAllMocks();
	phrasebookStore.reset();
	previewGenerationStore.reset();

	vi.mocked(api.getPhrasebookCategories).mockResolvedValue({
		success: true,
		data: { categories: [CATEGORY] }
	} as never);
	vi.mocked(api.getCategoryChildren).mockResolvedValue({
		success: true,
		data: { categories: [] }
	} as never);
	vi.mocked(api.getPhrasebookCategory).mockResolvedValue({
		success: true,
		data: { category: CATEGORY, values: VALUES }
	} as never);
	vi.mocked(api.listPresets).mockResolvedValue({
		success: true,
		data: [{ id: 'preset-1', name: 'Krea-2' }]
	} as never);
	vi.mocked(api.getSessionsForPreset).mockResolvedValue({
		success: true,
		data: [{ id: 'session-1', name: 'Studio product' }]
	} as never);
	vi.mocked(api.getPresetModes).mockResolvedValue({
		success: true,
		data: { modes: [{ name: 'txt2img', label: 'Txt2Img' }], default_mode: 'txt2img' }
	} as never);

	await phrasebookStore.loadRootCategories();
	phrasebookStore.setSelectedCategoryId('cat-1');
	await phrasebookStore.loadCategoryValues('cat-1');
	await previewGenerationStore.loadPresets();
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
});

function sectionLabels(): string[] {
	return Array.from(document.querySelectorAll('h3')).map((el) => el.textContent?.trim() ?? '');
}

function buttonByText(text: string): HTMLButtonElement {
	const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
		b.textContent?.trim().startsWith(text)
	);
	if (!button) throw new Error(`missing button starting with "${text}"`);
	return button;
}

function tabButton(label: string): HTMLButtonElement {
	const button = Array.from(
		document.querySelectorAll<HTMLButtonElement>('nav[aria-label="Category details"] button')
	).find((b) => b.textContent?.trim().startsWith(label));
	if (!button) throw new Error(`missing tab starting with "${label}"`);
	return button;
}

async function switchToTab(label: string) {
	tabButton(label).click();
	await settle();
}

describe('CategoryInfoView', () => {
	it('renders the Overview tab active by default, with Details then Subcategories', async () => {
		await mountView();

		const overview = tabButton('Overview');
		const previewImages = tabButton('Preview images');
		expect(overview.getAttribute('aria-current')).toBe('page');
		expect(previewImages.getAttribute('aria-current')).toBeNull();
		expect(sectionLabels()).toEqual(['Details', 'Subcategories']);
	});

	it('renders both detail bodies full width, without the narrow max-w-2xl wrapper', async () => {
		await mountView();

		expect(target.querySelector('.max-w-2xl')).toBeNull();

		await switchToTab('Preview images');

		expect(target.querySelector('.max-w-2xl')).toBeNull();
	});

	it('shows the header chips for value/subcategory counts and status', async () => {
		await mountView();

		expect(target.textContent).toContain('4 VALUES');
		expect(target.textContent).toContain('lighting');
		expect(target.textContent).toContain('Active');
	});

	it('switching to Preview images shows Target/Prompt/Advanced and hides the Overview sections', async () => {
		await mountView();
		await switchToTab('Preview images');

		expect(tabButton('Preview images').getAttribute('aria-current')).toBe('page');
		expect(sectionLabels()).toEqual(['Target', 'Prompt', 'Advanced', 'Existing previews (1)']);
		expect(target.textContent).not.toContain('Lighting setups for portrait and product shots.');
	});

	it('reports S of N selected, matching the store selection', async () => {
		phrasebookStore.selectValueIds(['v2', 'v3']);
		await mountView();
		await switchToTab('Preview images');

		expect(target.textContent).toContain('2 of 4');
	});

	it('"Select missing" selects exactly the previewless active ids', async () => {
		phrasebookStore.deselectAllValues();
		await mountView();
		await switchToTab('Preview images');

		const selectMissing = buttonByText('Select missing');
		selectMissing.click();
		await settle();

		expect(get(phrasebookStore).selectedValueIds).toEqual(new Set(['v2', 'v3']));
	});

	it('Generate button label follows the selection count and disables at zero', async () => {
		phrasebookStore.deselectAllValues();
		await mountView();
		await switchToTab('Preview images');

		const generateZero = buttonByText('Generate');
		expect(generateZero.textContent).toContain('Generate');
		expect(generateZero.textContent).toContain('0');
		expect(generateZero.disabled).toBe(true);

		phrasebookStore.selectValueIds(['v2', 'v3']);
		await settle();

		const generateTwo = buttonByText('Generate');
		expect(generateTwo.textContent).toContain('2');
		expect(generateTwo.disabled).toBe(false);
	});

	it('Advanced is collapsed by default and shows a mono "seed 42" summary once a fixed seed is set', async () => {
		await mountView();
		await switchToTab('Preview images');

		expect(target.querySelector('#preview-negative')).toBeNull();
		expect(target.textContent).not.toContain('seed 42');

		previewGenerationStore.setUseFixedSeed(true);
		previewGenerationStore.setFixedSeed(42);
		await settle();

		expect(target.querySelector('#preview-negative')).toBeNull();
		expect(target.textContent).toContain('seed 42');

		const toggle = target.querySelector<HTMLButtonElement>('button[aria-label="Expand"]');
		if (!toggle) throw new Error('missing Advanced expand toggle');
		toggle.click();
		await settle();

		expect(target.querySelector('#preview-negative')).not.toBeNull();
		expect(target.textContent).not.toContain('seed 42');
	});

	it('shows a live running indicator on the Preview images tab while a batch is in flight', async () => {
		vi.mocked(api.generatePreviews).mockResolvedValue({
			success: true,
			data: { started: 2, generations: [{ generation_id: 'g1' }, { generation_id: 'g2' }] }
		} as never);
		phrasebookStore.selectValueIds(['v2', 'v3']);
		await mountView();
		await switchToTab('Preview images');

		expect(tabButton('Preview images').textContent).not.toContain('/');

		buttonByText('Generate').click();
		await settle();

		expect(tabButton('Preview images').textContent).toContain('0/2');
	});

	it('export action calls the export API', async () => {
		vi.mocked(api.exportPhrasebookCategory).mockResolvedValue('name: lighting\n');
		if (!URL.createObjectURL) (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => 'blob:mock';
		if (!URL.revokeObjectURL) (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};
		await mountView();

		const exportButton = document.querySelector<HTMLButtonElement>('button[aria-label="Export as YAML"]');
		if (!exportButton) throw new Error('missing export button');
		exportButton.click();
		await settle();

		expect(api.exportPhrasebookCategory).toHaveBeenCalledWith('cat-1');
	});
});
