import { get, writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { KitDirectory } from '$lib/kits/kitChips';
import type { KitDetail } from '$lib/kits/types';

const directory = writable<KitDirectory>(new Map());

export const kitDirectory = { subscribe: directory.subscribe };

export const kitTotal = writable<number | null>(null);

const inflight = new Map<string, Promise<void>>();

function statusOf(error: unknown): number | null {
	const status = (error as { response?: { status?: number } } | null)?.response?.status;
	return typeof status === 'number' ? status : null;
}

function put(id: string, kit: KitDetail | null): void {
	directory.update((current) => {
		const next = new Map(current);
		next.set(id, kit);
		return next;
	});
}

export function rememberKit(kit: KitDetail): void {
	put(kit.id, kit);
}

export function forgetKit(id: string): void {
	put(id, null);
}

async function fetchKit(id: string): Promise<void> {
	try {
		const response = await api.getKit(id);
		if (response.success && response.data) put(id, response.data);
		else if (!get(directory).has(id)) put(id, null);
	} catch (error) {
		if (statusOf(error) === 404) put(id, null);
	}
}

export function ensureKits(ids: readonly string[], options: { refresh?: boolean } = {}): Promise<void> {
	const known = get(directory);
	const wanted = [...new Set(ids)].filter((id) => id && (options.refresh || !known.has(id)));
	const pending = wanted.map((id) => {
		const running = inflight.get(id);
		if (running) return running;
		const task = fetchKit(id).finally(() => inflight.delete(id));
		inflight.set(id, task);
		return task;
	});
	return Promise.all(pending).then(() => undefined);
}

export async function loadKitTotal(): Promise<void> {
	try {
		const response = await api.listKits({ page_size: 1 });
		if (response.success && response.data) kitTotal.set(response.data.total);
	} catch {
		return;
	}
}

export function resetKitDirectory(): void {
	inflight.clear();
	directory.set(new Map());
}

export const kitsRevision = writable(0);

export function bumpKits(): void {
	kitsRevision.update((value) => value + 1);
	void loadKitTotal();
}
