import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installFakeCanvas, readPixel } from './helpers/fakeCanvas';

const listFilters = vi.fn();
const getFilterLut = vi.fn();
const createMineFilter = vi.fn();
const updateMineFilter = vi.fn();
const deleteMineFilter = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listFilters: (...args: unknown[]) => listFilters(...args),
		getFilterLut: (...args: unknown[]) => getFilterLut(...args),
		createMineFilter: (...args: unknown[]) => createMineFilter(...args),
		updateMineFilter: (...args: unknown[]) => updateMineFilter(...args),
		deleteMineFilter: (...args: unknown[]) => deleteMineFilter(...args)
	}
}));

const renderRecipe = vi.fn((buffer: { width: number; height: number; data: Uint8ClampedArray }, _recipe?: unknown, _intensity?: number) => {
	const data = new Uint8ClampedArray(buffer.data);
	for (let i = 0; i < data.length; i += 4) {
		data[i] = 255 - data[i];
		data[i + 1] = 255 - data[i + 1];
		data[i + 2] = 255 - data[i + 2];
	}
	return { width: buffer.width, height: buffer.height, data };
});

vi.mock('$lib/filters/render', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/filters/render')>()),
	renderRecipe: (buffer: never, recipe: unknown, intensity: number) => renderRecipe(buffer, recipe, intensity),
	toImageData: (buffer: unknown) => buffer
}));

const CUBE_2 = ['LUT_3D_SIZE 2', '0 0 0', '1 0 0', '0 1 0', '1 1 0', '0 0 1', '1 0 1', '0 1 1', '1 1 1'].join('\n');

function apiError(status: number, error: string, message: string) {
	return { response: { status, data: { detail: { error, message } } } };
}

const { default: FiltersPanel } = await import('$lib/components/imageEditor/FiltersPanel.svelte');
const { default: AdjustPanel } = await import('$lib/components/imageEditor/AdjustPanel.svelte');
const { default: SaveFilterDialog } = await import('$lib/components/imageEditor/SaveFilterDialog.svelte');
const { PaintSession } = await import('$lib/components/imageEditor/session');
const { resetFilterCatalog } = await import('$lib/filters/catalog');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

const EMBER_STEPS = [
	{ op: 'white_balance', temperature: 34, tint: 6 },
	{ op: 'tone', contrast: 10, saturation: 8 },
	{ op: 'vignette', amount: 22, midpoint: 55, feather: 60 }
];

function item(overrides: Record<string, unknown>) {
	return {
		id: 'x',
		name: 'X',
		description: '',
		group: 'Colour',
		order: 10,
		intensity: 100,
		source: 'builtin',
		plugin_id: null,
		owned: false,
		has_lut: false,
		lut_size: null,
		lut_url: null,
		steps: EMBER_STEPS,
		unavailable_ops: [],
		needs_plugin: null,
		backend_ok: true,
		revision: 'r1',
		...overrides
	};
}

const CATALOG = {
	filters: [
		item({ id: 'pop', name: 'Pop', order: 10 }),
		item({ id: 'ember', name: 'Ember', order: 20, description: 'Warm skin and wood.' }),
		item({ id: 'matte', name: 'Matte', group: 'Film', order: 10 }),
		item({
			id: 'harbor',
			name: 'Harbor',
			source: 'local',
			has_lut: true,
			lut_size: 2,
			lut_url: '/api/filters/harbor/lut',
			order: 10
		}),
		item({ id: 'crt:neon', name: 'Neon Rain', source: 'plugin', plugin_id: 'crt', order: 10 }),
		item({
			id: 'crt:scanlines',
			name: 'Scanlines',
			source: 'plugin',
			plugin_id: 'crt',
			order: 20,
			unavailable_ops: ['crt.scanlines'],
			needs_plugin: 'crt-pack'
		}),
		item({ id: 'mine:01', name: 'My Ember', source: 'mine', owned: true, group: 'Mine', intensity: 80 })
	],
	ops: [],
	groups: ['Colour', 'Film']
};

