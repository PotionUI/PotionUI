// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { CloudCapabilities } from '$lib/form/capabilityBinder';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));
vi.mock('$lib/services/cloudCapabilities', () => ({
	fetchCloudCapabilities: vi.fn()
}));

const { api } = await import('$lib/services/api/index');
const { fetchCloudCapabilities } = await import('$lib/services/cloudCapabilities');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { sharedCapabilityCache } = await import('$lib/form/capabilityTracker');
const { default: SelectField } = await import('$lib/components/form-fields/SelectField.svelte');
const { default: SliderField } = await import('$lib/components/form-fields/SliderField.svelte');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: CheckboxField } = await import('$lib/components/form-fields/CheckboxField.svelte');
const { default: CloudOptionsField } = await import('$lib/components/form-fields/CloudOptionsField.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: CloudModelStub } = await import('./stubs/CloudModelStub.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

Element.prototype.scrollIntoView = () => {};
registerFieldComponent('row', { component: RowField });
registerFieldComponent('model', { component: CloudModelStub });
registerFieldComponent('select', { component: SelectField });
registerFieldComponent('slider', { component: SliderField });
registerFieldComponent('string', { component: TextInput });
registerFieldComponent('image', { component: TextInput });
registerFieldComponent('checkbox', { component: CheckboxField });
registerFieldComponent('cloud_options', { component: CloudOptionsField, ownsErrors: true });

const SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [
				{ name: 'model', type: 'model', title: 'Model', default: 'model:a', configuration: { model_type: 'cloud', tasks: ['txt2img'] } },
				{ name: 'prompt', type: 'string', title: 'Prompt', default: '' },
				{
					name: 'aspect_ratio',
					type: 'select',
					title: 'Aspect ratio',
					default: '21:9',
					options: [
						{ label: 'Ultra', value: '21:9' },
						{ label: 'Square', value: '1:1' },
						{ label: 'Wide', value: '16:9' }
					],
					capability: { model_field: 'model', param: 'aspect_ratio' }
				},
				{
					name: 'guidance',
					type: 'slider',
					title: 'Guidance',
					default: 50,
					minimum: 0,
					maximum: 100,
					capability: { model_field: 'model', param: 'guidance' }
				},
				{
					name: 'references',
					type: 'image',
					title: 'References',
					default: 'ref.png',
					capability: { model_field: 'model', input: 'reference' }
				},
				{
					name: 'provider_options',
					type: 'cloud_options',
					title: 'Provider options',
					capability: { model_field: 'model' },
					configuration: { include_unbound: false }
				}
			]
		}
	}
};

