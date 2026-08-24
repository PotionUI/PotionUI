import { writable, type Readable } from 'svelte/store';

export interface FlashMessage {
	message: Readable<string | null>;
	flash: (text: string) => void;
}

// Transient status text (e.g. toolbar action feedback) that clears itself after
// `durationMs`. Each call restarts the timer so a fast follow-up message replaces
// the pending clear instead of racing it.
export function createFlashMessage(durationMs = 3000): FlashMessage {
	const { subscribe, set } = writable<string | null>(null);
	let timer: ReturnType<typeof setTimeout> | undefined;

	function flash(text: string) {
		set(text);
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => set(null), durationMs);
	}

	return { message: { subscribe }, flash };
}
