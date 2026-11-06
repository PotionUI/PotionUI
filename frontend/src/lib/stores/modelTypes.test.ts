import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { getModelTypeDefinitions } = vi.hoisted(() => ({ getModelTypeDefinitions: vi.fn() }));

vi.mock('$lib/services/api/index', () => ({ api: { getModelTypeDefinitions } }));

import {
	assignableModelTypes,
	depotModelTypes,
	findModelType,
	modelTypeDefinitions,
	modelTypesLoaded,
	resetModelTypes,
	setModelTypes
} from './modelTypes.svelte';
import { refreshModelTypes } from './modelTypesLoader';
import { coreModelTypeDefinitions, modelTypeDefinition } from './modelTypesFixture';

const POSE_RIG = modelTypeDefinition({ id: 'pose_rig', label: 'Pose rig', folder: 'pose_rigs', source: 'pose-plugin' });

describe('model type store', () => {
	beforeEach(() => resetModelTypes());
	afterEach(() => {
		resetModelTypes();
		getModelTypeDefinitions.mockReset();
	});

	it('starts empty and unloaded', () => {
		expect(modelTypeDefinitions()).toEqual([]);
		expect(modelTypesLoaded()).toBe(false);
		expect(assignableModelTypes()).toEqual([]);
	});

	it('serves the definitions it was given, including plugin types', () => {
		setModelTypes([...coreModelTypeDefinitions(), POSE_RIG]);

		expect(modelTypesLoaded()).toBe(true);
		expect(findModelType('pose_rig')?.label).toBe('Pose rig');
		expect(findModelType('nothing')).toBeUndefined();
	});

	it('lists assignable types without virtual or unassignable ones', () => {
		setModelTypes([...coreModelTypeDefinitions(), POSE_RIG]);

		expect(assignableModelTypes()).toEqual(['checkpoint', 'diffusion_model', 'lora', 'text_encoder', 'pose_rig']);
		expect(depotModelTypes()).toContain('llm');
		expect(depotModelTypes()).not.toContain('cloud');
	});

	it('loads the list served by the backend', async () => {
		getModelTypeDefinitions.mockResolvedValue({ success: true, data: { types: [...coreModelTypeDefinitions(), POSE_RIG] } });

		await refreshModelTypes();

		expect(getModelTypeDefinitions).toHaveBeenCalledTimes(1);
		expect(findModelType('pose_rig')?.source).toBe('pose-plugin');
	});

	it('drops a type the backend no longer serves', async () => {
		setModelTypes([...coreModelTypeDefinitions(), POSE_RIG]);
		getModelTypeDefinitions.mockResolvedValue({ success: true, data: { types: coreModelTypeDefinitions() } });

		await refreshModelTypes();

		expect(findModelType('pose_rig')).toBeUndefined();
		expect(findModelType('checkpoint')?.label).toBe('Base model');
	});

	it('keeps the previous list when the request fails', async () => {
		setModelTypes([...coreModelTypeDefinitions(), POSE_RIG]);
		getModelTypeDefinitions.mockRejectedValue(new Error('offline'));

		await refreshModelTypes();

		expect(findModelType('pose_rig')?.label).toBe('Pose rig');
	});

	it('keeps the previous list when the response is unsuccessful', async () => {
		setModelTypes(coreModelTypeDefinitions());
		getModelTypeDefinitions.mockResolvedValue({ success: false, message: 'nope' });

		await refreshModelTypes();

		expect(modelTypeDefinitions()).toHaveLength(coreModelTypeDefinitions().length);
	});
});
