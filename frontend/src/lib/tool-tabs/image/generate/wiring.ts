import { get } from 'svelte/store';
import { checkSlotValue, loadCloudCapabilities, loadPresetFormSchema, searchAutopickSlot } from '$lib/form/formSources';
import { sharedCapabilityCache } from '$lib/form/capabilityTracker';
import { missingModelIn, seedFormValues } from '$lib/form/formStateSeed';
import { formAudienceStore } from '$lib/stores/formAudience';
import { api } from '$lib/services/api/index';
import { loadPresets } from '$lib/stores/presetsCatalog';
import { createGenerationSocket } from '$lib/services/websocket';
import { createGenerateController, type ControllerDeps, type GenerateController } from './controller';
import { syncServerTasks, syncVideoTasks } from './register';
import { createRunEngine, type RunMessage, type RunSocket } from './runEngine';

const capabilityOf = (modelId: string) => sharedCapabilityCache.get(modelId);

function runSocket(): RunSocket {
	const socket = createGenerationSocket();
	socket.connect();
	return {
		subscribe(generationId, handler) {
			const forward = (message: unknown) => handler(message as RunMessage);
			socket.subscribe(generationId, forward as never);
			return () => socket.unsubscribe(generationId, forward as never);
		},
		connected: () => socket.isConnected(),
		onConnection: (listener) => socket.onConnectionChange(listener),
		close: () => socket.disconnect()
	};
}

export type TabGenerateDeps = Pick<ControllerDeps, 'getStack' | 'setStack' | 'source' | 'editLink' | 'landRegion' | 'areaSize' | 'pictureSize' | 'stepsBeneath' | 'range' | 'inputFor' | 'basisOf' | 'applyMatte' | 'landPicture' | 'spawnEdit' | 'saveSpawned' | 'beneath'>;

export function createTabGenerate(deps: TabGenerateDeps, editor: 'image' | 'video' = 'image'): GenerateController {
	let controller!: GenerateController;
	const engine = createRunEngine(
		{
			start: (task, body) => api.startEditorRun(task, body),
			get: (id) => api.getEditorRun(id),
			cancel: (id) => api.cancelEditorRun(id),
			socket: runSocket
		},
		{ onSettled: (view) => controller.settle(view) }
	);
	controller = createGenerateController({
		...deps,
		engine,
		editor,
		onTasks: editor === 'video' ? syncVideoTasks : syncServerTasks,
		ports: {
			tasks: () => api.listEditorTasks(editor),
			sessions: (presetId) => api.listEditorSessions(presetId),
			formulas: async (presetId, mode) => {
				const response = await api.listFormulas(presetId, mode);
				return (response.success && response.data ? response.data : []).map((entry) => ({ id: entry.id, name: entry.name, values: entry.values ?? {} }));
			},
			schema: (presetId, mode) => loadPresetFormSchema(presetId, mode, undefined),
				presets: async () => {
					const response = await loadPresets();
					return response.success && response.data ? response.data : [];
				},
				pinSession: async (id, pinned) => void (await api.setSessionPinned(id, pinned)),
				deleteSession: async (id) => void (await api.deleteSession(id)),
				versions: async (id) => {
					const response = await api.getSessionVersions(id);
					return response.success && response.data ? response.data : [];
				},
				version: async (id, number) => {
					const response = await api.getSessionVersion(id, number);
					return response.success && response.data ? response.data : null;
				},
			seed: ({ presetId, schema, incoming }) =>
				seedFormValues(
					{ schema, presetId, tabId: `${editor}-editor-setup`, incoming: incoming as Record<string, any>, audience: get(formAudienceStore), videoDirectorActive: false },
					{ search: searchAutopickSlot, check: checkSlotValue, loadCapabilities: loadCloudCapabilities, capabilityOf }
				),
			missingModel: (schema, values) => missingModelIn(schema, values as Record<string, any>, capabilityOf),
			setDefault: async (task, body) => void (await api.setEditorTaskDefault(task, body)),
			clearDefault: (task) => api.clearEditorTaskDefault(task),
			saveSession: (body) => api.saveEditorSession(body),
			updateSession: (id, body) => api.updateEditorSession(id, body)
		}
	});
	return controller;
}