let restoreCanvas: () => void;
let session: InstanceType<typeof PaintSession>;
let host: HTMLElement;
const unsubscribers: Array<() => void> = [];
const instances: Array<{ $destroy: () => void }> = [];

async function settle() {
	for (let i = 0; i < 8; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function byLabel(name: string): HTMLElement | null {
	return document.body.querySelector(`[aria-label="${name}"]`);
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return Array.from(document.body.querySelectorAll('button')).find(
		(button) => (button.textContent || '').trim() === text
	);
}

function mountComponent(component: unknown, extra: Record<string, unknown> = {}) {
	const instance = createClassComponent({
		component: component as never,
		target: host,
		props: { session, state: session.snapshot(), ...extra }
	});
	instances.push(instance);
	unsubscribers.push(session.subscribe(() => instance.$set({ state: session.snapshot() })));
	return instance;
}

function input(el: Element, value: string) {
	(el as HTMLInputElement).value = value;
	el.dispatchEvent(new Event('input', { bubbles: true }));
}

beforeEach(async () => {
	restoreCanvas = installFakeCanvas();
	host = document.createElement('div');
	document.body.appendChild(host);
	listFilters.mockReset().mockResolvedValue(CATALOG);
	getFilterLut.mockReset().mockResolvedValue(CUBE_2);
	createMineFilter.mockReset();
	updateMineFilter.mockReset();
	deleteMineFilter.mockReset();
	renderRecipe.mockClear();
	resetFilterCatalog();
	session = new PaintSession();
	session.openBlank({ width: 8, height: 8, background: 'white', pen: null });
	session.setTool('filters');
});

afterEach(() => {
	for (const stop of unsubscribers.splice(0)) stop();
	for (const instance of instances.splice(0)) instance.$destroy();
	session.detach();
	host.remove();
	document.body.innerHTML = '';
	restoreCanvas();
});

describe('Filters tool strip', () => {
	it('renders None, every group, the badges and the Mine filters from /api/filters', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();

		const radios = Array.from(document.body.querySelectorAll('[role="radio"]'));
		const names = radios.map((radio) => radio.getAttribute('aria-label'));
		expect(names.slice(0, 5)).toEqual(['None', 'Pop', 'Ember', 'Matte', 'Harbor']);
		expect(names).toContain('My Ember');
		expect(names.at(-1)).toBe('My Ember');
		expect(radios[0].getAttribute('aria-checked')).toBe('true');
		expect(byLabel('Harbor')?.textContent).toContain('LUT');
		expect(byLabel('Neon Rain')?.textContent).toContain('Plugin');
		expect(byLabel('Pop')?.textContent).not.toContain('LUT');
		expect(document.body.querySelector('[role="radiogroup"][aria-label="Filters"]')).not.toBeNull();
	});

	it('dims a plugin filter whose plugin is off, names the plugin and refuses selection', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();

		const locked = document.body.querySelector('[role="radio"][aria-disabled="true"]') as HTMLButtonElement;
		expect(locked).not.toBeNull();
		expect(locked.getAttribute('aria-label')).toContain('crt-pack');
		expect(locked.className).toContain('opacity-60');
		expect(locked.querySelector('svg')).not.toBeNull();

		locked.click();
		await settle();
		expect(session.snapshot().filter.active).toBeNull();
	});

	it('selecting a filter shows its name, steps and the default intensity', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();

		byLabel('Ember')!.click();
		await settle();

		const snapshot = session.snapshot();
		expect(snapshot.filter.active?.id).toBe('ember');
		expect(snapshot.filter.intensity).toBe(100);
		expect(host.querySelector('[data-testid="filter-name"]')?.textContent).toBe('Ember');
		expect(host.textContent).toContain('Built in · 3 steps');
		expect(host.textContent).toContain('Nothing changes until Apply');
	});
});

