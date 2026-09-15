import { writable } from 'svelte/store';
import { randomUUID } from '$lib/utils/uuid';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface ToastAction {
	label: string;
	onClick: () => void;
}

export interface Toast {
	id: string;
	type: ToastType;
	message: string;
	title?: string;
	duration?: number;
	count?: number;
	action?: ToastAction;
}

interface ShowOptions {
	title?: string;
	duration?: number;
	action?: ToastAction;
}

const MAX_VISIBLE_TOASTS = 3;

function groupKey(type: ToastType, title?: string) {
	return `${type}:${title ?? ''}`;
}

export function capToasts(list: Toast[], max = MAX_VISIBLE_TOASTS): { visible: Toast[]; overflowCount: number } {
	if (list.length <= max) return { visible: list, overflowCount: 0 };
	return { visible: list.slice(0, max), overflowCount: list.length - max };
}

export function toastDisplayTitle(toast: Toast): string | undefined {
	if (!toast.count || toast.count <= 1) return toast.title;
	return `${toast.count}× ${toast.title ?? toast.message}`;
}

export function toastsAriaLive(list: Toast[]): 'assertive' | 'polite' {
	return list.some((t) => t.type === 'error') ? 'assertive' : 'polite';
}

function createToastStore() {
	const { subscribe, update } = writable<Toast[]>([]);
	const timers = new Map<string, ReturnType<typeof setTimeout>>();

	function scheduleRemoval(id: string, duration: number) {
		const existing = timers.get(id);
		if (existing) clearTimeout(existing);
		if (duration > 0) {
			timers.set(
				id,
				setTimeout(() => remove(id), duration)
			);
		} else {
			timers.delete(id);
		}
	}

	function push(type: ToastType, message: string, options: ShowOptions = {}) {
		const duration = options.duration ?? 4000;
		const key = groupKey(type, options.title);
		let id = '';
		update((list) => {
			const idx = list.findIndex((t) => groupKey(t.type, t.title) === key);
			if (idx !== -1) {
				id = list[idx].id;
				const next = [...list];
				next[idx] = {
					...list[idx],
					count: (list[idx].count ?? 1) + 1,
					message,
					duration,
					action: options.action ?? list[idx].action
				};
				return next;
			}
			id = randomUUID();
			return [...list, { id, type, message, title: options.title, duration, count: 1, action: options.action }];
		});
		scheduleRemoval(id, duration);
		return id;
	}

	function remove(id: string) {
		const timer = timers.get(id);
		if (timer) {
			clearTimeout(timer);
			timers.delete(id);
		}
		update((list) => list.filter((t) => t.id !== id));
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
