import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { PresetFormOverrideField } from '$lib/types/api';
import {
	overrideEditorKind,
	pendingOverrideFrom,
	effectiveEditable,
	isOverrideEmpty,
	isOverrideUnchanged,
	buildOverridesPayload,
	buildFieldConfigIndex,
	canUseRichEditor,
	toComponentValue,
	fromComponentValue,
	rawEditorHint,
	groupFieldsByTab,
	type PendingOverride
} from './presetFormOverrides';

const registeredTypes = new Set<string>();
vi.mock('$lib/fields/registry', () => ({
	hasFieldComponent: (type: string) => registeredTypes.has(type)
}));

function field(overrides: Partial<PresetFormOverrideField> = {}): PresetFormOverrideField {
	return {
		name: 'steps',
		label: 'Steps',
		type: 'number',
		preset_default: 20,
		override: null,
		tab: null,
		...overrides
	};
}

describe('overrideEditorKind', () => {
	it('picks boolean for checkbox/boolean types', () => {
		expect(overrideEditorKind({ type: 'boolean' })).toBe('boolean');
		expect(overrideEditorKind({ type: 'checkbox' })).toBe('boolean');
	});

	it('picks number for number/integer/slider types', () => {
		expect(overrideEditorKind({ type: 'number' })).toBe('number');
		expect(overrideEditorKind({ type: 'integer' })).toBe('number');
		expect(overrideEditorKind({ type: 'slider' })).toBe('number');
	});

	it('picks select only when options are present', () => {
		expect(overrideEditorKind({ type: 'select', options: [{ label: 'A', value: 'a' }] })).toBe('select');
		expect(overrideEditorKind({ type: 'select', options: [] })).toBe('text');
		expect(overrideEditorKind({ type: 'select' })).toBe('text');
	});

	it('falls back to text for unknown/complex types', () => {
		expect(overrideEditorKind({ type: 'model' })).toBe('text');
		expect(overrideEditorKind({ type: 'seed' })).toBe('text');
	});
});

describe('pendingOverrideFrom', () => {
	it('defaults editable/visible to true and default to the preset default when there is no override', () => {
		const pending = pendingOverrideFrom(field({ preset_default: 20, override: null }));
		expect(pending).toEqual({ hasDefault: false, default: 20, editable: true, visible: true });
	});

	it('seeds from a saved override, including a falsy default value', () => {
		const pending = pendingOverrideFrom(
			field({ preset_default: 20, override: { default: 0, editable: false, visible: true } })
		);
		expect(pending).toEqual({ hasDefault: true, default: 0, editable: false, visible: true });
	});
});

describe('effectiveEditable', () => {
	it('is false whenever visible is off, regardless of the editable flag', () => {
		expect(effectiveEditable({ editable: true, visible: false })).toBe(false);
		expect(effectiveEditable({ editable: false, visible: false })).toBe(false);
	});

	it('mirrors editable when visible', () => {
		expect(effectiveEditable({ editable: true, visible: true })).toBe(true);
		expect(effectiveEditable({ editable: false, visible: true })).toBe(false);
	});
});

describe('isOverrideEmpty', () => {
	it('is true for the implicit no-override state', () => {
		expect(isOverrideEmpty({ hasDefault: false, default: undefined, editable: true, visible: true })).toBe(true);
	});

	it('is false once a default override or a non-default editable/visible is set', () => {
		expect(isOverrideEmpty({ hasDefault: true, default: 5, editable: true, visible: true })).toBe(false);
		expect(isOverrideEmpty({ hasDefault: false, default: undefined, editable: false, visible: true })).toBe(false);
		expect(isOverrideEmpty({ hasDefault: false, default: undefined, editable: true, visible: false })).toBe(false);
	});
});

