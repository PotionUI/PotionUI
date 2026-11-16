import { loadPresetFormSchema } from '$lib/form/formSources';
import { api } from '$lib/services/api/index';
import { createGenerationSocket } from '$lib/services/websocket';
import { createGenerateController, type ControllerDeps, type GenerateController } from './controller';
import { syncServerTasks } from './register';
import { createRunEngine, type RunMessage, type RunSocket } from './runEngine';

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

function storage(): Pick<Storage, 'getItem' | 'setItem'> | undefined {
	try {
		return localStorage;
	} catch {
		return undefined;
	}
}

export type TabGenerateDeps = Pick<ControllerDeps, 'getStack' | 'setStack' | 'source' | 'editLink' | 'landRegion' | 'areaSize' | 'stepsBeneath'>;

export function createTabGenerate(deps: TabGenerateDeps): GenerateController {
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
		storage: storage(),
		onTasks: syncServerTasks,
		ports: {
			tasks: () => api.listEditorTasks('image'),
			sessions: (presetId) => api.listEditorSessions(presetId),
			formulas: async (presetId, mode) => {
				const response = await api.listFormulas(presetId, mode);
				return (response.success && response.data ? response.data : []).map((entry) => ({ id: entry.id, name: entry.name, values: entry.values ?? {} }));
			},
			schema: (presetId, mode, variant) => loadPresetFormSchema(presetId, mode, variant ?? undefined),
			setDefault: async (task, body) => void (await api.setEditorTaskDefault(task, body)),
			clearDefault: (task) => api.clearEditorTaskDefault(task)
		}
	});
	return controller;
}
