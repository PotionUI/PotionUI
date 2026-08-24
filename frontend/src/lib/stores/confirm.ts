import { writable } from 'svelte/store';

export type ConfirmVariant = 'danger' | 'warning' | 'info' | 'success';

export interface ConfirmOptions {
	title?: string;
	message: string;
	variant?: ConfirmVariant;
}

export interface ConfirmRequest extends ConfirmOptions {
	id: number;
}

interface QueueEntry {
	request: ConfirmRequest;
	resolve: (value: boolean) => void;
	settled: boolean;
}

const queue: QueueEntry[] = [];
const current = writable<ConfirmRequest | null>(null);
let nextId = 1;

export const activeConfirm = { subscribe: current.subscribe };

function publishHead() {
	current.set(queue.length > 0 ? queue[0].request : null);
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		queue.push({
			request: { ...options, id: nextId++ },
			resolve,
			settled: false
		});
		if (queue.length === 1) publishHead();
	});
}

export function settleConfirm(id: number, result: boolean) {
	const index = queue.findIndex((entry) => entry.request.id === id);
	if (index === -1) return;
	const [entry] = queue.splice(index, 1);
	if (!entry.settled) {
		entry.settled = true;
		entry.resolve(result);
	}
	publishHead();
}

export function cancelAllConfirms() {
	const pending = queue.splice(0, queue.length);
	for (const entry of pending) {
		if (!entry.settled) {
			entry.settled = true;
			entry.resolve(false);
		}
	}
	publishHead();
}
