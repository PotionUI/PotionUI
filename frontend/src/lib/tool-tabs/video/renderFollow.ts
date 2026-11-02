import type { MediaToolRun } from '$lib/media-tools/types';
import { trackRun, type RenderRunApi } from './renderRun';

export interface RenderListener {
	progress: (run: MediaToolRun) => void;
	done: (run: MediaToolRun) => void;
}

interface Entry {
	runId: string;
	controller: AbortController;
	listener: RenderListener | null;
	fallback: (run: MediaToolRun) => void;
	alive: () => boolean;
}

const entries = new Map<string, Entry>();

export function isFollowing(tabId: string, runId?: string): boolean {
	const entry = entries.get(tabId);
	return !!entry && (runId === undefined || entry.runId === runId);
}

export function followRender(
	tabId: string,
	runId: string,
	api: RenderRunApi,
	listener: RenderListener,
	fallback: (run: MediaToolRun) => void,
	alive: () => boolean = () => true
): void {
	const current = entries.get(tabId);
	if (current && current.runId === runId) {
		current.listener = listener;
		current.fallback = fallback;
		current.alive = alive;
		return;
	}
	current?.controller.abort();
	const entry: Entry = { runId, controller: new AbortController(), listener, fallback, alive };
	entries.set(tabId, entry);
	void trackRun(runId, {
		api,
		signal: entry.controller.signal,
		onProgress: (run) => {
			if (!entry.alive()) {
				if (entries.get(tabId) === entry) stopRender(tabId);
				return;
			}
			entry.listener?.progress(run);
		}
	}).then((run) => {
		if (entries.get(tabId) !== entry) return;
		entries.delete(tabId);
		if (!run) return;
		if (entry.listener) entry.listener.done(run);
		else entry.fallback(run);
	});
}

export function detachRender(tabId: string, listener: RenderListener): void {
	const entry = entries.get(tabId);
	if (entry && entry.listener === listener) entry.listener = null;
}

export function settleRender(tabId: string, run: MediaToolRun): boolean {
	const entry = entries.get(tabId);
	if (!entry || entry.runId !== run.run_id) return false;
	entry.controller.abort();
	entries.delete(tabId);
	if (entry.listener) entry.listener.done(run);
	else entry.fallback(run);
	return true;
}

export function stopRender(tabId: string): void {
	entries.get(tabId)?.controller.abort();
	entries.delete(tabId);
}