describe('isOverrideUnchanged', () => {
	it('is true when a primitive default still matches the saved override (unchanged)', () => {
		const f = field({ preset_default: 20, override: { default: 20, editable: true, visible: true } });
		const pending: PendingOverride = { hasDefault: true, default: 20, editable: true, visible: true };
		expect(isOverrideUnchanged(f, pending)).toBe(true);
	});

	it('is false when a primitive default actually differs', () => {
		const f = field({ preset_default: 20, override: { default: 20, editable: true, visible: true } });
		const pending: PendingOverride = { hasDefault: true, default: 30, editable: true, visible: true };
		expect(isOverrideUnchanged(f, pending)).toBe(false);
	});

	it('is true for an array-valued default (checkbox_group/lora_picker) that matches by content, not by reference', () => {
		// A fresh array instance with the SAME contents - what the rich editor's
		// onChange hands back on every edit, even a no-op one. A bare `===` would
		// wrongly report this row as dirty forever; see the doc comment on
		// `isOverrideUnchanged` for why `valuesEqual` is used instead.
		const f = field({
			type: 'checkbox_group',
			preset_default: [],
			override: { default: ['a', 'b'], editable: true, visible: true }
		});
		const pending: PendingOverride = { hasDefault: true, default: ['a', 'b'], editable: true, visible: true };
		expect(isOverrideUnchanged(f, pending)).toBe(true);
	});

	it('is false for an array-valued default whose contents actually differ', () => {
		const f = field({
			type: 'checkbox_group',
			preset_default: [],
			override: { default: ['a', 'b'], editable: true, visible: true }
		});
		const pending: PendingOverride = { hasDefault: true, default: ['a', 'c'], editable: true, visible: true };
		expect(isOverrideUnchanged(f, pending)).toBe(false);
	});

	it('is true for an object-valued default (e.g. a resolution field) that matches by content', () => {
		const f = field({
			type: 'resolution',
			preset_default: { width: 1024, height: 1024 },
			override: null
		});
		const pending: PendingOverride = {
			hasDefault: false,
			default: { width: 1024, height: 1024 },
			editable: true,
			visible: true
		};
		expect(isOverrideUnchanged(f, pending)).toBe(true);
	});
});

describe('buildOverridesPayload', () => {
	it('omits fields whose pending state matches the saved override', () => {
		const f = field({ override: { default: 5, editable: true, visible: true } });
		const pending: PendingOverride = { hasDefault: true, default: 5, editable: true, visible: true };
		expect(buildOverridesPayload([f], { steps: pending })).toEqual({});
	});

	it('sends null to clear a field back to no override', () => {
		const f = field({ override: { default: 5, editable: true, visible: true } });
		const pending: PendingOverride = { hasDefault: false, default: undefined, editable: true, visible: true };
		expect(buildOverridesPayload([f], { steps: pending })).toEqual({ steps: null });
	});

	it('sends a patch carrying only the effective values for a changed field', () => {
		const f = field({ override: null });
		const pending: PendingOverride = { hasDefault: true, default: 30, editable: true, visible: true };
		expect(buildOverridesPayload([f], { steps: pending })).toEqual({
			steps: { default: 30, editable: true, visible: true }
		});
	});

	it('forces editable: false in the payload when visible is off, even if editable was left on', () => {
		const f = field({ override: null });
		const pending: PendingOverride = { hasDefault: false, default: undefined, editable: true, visible: false };
		expect(buildOverridesPayload([f], { steps: pending })).toEqual({
			steps: { editable: false, visible: false }
		});
	});

	it('skips fields with no pending state at all', () => {
		const f = field();
		expect(buildOverridesPayload([f], {})).toEqual({});
	});
});

