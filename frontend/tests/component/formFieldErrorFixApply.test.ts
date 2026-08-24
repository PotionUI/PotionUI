// @vitest-environment jsdom
//
// Covers the one hop the util-level tests cannot: the chip's click reaching
// DynamicForm's own value channel. `deriveFieldErrorFix`/`applyFieldErrorFix`
// stay green against a fake actions object even if the context is never wired,
// the chip never renders, or `setFieldValue` writes somewhere the form doesn't
// publish — so this mounts the real form and reads the value it hands its
// parent for submission.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { clearFieldError } = await import('$lib/utils/formValidationErrors');
const { default: ResolutionField } = await import(
	'$lib/components/form-fields/ResolutionField.svelte'
);
const { default: SelectField } = await import('$lib/components/form-fields/SelectField.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

const GEOMETRY_MESSAGE =
	'LTX 1.5x upscale of 960x544 is not achievable: the upsampled stage-1 latent lands on a 45x25 grid, ' +
	'but the refine stage is configured for 45x26 (target resolution 1440x816) -- 1.5x only lands on a clean ' +
	'grid when both the width and height are multiples of 64px. Nearest achievable resolution: 960x512. ' +
	'You can also switch Upscale to 2.0x, which is always achievable at any resolution.';

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [
				{
					name: 'resolution',
					type: 'resolution',
					title: 'Resolution',
					default: '960x544',
					options: [
						{ value: '960x544', ratio: [16, 9] },
						{ value: '544x960', ratio: [9, 16] }
					]
				},
				{
					name: 'upscale',
					type: 'select',
					title: 'Upscale',
					default: '1.5x',
					options: [
						{ value: 'off', label: 'Off (single pass)' },
						{ value: '1.5x', label: '1.5x' },
						{ value: '2.0x', label: '2.0x (recommended)' }
					]
				}
			]
		}
	}
};

// The generate page's own wiring, reproduced: a failed submission's field
// errors go in as a prop, and every edit clears that field's entry.
function mountFormWithGeometryError() {
	const target = document.createElement('div');
	document.body.appendChild(target);

	let fieldErrors: Record<string, string[]> = {
		resolution: [GEOMETRY_MESSAGE],
		upscale: [GEOMETRY_MESSAGE]
	};
	let published: Record<string, unknown> = {};
	const edited: string[] = [];
	let form: { $set: (props: Record<string, unknown>) => void; $destroy: () => void };

	form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: {
			presetId: `ltx-${Math.random()}`,
			mode: 'video',
			initialData: { resolution: '960x544', upscale: '1.5x' },
			fieldErrors,
			onFormDataChange: (data: Record<string, unknown>) => {
				published = data;
			},
			onFieldEdit: (name: string) => {
				edited.push(name);
				fieldErrors = clearFieldError(fieldErrors, name);
				form.$set({ fieldErrors });
			}
		}
	});

	return {
		target,
		form,
		edited,
		errorsOf: () => fieldErrors,
		publishedValueOf: (name: string) => published[name],
		chipOf: (fieldName: string) =>
			target.querySelector<HTMLButtonElement>(
				`[data-field-name="${fieldName}"] button[aria-label]`
			),
		destroy: () => {
			form.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountFormWithGeometryError> | undefined;

registerFieldComponent('resolution', { component: ResolutionField });
registerFieldComponent('select', { component: SelectField });
registerFieldComponent('row', { component: RowField });

beforeEach(() => {
	// jsdom implements it on neither Element nor its prototype, and DynamicForm
	// scrolls the first erroring field into view on every new error batch.
	Element.prototype.scrollIntoView = () => {};
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { form_schema: FORM_SCHEMA }
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('field error quick-fix in a mounted form', () => {
	it('labels each linked field with the concrete value it would apply', async () => {
		mounted = mountFormWithGeometryError();
		await settle();

		expect(mounted.chipOf('resolution')?.getAttribute('aria-label')).toBe('Use 960×512');
		expect(mounted.chipOf('upscale')?.getAttribute('aria-label')).toBe('Switch to 2.0x');
		expect(mounted.chipOf('resolution')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'Use 960×512'
		);
	});

	it('publishes the suggested resolution as the submitted form value when clicked', async () => {
		mounted = mountFormWithGeometryError();
		await settle();
		expect(mounted.publishedValueOf('resolution')).toBe('960x544');

		mounted.chipOf('resolution')!.click();
		await settle();

		expect(mounted.publishedValueOf('resolution')).toBe('960x512');
		expect(mounted.publishedValueOf('upscale')).toBe('1.5x');
	});

	it('publishes the safe factor as the submitted form value when clicked', async () => {
		mounted = mountFormWithGeometryError();
		await settle();

		mounted.chipOf('upscale')!.click();
		await settle();

		expect(mounted.publishedValueOf('upscale')).toBe('2.0x');
		expect(mounted.publishedValueOf('resolution')).toBe('960x544');
	});

	it('clears the error off both linked fields, so neither chip lingers', async () => {
		mounted = mountFormWithGeometryError();
		await settle();

		mounted.chipOf('resolution')!.click();
		await settle();

		expect(mounted.edited).toEqual(expect.arrayContaining(['resolution', 'upscale']));
		expect(mounted.errorsOf()).toEqual({});
		expect(mounted.chipOf('resolution')).toBeNull();
		expect(mounted.chipOf('upscale')).toBeNull();
	});

	it('offers no chip for a field error carrying no suggestion', async () => {
		mounted = mountFormWithGeometryError();
		await settle();
		mounted.form.$set({ fieldErrors: { resolution: ['Resolution must be divisible by 32.'] } });
		await settle();

		expect(mounted.target.querySelector('[data-field-name="resolution"] p')?.textContent).toBe(
			'Resolution must be divisible by 32.'
		);
		expect(mounted.chipOf('resolution')).toBeNull();
	});

	it('keeps the assertive region to the message text, with the action beside it', async () => {
		mounted = mountFormWithGeometryError();
		await settle();

		const chip = mounted.chipOf('resolution')!;
		expect(chip.tagName).toBe('BUTTON');
		expect(chip.closest('[role="alert"]')).toBeNull();
		expect(
			chip.parentElement?.querySelector('[role="alert"] p')?.textContent
		).toBe(GEOMETRY_MESSAGE);
	});
});