describe('apply and undo', () => {
	it('applies at the chosen intensity as one undo step and restores on undo', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		const layer = session.snapshot().layers[0];
		expect(readPixel(layer.canvas)).toEqual([255, 255, 255, 255]);

		byLabel('Ember')!.click();
		await settle();
		input(host.querySelector('#filter-intensity')!, '70');
		await settle();
		expect(session.snapshot().filter.intensity).toBe(70);
		expect(session.snapshot().canUndo).toBe(false);

		buttonByText('Apply')!.click();
		await settle();

		const after = session.snapshot();
		expect(readPixel(layer.canvas)).toEqual([0, 0, 0, 255]);
		expect(after.undoLabel).toBe('Filter: Ember 70%');
		expect(after.filter.active).toBeNull();
		expect(renderRecipe.mock.calls.some((call) => call[2] === 70)).toBe(true);

		session.undo();
		await settle();
		expect(readPixel(layer.canvas)).toEqual([255, 255, 255, 255]);
		expect(session.snapshot().canUndo).toBe(false);
		session.redo();
		expect(readPixel(layer.canvas)).toEqual([0, 0, 0, 255]);
	});

	it('Reset drops the filter without touching the layer', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Pop')!.click();
		await settle();
		buttonByText('Reset')!.click();
		await settle();
		expect(session.snapshot().filter.active).toBeNull();
		expect(session.snapshot().canUndo).toBe(false);
		expect(readPixel(session.snapshot().layers[0].canvas)).toEqual([255, 255, 255, 255]);
	});
});

describe('Save as filter', () => {
	async function openDialog(): Promise<void> {
		mountComponent(SaveFilterDialog, { isOpen: true });
		await settle();
	}

	it('POSTs the active steps, default intensity and name, then selects the saved filter', async () => {
		createMineFilter.mockResolvedValue(
			item({ id: 'mine:02', name: 'My Ember 2', source: 'mine', owned: true, group: 'Mine', intensity: 70 })
		);
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Ember')!.click();
		await settle();
		session.setFilterIntensity(70);
		session.setFilterStepParam(0, 'temperature', 44);
		session.toggleFilterStep(2);

		await openDialog();
		const name = document.body.querySelector('#save-filter-name') as HTMLInputElement;
		expect(name.value).toBe('My Ember');
		input(name, 'My Ember 2');
		input(document.body.querySelector('#save-filter-description')!, 'Warm');
		await settle();
		buttonByText('Save filter')!.click();
		await settle();

		expect(createMineFilter).toHaveBeenCalledTimes(1);
		expect(createMineFilter).toHaveBeenCalledWith({
			name: 'My Ember 2',
			description: 'Warm',
			intensity: 70,
			steps: [
				{ op: 'white_balance', temperature: 44, tint: 6 },
				{ op: 'tone', contrast: 10, saturation: 8 },
				{ op: 'vignette', amount: 22, midpoint: 55, feather: 60, enabled: false }
			],
			source_id: 'ember'
		});
		expect(listFilters).toHaveBeenCalledTimes(2);
		expect(session.snapshot().filter.active?.id).toBe('mine:02');
	});

	it('shows the server message under the name when the name is taken', async () => {
		createMineFilter.mockRejectedValue(
			apiError(409, 'filter_name_taken', "You already have a filter named 'My Pop'")
		);
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Pop')!.click();
		await settle();
		await openDialog();
		buttonByText('Save filter')!.click();
		await settle();
		const name = document.body.querySelector('#save-filter-name') as HTMLInputElement;
		expect(name.getAttribute('aria-invalid')).toBe('true');
		expect(document.body.querySelector('[role="alert"]')?.textContent).toContain(
			"You already have a filter named 'My Pop'"
		);
		expect(session.snapshot().filter.active?.id).toBe('pop');
	});

	it('shows the limit message without marking the name', async () => {
		createMineFilter.mockRejectedValue(
			apiError(409, 'filter_limit_reached', 'You can keep at most 100 filters; delete one to save another')
		);
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Pop')!.click();
		await settle();
		await openDialog();
		buttonByText('Save filter')!.click();
		await settle();
		const name = document.body.querySelector('#save-filter-name') as HTMLInputElement;
		expect(name.getAttribute('aria-invalid')).not.toBe('true');
		expect(document.body.querySelector('[role="alert"]')?.textContent).toContain('at most 100 filters');
	});

	it('turns a server LUT refusal into the LUT message and blocks saving', async () => {
		createMineFilter.mockRejectedValue(
			apiError(422, 'filter_lut_unsupported', "LUT filters can't be copied yet; only filters made of steps can be saved to My filters")
		);
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Pop')!.click();
		await settle();
		await openDialog();
		buttonByText('Save filter')!.click();
		await settle();
		expect(document.body.querySelector('[data-testid="lut-refusal"]')?.textContent).toContain(
			'only filters made of steps'
		);
		expect(buttonByText('Save filter')!.disabled).toBe(true);
	});

	it('refuses a LUT filter with the README message and never POSTs', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Harbor')!.click();
		await settle();
		expect(getFilterLut).toHaveBeenCalledWith('/api/filters/harbor/lut');
		expect(session.snapshot().filter.active?.hasLut).toBe(true);

		const save = buttonByText('Save as filter')!;
		expect(save.disabled).toBe(true);
		expect(host.querySelector('[data-testid="lut-refusal"]')?.textContent).toContain(
			"LUT filters can't be copied yet"
		);
		save.click();

		await openDialog();
		expect(document.body.querySelector('[data-testid="lut-refusal"]')).not.toBeNull();
		expect(buttonByText('Save filter')!.disabled).toBe(true);
		expect(createMineFilter).not.toHaveBeenCalled();
	});
});