describe('buildFieldConfigIndex', () => {
	it('flattens a nested form schema tree by field name', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{ type: 'model', name: 'checkpoint', configuration: { model_type: 'checkpoint' } },
						{
							type: 'row',
							children: [
								{ type: 'slider', name: 'steps', configuration: { min: 1, max: 100 } },
								{ type: 'select', name: 'sampler', options: [{ label: 'Euler', value: 'euler' }] }
							]
						}
					]
				}
			}
		};

		const index = buildFieldConfigIndex(schema);
		expect(Object.keys(index).sort()).toEqual(['checkpoint', 'sampler', 'steps']);
		expect(index.checkpoint.configuration).toEqual({ model_type: 'checkpoint' });
		expect(index.steps.configuration).toEqual({ min: 1, max: 100 });
		expect((index.sampler as { options?: unknown }).options).toEqual([{ label: 'Euler', value: 'euler' }]);
	});

	it('returns an empty index for a missing/empty schema', () => {
		expect(buildFieldConfigIndex(null)).toEqual({});
		expect(buildFieldConfigIndex({})).toEqual({});
	});

	it('first-seen wins on a duplicate field name', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{ type: 'number', name: 'dup', configuration: { first: true } },
						{ type: 'number', name: 'dup', configuration: { first: false } }
					]
				}
			}
		};
		expect(buildFieldConfigIndex(schema).dup.configuration).toEqual({ first: true });
	});
});

describe('canUseRichEditor', () => {
	beforeEach(() => {
		registeredTypes.clear();
		// Every type used below has a real registered component (builtin.ts
		// registers all of them) - registration alone must NOT be enough to
		// qualify for the rich editor; only `RICH_EDITOR_TYPES` membership does.
		registeredTypes.add('model');
		registeredTypes.add('slider');
		registeredTypes.add('select');
		registeredTypes.add('checkbox_group');
		registeredTypes.add('resolution');
		registeredTypes.add('image');
		registeredTypes.add('lora_picker');
		registeredTypes.add('carousel');
		registeredTypes.add('prompt_timeline');
		registeredTypes.add('llm');
		registeredTypes.add('group');
		registeredTypes.add('tabs');
	});

	it('is true when a component is registered, config metadata exists, and the type is on the allow-list', () => {
		expect(canUseRichEditor('model', { type: 'model', name: 'checkpoint' })).toBe(true);
		expect(canUseRichEditor('slider', { type: 'slider', name: 'steps' })).toBe(true);
		expect(canUseRichEditor('select', { type: 'select', name: 'sampler' })).toBe(true);
		expect(canUseRichEditor('checkbox_group', { type: 'checkbox_group', name: 'styles' })).toBe(true);
		expect(canUseRichEditor('resolution', { type: 'resolution', name: 'size' })).toBe(true);
	});

	it('is true for lora_picker: it renders its own real, self-contained picker (LoraPickerField), same as model', () => {
		expect(canUseRichEditor('lora_picker', { type: 'lora_picker', name: 'loras' })).toBe(true);
	});

	it('is false without config metadata (field hidden by an override, or a non-default variant)', () => {
		expect(canUseRichEditor('model', undefined)).toBe(false);
		expect(canUseRichEditor('lora_picker', undefined)).toBe(false);
	});

	it('is false for media upload types even with config metadata and a registered component', () => {
		expect(canUseRichEditor('image', { type: 'image', name: 'init_image' })).toBe(false);
	});

	it('is false when no component is registered for the type', () => {
		expect(canUseRichEditor('some_plugin_type', { type: 'some_plugin_type', name: 'x' })).toBe(false);
	});

	it('falls back for composite/list editors NOT on the allow-list even when a component is registered and config exists', () => {
		expect(canUseRichEditor('carousel', { type: 'carousel', name: 'style_gallery' })).toBe(false);
	});

	it('falls back for multi-part composite editors (prompt_timeline, llm) even when registered and config exists', () => {
		expect(canUseRichEditor('prompt_timeline', { type: 'prompt_timeline', name: 'timeline' })).toBe(false);
		expect(canUseRichEditor('llm', { type: 'llm', name: 'assistant' })).toBe(false);
	});

	it('falls back for named containers/display-only types even when registered and config exists', () => {
		expect(canUseRichEditor('group', { type: 'group', name: 'advanced' })).toBe(false);
		expect(canUseRichEditor('tabs', { type: 'tabs', name: 'sections' })).toBe(false);
	});
});

