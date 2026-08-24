import { writable } from 'svelte/store';
import { randomUUID } from '$lib/utils/uuid';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
	id: string;
	type: ToastType;
	message: string;
	title?: string;
	duration?: number;
}

interface ShowOptions {
	title?: string;
	duration?: number;
}

function createToastStore() {
	const { subscribe, update } = writable<Toast[]>([]);

	function push(type: ToastType, message: string, options: ShowOptions = {}) {
		const id = randomUUID();
		const duration = options.duration ?? 4000;
		update((toasts) => [...toasts, { id, type, message, title: options.title, duration }]);
		if (duration > 0) {
			setTimeout(() => remove(id), duration);
		}
		return id;
	}

	function remove(id: string) {
		update((toasts) => toasts.filter((t) => t.id !== id));
	}

	return {
		subscribe,
		show: (type: ToastType, message: string, options?: ShowOptions) => push(type, message, options),
		success: (msg: string, duration?: number) => push('success', msg, { duration }),
		error: (msg: string, duration?: number) => push('error', msg, { duration: duration ?? 6000 }),
		info: (msg: string, duration?: number) => push('info', msg, { duration }),
		warning: (msg: string, duration?: number) => push('warning', msg, { duration }),
		remove
	};
}

export const toasts = createToastStore();
