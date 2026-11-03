import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetModelTypes, setModelTypes } from '$lib/stores/modelTypes.svelte';
import { coreModelTypeDefinitions, modelTypeDefinition } from '$lib/stores/modelTypesFixture';
import {
	modelSourceLabel,
	modelFilenameStem,
	modelSummaryParts,
	modelTagLabels,
	modelIsCloud,
	modelOriginLine,
	modelTypePresentation
} from './modelPresentation';

beforeEach(() => setModelTypes(coreModelTypeDefinitions()));
afterEach(() => resetModelTypes());

describe('modelPresentation', () => {
	it('turns internal model types into user-facing roles', () => {
		expect(modelTypePresentation('checkpoint')).toEqual({
			label: 'Base model',
			purpose: 'Main generation model'
		});
		expect(modelTypePresentation('lora').purpose).toBe('Style or concept adapter');
		expect(modelTypePresentation('text_encoder')).toEqual({
			label: 'Text encoder',
			purpose: 'Converts prompts into guidance'
		});
	});

	it('labels a plugin type with the label the backend serves', () => {
		setModelTypes([
			...coreModelTypeDefinitions(),
			modelTypeDefinition({ id: 'pose_rig', label: 'Pose rig', purpose: 'Skeleton for pose transfer', source: 'pose-plugin' })
		]);

		expect(modelTypePresentation('pose_rig')).toEqual({ label: 'Pose rig', purpose: 'Skeleton for pose transfer' });
		expect(modelSummaryParts({ model_type: 'pose_rig' })[0]).toBe('Pose rig');
	});

	it('shows a type the backend no longer serves as unavailable', () => {
		expect(modelTypePresentation('pose_rig')).toEqual({ label: 'Pose Rig', purpose: 'Type no longer available' });
	});

	it('keeps the needs-a-type wording for undefined models', () => {
		expect(modelTypePresentation('undefined')).toEqual({
			label: 'Needs a type',
			purpose: 'Type not recognised from the file'
		});
	});

	it('does not call a type unavailable before the list has loaded', () => {
		resetModelTypes();

		expect(modelTypePresentation('lora')).toEqual({ label: 'Lora', purpose: 'Generation resource' });
	});

	it('normalizes provider names and deduplicates tags', () => {
		const model = {
			model_type: 'lora',
			tags: [{ name: 'Portrait' }],
			providers: [{ provider: 'civitai-provider', tags: ['portrait', 'cinematic'] }]
		};
		expect(modelSourceLabel(model)).toBe('Civitai');
		expect(modelTagLabels(model)).toEqual(['Portrait', 'cinematic']);
		expect(modelSummaryParts(model)).toEqual(['LoRA', 'Civitai', 'Portrait', 'cinematic']);
	});

	it('shows the model filename without its path or extension', () => {
		expect(modelFilenameStem({ filename: 'models/checkpoints/potion-xl-v2.safetensors' })).toBe(
			'potion-xl-v2'
		);
	});
});

describe('modelOriginLine', () => {
	it('shows the file stem for a model that lives in a file', () => {
		expect(modelOriginLine({ model_type: 'checkpoint', filename: 'models/flux-dev.safetensors' })).toBe('File · flux-dev');
		expect(modelOriginLine({ model_type: 'checkpoint', filename: '' })).toBe('');
	});

	it('names the provider of a cloud model and never its slug', () => {
		const model = { model_type: 'cloud', filename: 'google-veo-3-1', providers: [{ provider: 'cloud.openrouter' }] };
		expect(modelOriginLine(model)).toBe('Cloud · Openrouter');
		expect(modelOriginLine(model)).not.toContain('veo');
	});

	it('shows nothing for a cloud model whose provider is unknown', () => {
		expect(modelOriginLine({ model_type: 'cloud', filename: 'google-veo-3-1' })).toBe('');
		expect(modelIsCloud({ model_type: 'CLOUD' })).toBe(true);
		expect(modelIsCloud({ model_type: 'lora' })).toBe(false);
	});
});

describe('modelSummaryParts for a cloud model', () => {
	it('names the type once and leaves the provider to the origin line', () => {
		const model = { model_type: 'cloud', providers: [{ provider: 'cloud.openrouter', tags: ['google'] }] };
		expect(modelSummaryParts(model)).toEqual(['Cloud', 'google']);
	});
});

describe('modelOriginLine with the listing fields', () => {
	it('uses the provider label and adds the vendor', () => {
		const model = { model_type: 'cloud', filename: 'google-veo-3-1', provider_label: 'OpenRouter', vendor: 'google' };
		expect(modelOriginLine(model)).toBe('Cloud · OpenRouter · google');
	});

	it('prefers the top-level label over the humanised driver', () => {
		const model = { model_type: 'cloud', provider_label: 'OpenRouter', providers: [{ provider: 'cloud.openrouter' }] };
		expect(modelOriginLine(model)).toBe('Cloud · OpenRouter');
	});

	it('falls back to the humanised driver when the label is null', () => {
		const model = { model_type: 'cloud', provider_label: null, vendor: null, providers: [{ provider: 'cloud.openrouter' }] };
		expect(modelOriginLine(model)).toBe('Cloud · Openrouter');
	});

	it('shows the vendor alone when nothing names the provider', () => {
		expect(modelOriginLine({ model_type: 'cloud', vendor: 'google' })).toBe('Cloud · google');
	});
});