describe('toComponentValue / fromComponentValue', () => {
	it('round-trips a model ref through the {modelPath, tagFilters} component shape', () => {
		const wire = 'model:01KEVZXJAPH81SSR8RJ2P80XA0';
		const componentValue = toComponentValue('model', wire);
		expect(componentValue).toEqual({ modelPath: wire, tagFilters: [] });
		expect(fromComponentValue('model', componentValue)).toBe(wire);
	});

	it('treats a non-string model default (e.g. undefined/null) as an empty picker', () => {
		expect(toComponentValue('model', null)).toEqual({ modelPath: '', tagFilters: [] });
		expect(toComponentValue('models', undefined)).toEqual({ modelPath: '', tagFilters: [] });
	});

	it('recovers an empty string when the model component value is cleared', () => {
		expect(fromComponentValue('model', null)).toBe('');
		expect(fromComponentValue('model', { modelPath: '', tagFilters: [] })).toBe('');
	});

	it('passes every other field type straight through unchanged', () => {
		expect(toComponentValue('slider', 30)).toBe(30);
		expect(fromComponentValue('slider', 30)).toBe(30);
		expect(toComponentValue('checkbox_group', ['a', 'b'])).toEqual(['a', 'b']);
		expect(fromComponentValue('select', 'euler')).toBe('euler');
	});

	it('passes a lora_picker array straight through - its onChange value IS the wire shape already', () => {
		const rows = [{ model: 'model:01KEVZXJAPH81SSR8RJ2P80XA0', strength: 0.8 }];
		expect(toComponentValue('lora_picker', rows)).toEqual(rows);
		expect(fromComponentValue('lora_picker', rows)).toEqual(rows);
	});
});

describe('hydrating a persisted override into the rich editor', () => {
	beforeEach(() => {
		registeredTypes.clear();
		registeredTypes.add('model');
	});

	// Captured from the real serializer's output for the `diffusion_model` field
	// with a stored override applied, not hand-written. Only `default` differs
	// from the no-override case; every other key is identical.
	const realDiffusionModelSchemaEntryWithOverride = {
		type: 'model',
		title: 'Diffusion Model (NaDiT)',
		description: null,
		name: 'diffusion_model',
		default: 'model:01KX5KHV5CYSVN6T9KH8SY2MG9',
		audience: 'simple',
		configuration: {
			model_type: 'diffusion_model',
			placeholder: 'Select the SeedVR2 3B NaDiT checkpoint...',
			searchable: true,
			display_provider_image: true,
			allow_info_modal: true,
			recommendations: null,
			filter_tags: null
		},
		preset_id: '01KXB7C553THYMSMKY1QSYESFM'
	} as const;

	it('a saved model override reaches the exact same rich-editor state a fresh pick would (real backend shape)', () => {
		const wire = 'model:01KX5KHV5CYSVN6T9KH8SY2MG9';
		const f = field({
			name: 'diffusion_model',
			type: 'model',
			preset_default: 'models/diffusion_models/seedvr2_ema_3b_fp16.safetensors',
			override: { default: wire, editable: true, visible: true }
		});
		// What PresetFormOverridesTab.svelte does on load: seed `pending` from the
		// saved override, then look up the field's real config (from the SAME
		// real schema entry above) and ask canUseRichEditor.
		const pending = pendingOverrideFrom(f);
		expect(pending.default).toBe(wire);

		expect(canUseRichEditor('model', realDiffusionModelSchemaEntryWithOverride)).toBe(true);

		// The value handed to <ModelField> is the same {modelPath, tagFilters}
		// shape a fresh selection produces - not the raw wire string.
		expect(toComponentValue('model', pending.default)).toEqual({ modelPath: wire, tagFilters: [] });
	});

	// `canUseRichEditor` returns false whenever `hasFieldComponent` reads the
	// module-level field registry before it's been populated (a module-registration
	// timing race on a fresh load), NOT because of the config shape. The registry
	// isn't reactive, so the decision never re-evaluates once registration finishes;
	// the fix registers components synchronously at the component's module-eval time.
	it('documents that canUseRichEditor is only ever false-until-registered because hasFieldComponent is unregistered - not because of the real config shape', () => {
		registeredTypes.clear(); // simulates the race: registration hasn't run yet
		expect(canUseRichEditor('model', realDiffusionModelSchemaEntryWithOverride)).toBe(false);
		registeredTypes.add('model'); // what PresetFormOverridesTab.svelte's own registerBuiltinFieldComponents() call guarantees before first render
		expect(canUseRichEditor('model', realDiffusionModelSchemaEntryWithOverride)).toBe(true);
	});
});

