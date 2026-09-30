import type { Backend, IndexModelsResult } from '$lib/services/admin-api';
import type { IndexingStatus } from '$lib/services/api/models';
import { indexingDoneSummary, indexingIsRunning } from '$lib/models-location/indexingDisplay';

export const NATIVE_LOCAL_DRIVER = 'native.local';

export const INDEX_MODELS_ICON = 'scan-search';

export function isNativeLocalBackend(backend: Pick<Backend, 'driver'> | null | undefined): boolean {
	return backend?.driver === NATIVE_LOCAL_DRIVER;
}

export function isIndexModelsResult(data: unknown): data is IndexModelsResult {
	return !!data && typeof data === 'object' && Array.isArray((data as IndexModelsResult).size_conflicts);
}

export function backendIndexBusy(
	backend: Pick<Backend, 'id' | 'driver'>,
	callsInFlight: Record<string, boolean>,
	status: IndexingStatus | null
): boolean {
	if (callsInFlight[backend.id]) return true;
	return isNativeLocalBackend(backend) && indexingIsRunning(status);
}

export function indexResultWarnings(result: IndexModelsResult): number {
	return (
		result.size_conflicts.length +
		result.digest_conflicts.length +
		result.duplicates.length +
		result.ambiguous.length +
		(result.type_mismatches?.length ?? 0)
	);
}

export function indexResultMessage(result: IndexModelsResult, backendName: string): string {
	const warnings = indexResultWarnings(result);
	return (
		`Indexed ${result.listed} models on "${backendName}" — ${result.created} new, ${result.matched} matched, ${result.removed} removed` +
		(warnings > 0 ? ` (${warnings} warning${warnings === 1 ? '' : 's'})` : '')
	);
}

export type IndexCompletion = { kind: 'success' | 'error'; message: string };

export function indexCompletion(status: IndexingStatus | null): IndexCompletion | null {
	if (!status) return null;
	if (status.state === 'failed') return { kind: 'error', message: status.error || 'Indexing failed.' };
	if (status.state === 'blocked') return { kind: 'error', message: status.error || 'A plugin blocked indexing.' };
	const summary = indexingDoneSummary(status);
	if (!summary) return null;
	if (summary.kind === 'zero') return { kind: 'error', message: 'No model files found in the configured location.' };
	if (summary.kind === 'up_to_date') {
		return { kind: 'success', message: `All ${summary.found} model files are up to date.` };
	}
	return {
		kind: 'success',
		message: `Indexed ${summary.found} model files — ${summary.indexed} new, ${summary.alreadyIndexed} already indexed.`
	};
}

export function stopRowEvent(event: Event): void {
	event.stopPropagation();
}