function capsFor(id: string): CloudCapabilities {
	if (id === 'a') {
		return {
			model_id: 'a',
			params: [
				{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9'], default: '1:1' },
				{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10, step: 0.5, default: 5 },
				{ name: 'x.hdr', kind: 'boolean', extra: true, label: 'HDR', description: 'Render in high dynamic range.' },
				{ name: 'x.note', kind: 'text', extra: true, label: 'Note' }
			],
			inputs: [{ role: 'reference', modality: 'image', max_items: 3 }]
		};
	}
	if (id === 'd') {
		return {
			model_id: 'd',
			params: [
				{ name: 'aspect_ratio', kind: 'enum', values: ['4:3', '3:4'], default: '4:3' },
				{ name: 'guidance', kind: 'range', minimum: 0, maximum: 3, integer: true, default: 1 },
				{ name: 'x.note', kind: 'text', extra: true }
			],
			inputs: []
		};
	}
	if (id === 'c') {
		return {
			model_id: 'c',
			params: [
				{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '4:3'], default: '4:3' },
				{ name: 'guidance', kind: 'range', minimum: 0, maximum: 3, integer: true, default: 2 }
			],
			inputs: []
		};
	}
	if (id === 'b') return { model_id: 'b', params: [], inputs: [] };
	return { model_id: id, params: [], inputs: [] };
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountForm(extra: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	let published: Record<string, any> = {};
	const log: Array<Record<string, any>> = [];
	let form: { $set: (props: Record<string, unknown>) => void; $destroy: () => void };
	form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: {
			presetId: `cloud-${Math.random()}`,
			mode: 'txt2img',
			onFormDataChange: (data: Record<string, any>) => {
				published = data;
				log.push(data);
				queueMicrotask(() => form.$set({ initialData: data }));
			},
			...extra
		}
	});
	const field = (name: string) => target.querySelector<HTMLElement>(`[data-field-name="${name}"]`);
	return {
		target,
		form,
		published: () => published,
		log,
		field,
		chooseModel: async (value: string) => {
			const select = target.querySelector<HTMLSelectElement>('select[aria-label="Model"]')!;
			select.value = value;
			select.dispatchEvent(new Event('change', { bubbles: true }));
			await settle();
		},
		destroy: () => {
			form.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountForm> | undefined;

beforeEach(() => {
	clearSchemaCache();
	sharedCapabilityCache.clear();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({ success: true, data: { preset_id: 'p', form_schema: SCHEMA } } as never);
	vi.mocked(fetchCloudCapabilities).mockImplementation(async (id: string) => capsFor(id));
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('Capability-bound fields', () => {
	it('shows the aspect ratio with only the options the model offers, and hides it for a model without one', async () => {
		mounted = mountForm();
		await settle();

		expect(fetchCloudCapabilities).toHaveBeenCalledWith('a');
		expect(mounted.field('aspect_ratio')).toBeTruthy();
		mounted.field('aspect_ratio')!.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.click();
		await settle();
		const labels = Array.from(document.querySelectorAll('[role="option"]')).map((el) => el.textContent?.trim());
		expect(labels).toEqual(['Square', 'Wide']);
		document.body.click();

		await mounted.chooseModel('model:b');

		expect(mounted.field('aspect_ratio')).toBeNull();
		expect(mounted.field('guidance')).toBeNull();
		expect(mounted.field('references')).toBeNull();
		expect(mounted.published()).not.toHaveProperty('aspect_ratio');
		expect(mounted.published()).not.toHaveProperty('guidance');
		expect(mounted.published()).not.toHaveProperty('references');
		expect(mounted.published().model).toBe('model:b');
	});

	it('resets a value the model does not offer to its default', async () => {
		mounted = mountForm();
		await settle();

		expect(mounted.published().aspect_ratio).toBe('1:1');
		expect(mounted.published().guidance).toBe(5);
		expect(mounted.published().references).toBe('ref.png');
	});

	it('constrains a range to the model', async () => {
		mounted = mountForm();
		await settle();

		const slider = mounted.field('guidance')!.querySelector<HTMLInputElement>('input[type="range"]')!;
		expect([slider.min, slider.max, slider.step]).toEqual(['1', '10', '0.5']);
	});

	it('brings the fields back when the model that has them is chosen again', async () => {
		mounted = mountForm();
		await settle();
		await mounted.chooseModel('model:b');
		expect(mounted.field('aspect_ratio')).toBeNull();

		await mounted.chooseModel('model:a');

		expect(mounted.field('aspect_ratio')).toBeTruthy();
		expect(mounted.published().aspect_ratio).toBe('1:1');
		expect(fetchCloudCapabilities).toHaveBeenCalledTimes(2);
	});

	it('ignores a slow answer for a model that is no longer chosen', async () => {
		const slow = deferred<CloudCapabilities>();
		vi.mocked(fetchCloudCapabilities).mockImplementation((id: string) =>
			id === 'a' ? slow.promise : Promise.resolve(capsFor(id))
		);
		mounted = mountForm();
		await settle();
		await mounted.chooseModel('model:b');
		expect(mounted.field('aspect_ratio')).toBeNull();

		slow.resolve(capsFor('a'));
		await settle();

		expect(mounted.field('aspect_ratio')).toBeNull();
		expect(mounted.published()).not.toHaveProperty('aspect_ratio');
	});

	it('shows the fields as declared when no model is chosen or the request fails', async () => {
		vi.mocked(fetchCloudCapabilities).mockRejectedValue(new Error('404'));
		mounted = mountForm();
		await settle();

		expect(mounted.field('aspect_ratio')).toBeTruthy();
		expect(mounted.field('guidance')).toBeTruthy();
		expect(mounted.published().aspect_ratio).toBe('21:9');

		await mounted.chooseModel('');
		expect(mounted.field('aspect_ratio')).toBeTruthy();
	});
});

describe('Defaults of the chosen model', () => {
	function bareSchema() {
		const schema = JSON.parse(JSON.stringify(SCHEMA));
		const fields = schema.properties.generation.children;
		for (const field of fields) if (field.name === 'aspect_ratio' || field.name === 'guidance') delete field.default;
		return schema;
	}

	beforeEach(() => {
		vi.mocked(api.getPresetFormSchema).mockResolvedValue({ success: true, data: { preset_id: 'p', form_schema: bareSchema() } } as never);
	});

	it('shows the model default on an untouched form', async () => {
		mounted = mountForm();
		await settle();

		expect(mounted.published().aspect_ratio).toBe('1:1');
		expect(mounted.published().guidance).toBe(5);
		expect(mounted.field('aspect_ratio')!.textContent).toContain('Square');
	});

	it('keeps a valid pick when switching to a model that also offers it', async () => {
		mounted = mountForm({ initialData: { aspect_ratio: '1:1', guidance: 2 } });
		await settle();
		sharedCapabilityCache.set('c', capsFor('c'));

		await mounted.chooseModel('model:c');

		expect(mounted.published().aspect_ratio).toBe('1:1');
		expect(mounted.published().guidance).toBe(2);
	});

	it('resets to the new default when the new model does not offer the pick', async () => {
		mounted = mountForm({ initialData: { aspect_ratio: '16:9', guidance: 7 } });
		await settle();
		sharedCapabilityCache.set('c', capsFor('c'));

		await mounted.chooseModel('model:c');

		expect(mounted.published().aspect_ratio).toBe('4:3');
		expect(mounted.published().guidance).toBe(2);
	});
});

describe('Switching between cached models', () => {
	it('keeps the values of a session loaded with another model', async () => {
		mounted = mountForm();
		await settle();
		sharedCapabilityCache.set('d', capsFor('d'));

		mounted.form.$set({
			initialData: {
				model: 'model:d',
				aspect_ratio: '3:4',
				guidance: 2,
				provider_options: { 'x.note': 'kept' }
			}
		});
		await settle();

		expect(mounted.published().aspect_ratio).toBe('3:4');
		expect(mounted.published().guidance).toBe(2);
		expect(mounted.published().provider_options).toEqual({ 'x.note': 'kept' });
		expect(mounted.field('aspect_ratio')).toBeTruthy();
	});

	it('applies the new model rules at once and never the old ones', async () => {
		mounted = mountForm({ initialData: { aspect_ratio: '16:9', guidance: 7 } });
		await settle();
		expect(mounted.published().aspect_ratio).toBe('16:9');
		sharedCapabilityCache.set('d', capsFor('d'));
		const before = mounted.log.length;

		await mounted.chooseModel('model:d');

		const after = mounted.log.slice(before);
		expect(after.length).toBeGreaterThan(0);
		for (const data of after) {
			expect(data.aspect_ratio).not.toBe('1:1');
			expect(data.guidance).not.toBe(5);
		}
		expect(mounted.published().aspect_ratio).toBe('4:3');
		expect(mounted.published().guidance).toBe(1);
		expect(mounted.field('references')).toBeNull();
	});
});

describe('Late echoes of the published data', () => {
	it('do not undo a model change made after they were published', async () => {
		mounted = mountForm();
		await settle();
		const stale = mounted.log[0];

		await mounted.chooseModel('model:b');
		mounted.form.$set({ initialData: stale });
		await settle();

		expect(mounted.published().model).toBe('model:b');
		expect(mounted.field('aspect_ratio')).toBeNull();
	});
});

describe('Cache lifetime', () => {
	it('asks for the capabilities again once they are old, so catalog changes show up', async () => {
		mounted = mountForm();
		await settle();
		expect(fetchCloudCapabilities).toHaveBeenCalledTimes(1);

		sharedCapabilityCache.expireAll();
		const prompt = mounted.field('prompt')!.querySelector<HTMLInputElement>('input')!;
		prompt.value = 'changed';
		prompt.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(fetchCloudCapabilities).toHaveBeenCalledTimes(2);
		expect(mounted.field('aspect_ratio')).toBeTruthy();
	});
});

describe('Cloud options field', () => {
	it('renders the extras and submits them keyed by their full names', async () => {
		mounted = mountForm();
		await settle();

		const options = mounted.field('provider_options')!;
		expect(options.textContent).toContain('HDR');
		expect(options.textContent).toContain('Note');
		expect(options.textContent).not.toContain('Aspect ratio');

		const hdr = options.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
		hdr.checked = true;
		hdr.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		const note = options.querySelector<HTMLInputElement>('input[type="text"], input:not([type])')!;
		note.value = 'moody';
		note.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.published().provider_options).toEqual({ 'x.hdr': true, 'x.note': 'moody' });
	});

	it('removes a key when its value is cleared', async () => {
		mounted = mountForm({ initialData: { provider_options: { 'x.note': 'moody' } } });
		await settle();

		const note = mounted.field('provider_options')!.querySelector<HTMLInputElement>('input[type="text"], input:not([type])')!;
		note.value = '';
		note.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.published().provider_options).toEqual({});
	});

	it('does not send options of the previous model while the new one is still loading', async () => {
		const slow = deferred<CloudCapabilities>();
		vi.mocked(fetchCloudCapabilities).mockImplementation((id: string) =>
			id === 'd' ? slow.promise : Promise.resolve(capsFor(id))
		);
		mounted = mountForm({ initialData: { provider_options: { 'x.note': 'moody' } } });
		await settle();
		expect(mounted.published().provider_options).toEqual({ 'x.note': 'moody' });

		await mounted.chooseModel('model:d');
		expect(mounted.published()).not.toHaveProperty('provider_options');

		slow.resolve(capsFor('d'));
		await settle();
		expect(mounted.field('provider_options')).toBeTruthy();
	});

	it('shows nothing, not an empty box, when the model has no extras', async () => {
		mounted = mountForm();
		await settle();
		await mounted.chooseModel('model:b');

		expect(mounted.field('provider_options')).toBeNull();
		expect(mounted.target.querySelector('[data-testid="cloud-options"]')).toBeNull();
	});

	it('drops option values the new model does not offer', async () => {
		mounted = mountForm({ initialData: { provider_options: { 'x.note': 'moody' } } });
		await settle();
		expect(mounted.published().provider_options).toEqual({ 'x.note': 'moody' });

		await mounted.chooseModel('model:b');

		expect(mounted.published()).not.toHaveProperty('provider_options');
	});
});

describe('Server field errors', () => {
	it('puts errors on the bound field and on the matching option control', async () => {
		mounted = mountForm({
			fieldErrors: {
				aspect_ratio: ["'21:9' is not offered by this model. Choose one of: 1:1, 16:9"],
				provider_options: ['x.note: must be shorter', 'an unrelated option problem']
			}
		});
		await settle();

		expect(mounted.field('aspect_ratio')!.textContent).toContain("'21:9' is not offered by this model.");
		const note = mounted.target.querySelector('[data-option-name="x.note"]')!;
		expect(note.textContent).toContain('must be shorter');
		expect(note.textContent).not.toContain('x.note:');
		const hdr = mounted.target.querySelector('[data-option-name="x.hdr"]')!;
		expect(hdr.textContent).not.toContain('must be shorter');
		expect(mounted.field('provider_options')!.textContent).toContain('an unrelated option problem');
		expect(mounted.target.textContent!.split('must be shorter').length - 1).toBe(1);
	});
});