describe('Fine-tune', () => {
	it('opens the steps with eye toggles, edits them, updates a Mine filter and goes back', async () => {
		updateMineFilter.mockResolvedValue(item({ id: 'mine:01', name: 'My Ember', source: 'mine', owned: true, group: 'Mine' }));
		mountComponent(FiltersPanel, { phone: true });
		mountComponent(AdjustPanel);
		await settle();
		byLabel('My Ember')!.click();
		await settle();
		buttonByText('Fine-tune')!.click();
		await settle();

		const snapshot = session.snapshot();
		expect(snapshot.toolId).toBe('adjust');
		expect(snapshot.filter.fineTune).toBe(true);
		expect(byLabel('White balance')).not.toBeNull();
		expect(byLabel('Disable Vignette')).not.toBeNull();

		input(document.body.querySelector('#finetune-0-temperature')!, '50');
		byLabel('Disable Vignette')!.click();
		await settle();
		expect(document.body.textContent).toContain('2 tweaks');

		buttonByText('Update My Ember')!.click();
		await settle();
		expect(updateMineFilter).toHaveBeenCalledWith('mine:01', {
			steps: [
				{ op: 'white_balance', temperature: 50, tint: 6 },
				{ op: 'tone', contrast: 10, saturation: 8 },
				{ op: 'vignette', amount: 22, midpoint: 55, feather: 60, enabled: false }
			],
			intensity: 80
		});
		expect(session.snapshot().filter.fineTune).toBe(false);
		expect(session.snapshot().toolId).toBe('filters');
	});

	it('Reset steps restores the recipe and the back link keeps the filter', async () => {
		mountComponent(FiltersPanel, { phone: true });
		mountComponent(AdjustPanel);
		await settle();
		byLabel('Ember')!.click();
		await settle();
		session.openFineTune();
		session.setFilterStepParam(1, 'contrast', 40);
		await settle();
		buttonByText('Reset steps')!.click();
		await settle();
		expect(session.snapshot().filter.steps).toEqual(EMBER_STEPS);

		buttonByText('Filters')!.click();
		await settle();
		expect(session.snapshot().toolId).toBe('filters');
		expect(session.snapshot().filter.active?.id).toBe('ember');
	});
});

