import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers the workbench_update handler under test.

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function seedCurrentGeneration(tabId: string, generationId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		generation: {
			...tab.generation,
			currentGeneration: { generation_id: generationId, status: 'running' }
		}
	});
}

function currentGeneration(tabId: string): any {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!.generation.currentGeneration;
}

describe('workbench_update message handler - mesh', () => {
	beforeEach(() => tabsStore.reset());

	it('sets current_mesh and mesh_metadata for a final (non-temporary) mesh', () => {
		const tabId = defaultTabId();
		seedCurrentGeneration(tabId, 'gen-mesh-1');

		dispatchGenerationMessage(
			{
				type: 'workbench_update',
				generation_id: 'gen-mesh-1',
				pipe_id: 3,
				pipe_name: 'generator_mesh',
				output_type: 'mesh',
				file_type: 'mesh',
				mesh_format: 'glb',
				temporary: false,
				derived: false,
				seed: 4242,
				vertex_count: 3,
				face_count: 1,
				path: '/api/media/generations/gen-mesh-1/0.glb',
				mesh_name: '0.glb'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const gen = currentGeneration(tabId);
		expect(gen.current_mesh).toBe('/api/media/generations/gen-mesh-1/0.glb');
		expect(gen.file_type).toBe('mesh');
		expect(gen.current_image).toBeNull();
		expect(gen.current_video).toBeNull();
		expect(gen.current_audio).toBeNull();
		expect(gen.mesh_metadata).toEqual({
			format: 'glb',
			filename: '0.glb',
			vertex_count: 3,
			face_count: 1,
			seed: 4242,
			temporary: false,
			derived: false
		});
	});

	it('sets current_mesh from the tmp route for a temporary mesh', () => {
		const tabId = defaultTabId();
		seedCurrentGeneration(tabId, 'gen-mesh-2');

		dispatchGenerationMessage(
			{
				type: 'workbench_update',
				generation_id: 'gen-mesh-2',
				file_type: 'mesh',
				mesh_format: 'glb',
				temporary: true,
				path: '/api/media/tmp/gen-mesh-2/0.glb',
				mesh_name: '0.glb'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const gen = currentGeneration(tabId);
		expect(gen.current_mesh).toBe('/api/media/tmp/gen-mesh-2/0.glb');
		expect(gen.mesh_metadata.temporary).toBe(true);
	});

	it('ignores an unsaved mesh update that carries file_type but no path', () => {
		const tabId = defaultTabId();
		seedCurrentGeneration(tabId, 'gen-mesh-3');

		dispatchGenerationMessage(
			{
				type: 'workbench_update',
				generation_id: 'gen-mesh-3',
				file_type: 'mesh'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const gen = currentGeneration(tabId);
		expect(gen.current_mesh).toBeUndefined();
		expect(gen.file_type).toBeUndefined();
	});

	it('does not mistake a mesh update for a video update', () => {
		const tabId = defaultTabId();
		seedCurrentGeneration(tabId, 'gen-mesh-4');

		dispatchGenerationMessage(
			{
				type: 'workbench_update',
				generation_id: 'gen-mesh-4',
				file_type: 'mesh',
				path: '/api/media/generations/gen-mesh-4/0.glb'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const gen = currentGeneration(tabId);
		expect(gen.current_video).toBeNull();
		expect(gen.file_type).toBe('mesh');
	});
});
