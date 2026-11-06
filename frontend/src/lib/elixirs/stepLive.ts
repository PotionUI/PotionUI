import type { GenerationState, GenerationTab } from '$lib/types/tabs';
import { mapGenerationFiles, type RestoredGenerationData } from '$lib/utils/generationOrchestrator';
import { buildActiveCompletionPatch } from '$lib/generation/restore/reconcile';
import type { Invocation, Run } from './types';

export interface StepGeneration {
	id: string;
	status: 'running' | 'completed' | 'failed';
	inputKey: string;
	at: number;
}

const RUNNING = new Set(['submitted', 'running']);
const FAILED = new Set(['failed', 'cancelled', 'interrupted']);

function stamp(item: Invocation): number {
	const parsed = Date.parse(item.submitted_at ?? item.finished_at ?? '');
	return Number.isFinite(parsed) ? parsed : 0;
}

export function stepGenerations(run: Run | null | undefined, nodeId: string): StepGeneration[] {
	if (!run?.invocations) return [];
	const latest = new Map<string, Invocation>();
	for (const item of run.invocations) {
		if (item.node_id !== nodeId || !item.generation_id || item.status === 'superseded') continue;
		const key = `${item.edge_id ?? ''}\u0000${item.input_key}`;
		const seen = latest.get(key);
		if (!seen || item.attempt > seen.attempt) latest.set(key, item);
	}
	return [...latest.values()]
		.sort((a, b) => stamp(b) - stamp(a) || b.ordinal - a.ordinal)
		.flatMap((item) => {
			const status = RUNNING.has(item.status) ? 'running' : item.status === 'completed' ? 'completed' : FAILED.has(item.status) ? 'failed' : null;
			return status ? [{ id: item.generation_id as string, status, inputKey: item.input_key, at: stamp(item) }] : [];
		});
}

export interface StepLiveDeps {
	readTab(): GenerationTab | undefined;
	updateTab(patch: Partial<GenerationTab>): void;
	attach(generationIds: string[]): Promise<boolean>;
	files(generationId: string): Promise<unknown[] | null>;
}

export interface StepLive {
	sync(items: StepGeneration[]): Promise<void>;
}

function merge(parts: RestoredGenerationData[]): RestoredGenerationData {
	const images = parts.flatMap((part) => part.images);
	const videos = parts.flatMap((part) => part.videos);
	const audios = parts.flatMap((part) => part.audios);
	const meshes = parts.flatMap((part) => part.meshes);
	return { images, videos, audios, meshes, totalItems: images.length + videos.length + audios.length + meshes.length };
}

export function createStepLive(deps: StepLiveDeps): StepLive {
	const attached = new Set<string>();
	const hydrated = new Map<string, RestoredGenerationData>();
	let chain: Promise<void> = Promise.resolve();

	async function run(items: StepGeneration[]): Promise<void> {
		if (!deps.readTab()) return;
		const idle = (() => {
			const tab = deps.readTab();
			if (!tab || tab.activeGenerationId) return false;
			const generation = tab.generation;
			return generation.batchImages.length + generation.batchVideos.length + generation.batchAudios.length + (generation.batchMeshes?.length ?? 0) === 0;
		})();
		const completed = items.filter((item) => item.status === 'completed' && (!attached.has(item.id) || idle));
		const running = items.filter((item) => item.status === 'running' && !attached.has(item.id));
		const failed = items.filter((item) => item.status === 'failed' && !attached.has(item.id));

		let grew = false;
		for (const item of completed) {
			if (hydrated.has(item.id)) continue;
			const files = await deps.files(item.id);
			if (files) {
				hydrated.set(item.id, mapGenerationFiles(files, item.id));
				grew = true;
			}
		}
		const shown = completed.filter((item) => hydrated.has(item.id));
		if (grew && shown.length > 0 && !deps.readTab()?.activeGenerationId) {
			const patch: Partial<GenerationState> = buildActiveCompletionPatch(shown[0].id, merge(shown.map((item) => hydrated.get(item.id)!)));
			const tab = deps.readTab();
			if (tab) deps.updateTab({ generation: { ...tab.generation, ...patch } });
		}

		if (running.length > 0) {
			const tab = deps.readTab();
			if (!tab) return;
			const claimed = !tab.activeGenerationId;
			if (claimed) deps.updateTab({ activeGenerationId: running[0].id });
			if (await deps.attach(running.map((item) => item.id))) {
				for (const item of running) attached.add(item.id);
			} else if (claimed) {
				deps.updateTab({ activeGenerationId: null });
			}
			return;
		}

		if (shown.length === 0 && failed.length > 0 && !deps.readTab()?.activeGenerationId && hydrated.size === 0) {
			deps.updateTab({ activeGenerationId: failed[0].id });
			if (await deps.attach([failed[0].id])) attached.add(failed[0].id);
			else deps.updateTab({ activeGenerationId: null });
		}
	}

	return {
		sync(items) {
			chain = chain.then(() => run(items)).catch(() => undefined);
			return chain;
		}
	};
}
