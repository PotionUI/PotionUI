import { describe, it, expect } from 'vitest';
import type { IndexModelsResult } from '$lib/services/admin-api';
import type { IndexingStatus } from '$lib/services/api/models';
import {
	backendIndexBusy,
	indexCompletion,
	indexResultMessage,
	isIndexModelsResult,
	isNativeLocalBackend
} from './backendIndexing';

const native = { id: 'n', driver: 'native.local' };
const comfy = { id: 'c', driver: 'comfyui.remote' };

function status(overrides: Partial<IndexingStatus>): IndexingStatus {
	return { state: 'idle', ...overrides };
}

const result: IndexModelsResult = {
	backend_id: 'c',
	listed: 12,
	created: 3,
	matched: 8,
	removed: 1,
	size_conflicts: [],
	digest_conflicts: [],
	duplicates: [],
	ambiguous: ['x']
};

describe('backendIndexBusy', () => {
	it('native-local follows the shared indexing status', () => {
		expect(backendIndexBusy(native, {}, status({ state: 'scanning' }))).toBe(true);
		expect(backendIndexBusy(native, {}, status({ state: 'indexing' }))).toBe(true);
		expect(backendIndexBusy(native, {}, status({ state: 'done', restart_pending: true }))).toBe(true);
		expect(backendIndexBusy(native, {}, status({ state: 'done' }))).toBe(false);
		expect(backendIndexBusy(native, {}, null)).toBe(false);
	});

	it('remote backends ignore the shared status and follow their own call', () => {
		expect(backendIndexBusy(comfy, {}, status({ state: 'scanning' }))).toBe(false);
		expect(backendIndexBusy(comfy, { c: true }, status({ state: 'idle' }))).toBe(true);
		expect(backendIndexBusy(comfy, { n: true }, null)).toBe(false);
	});

	it('an in-flight call keeps native busy before the status catches up', () => {
		expect(backendIndexBusy(native, { n: true }, status({ state: 'done' }))).toBe(true);
	});
});

describe('index results', () => {
	it('recognises a remote result and rejects a coordinator status', () => {
		expect(isIndexModelsResult(result)).toBe(true);
		expect(isIndexModelsResult({ state: 'scanning' })).toBe(false);
		expect(isIndexModelsResult(undefined)).toBe(false);
	});

	it('builds the toast message with warnings', () => {
		expect(indexResultMessage(result, 'Comfy')).toBe(
			'Indexed 12 models on "Comfy" — 3 new, 8 matched, 1 removed (1 warning)'
		);
	});

	it('detects native-local by driver', () => {
		expect(isNativeLocalBackend(native)).toBe(true);
		expect(isNativeLocalBackend(comfy)).toBe(false);
	});
});

describe('indexCompletion', () => {
	it('maps terminal states to a toast', () => {
		expect(indexCompletion(status({ state: 'done', found_on_disk: 5, indexed: 2 }))).toEqual({
			kind: 'success',
			message: 'Indexed 5 model files — 2 new, 3 already indexed.'
		});
		expect(indexCompletion(status({ state: 'done', found_on_disk: 5, indexed: 0 }))?.message).toBe(
			'All 5 model files are up to date.'
		);
		expect(indexCompletion(status({ state: 'failed', error: 'boom' }))).toEqual({ kind: 'error', message: 'boom' });
		expect(indexCompletion(status({ state: 'done', found_on_disk: 0 }))?.kind).toBe('error');
		expect(indexCompletion(status({ state: 'scanning' }))).toBeNull();
	});
});
