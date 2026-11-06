import { writable } from 'svelte/store';

const STORAGE_KEY = 'potionui:loop-videos';

function read(): boolean {
	try {
		return localStorage.getItem(STORAGE_KEY) !== 'off';
	} catch {
		return true;
	}
}

function write(value: boolean): void {
	try {
		localStorage.setItem(STORAGE_KEY, value ? 'on' : 'off');
	} catch {
		return;
	}
}

function createLoopVideos() {
	const { subscribe, update } = writable(read());
	return {
		subscribe,
		toggle(): void {
			update((current) => {
				write(!current);
				return !current;
			});
		}
	};
}

export const loopVideos = createLoopVideos();