describe('Mine filters', () => {
	async function openMenu() {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		(byLabel('More actions for My Ember') as HTMLButtonElement).click();
		await settle();
	}

	it('only Mine filters get a manage menu', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		expect(byLabel('More actions for My Ember')).not.toBeNull();
		expect(byLabel('More actions for Ember')).toBeNull();
		expect(byLabel('More actions for Harbor')).toBeNull();
	});

	it('renames through the menu with a PATCH', async () => {
		updateMineFilter.mockResolvedValue(item({ id: 'mine:01' }));
		await openMenu();
		(document.body.querySelector('[role="menuitem"]') as HTMLButtonElement).click();
		await settle();

		const rename = document.body.querySelector('input[data-rename]') as HTMLInputElement;
		expect(rename.value).toBe('My Ember');
		input(rename, 'Evening');
		rename.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await settle();
		expect(updateMineFilter).toHaveBeenCalledWith('mine:01', { name: 'Evening' });
	});

	it('keeps the rename open with the server message when the name is taken', async () => {
		updateMineFilter.mockRejectedValue(
			apiError(409, 'filter_name_taken', "You already have a filter named 'Pop'")
		);
		await openMenu();
		(document.body.querySelector('[role="menuitem"]') as HTMLButtonElement).click();
		await settle();

		const rename = document.body.querySelector('input[data-rename]') as HTMLInputElement;
		input(rename, 'Pop');
		rename.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await settle();
		expect(document.body.querySelector('input[data-rename]')).not.toBeNull();
		expect(document.body.querySelector('#filter-rename-error')?.textContent).toContain(
			"You already have a filter named 'Pop'"
		);
	});

	it('asks before deleting, keeps the filter on Cancel and deletes on confirm', async () => {
		deleteMineFilter.mockResolvedValue(undefined);
		await openMenu();
		const items = Array.from(document.body.querySelectorAll('[role="menuitem"]')) as HTMLButtonElement[];
		items.find((entry) => entry.textContent!.includes('Delete'))!.click();
		await settle();

		const confirm = document.body.querySelector('[role="alertdialog"]');
		expect(confirm?.textContent).toContain("Delete My Ember? This can't be undone.");
		expect(deleteMineFilter).not.toHaveBeenCalled();

		Array.from(confirm!.querySelectorAll('button')).find((b) => b.textContent!.trim() === 'Cancel')!.click();
		await settle();
		expect(document.body.querySelector('[role="alertdialog"]')).toBeNull();
		expect(deleteMineFilter).not.toHaveBeenCalled();

		(byLabel('More actions for My Ember') as HTMLButtonElement).click();
		await settle();
		(Array.from(document.body.querySelectorAll('[role="menuitem"]')) as HTMLButtonElement[])
			.find((entry) => entry.textContent!.includes('Delete'))!
			.click();
		await settle();
		Array.from(document.body.querySelector('[role="alertdialog"]')!.querySelectorAll('button'))
			.find((b) => b.textContent!.trim() === 'Delete')!
			.click();
		await settle();
		expect(deleteMineFilter).toHaveBeenCalledWith('mine:01');
	});
});

describe('compare', () => {
	it('the eye button shows the original only while held', async () => {
		mountComponent(FiltersPanel, { phone: true });
		await settle();
		byLabel('Pop')!.click();
		await settle();
		const eye = byLabel('Compare with the original')!;
		eye.dispatchEvent(new Event('pointerdown', { bubbles: true }));
		await settle();
		expect(session.snapshot().filter.compare).toBe(true);
		eye.dispatchEvent(new Event('pointerup', { bubbles: true }));
		await settle();
		expect(session.snapshot().filter.compare).toBe(false);
	});
});