describe('groupFieldsByTab', () => {
	it('returns no groups for a flat form (no tabs)', () => {
		const fields = [field({ name: 'a', tab: null }), field({ name: 'b', tab: null })];
		expect(groupFieldsByTab(fields, [])).toEqual([]);
	});

	it('groups fields under their tab, preserving tabs declaration order', () => {
		const fields = [
			field({ name: 'sampler', tab: 'Sampling' }),
			field({ name: 'checkpoint', tab: 'Model' }),
			field({ name: 'steps', tab: 'Sampling' })
		];
		const groups = groupFieldsByTab(fields, ['Model', 'Sampling']);
		expect(groups.map((g) => g.label)).toEqual(['Model', 'Sampling']);
		expect(groups[0].fields.map((f) => f.name)).toEqual(['checkpoint']);
		expect(groups[1].fields.map((f) => f.name)).toEqual(['sampler', 'steps']);
	});

	it('appends a trailing General group only when a tabbed form has untabbed fields', () => {
		const withUntabbed = [field({ name: 'sampler', tab: 'Sampling' }), field({ name: 'seed', tab: null })];
		const groupsWithGeneral = groupFieldsByTab(withUntabbed, ['Sampling']);
		expect(groupsWithGeneral.map((g) => g.label)).toEqual(['Sampling', 'General']);
		expect(groupsWithGeneral[1].fields.map((f) => f.name)).toEqual(['seed']);

		const allTabbed = [field({ name: 'sampler', tab: 'Sampling' })];
		expect(groupFieldsByTab(allTabbed, ['Sampling']).map((g) => g.label)).toEqual(['Sampling']);
	});

	it('routes a field whose tab label has no matching entry in tabs into General', () => {
		const fields = [field({ name: 'sampler', tab: 'Sampling' }), field({ name: 'orphan', tab: 'Nonexistent' })];
		const groups = groupFieldsByTab(fields, ['Sampling']);
		expect(groups.map((g) => g.label)).toEqual(['Sampling', 'General']);
		expect(groups[1].fields.map((f) => f.name)).toEqual(['orphan']);
	});
});

describe('rawEditorHint', () => {
	it('gives a plain-words hint for composite/complex types kept on the raw fallback', () => {
		expect(rawEditorHint('carousel')).toMatch(/value/i);
		expect(rawEditorHint('prompt_timeline')).toBeTruthy();
		expect(rawEditorHint('llm')).toBeTruthy();
		expect(rawEditorHint('image')).toBeTruthy();
		expect(rawEditorHint('group')).toBeTruthy();
	});

	it('is undefined for simple types where the raw fallback already is a reasonable direct edit', () => {
		expect(rawEditorHint('string')).toBeUndefined();
		expect(rawEditorHint('seed')).toBeUndefined();
		expect(rawEditorHint('some_plugin_type')).toBeUndefined();
	});
});
