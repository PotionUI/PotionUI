import { api } from '$lib/services/api/index';

let labels = $state<Record<string, string>>({});
let loaded = false;
let inFlight: Promise<void> | null = null;

export function fallbackModeLabel(key: string): string {
	return key
		.split(/[_\-\s]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

export function setModeLabels(map: Record<string, string>): void {
	loaded = true;
	labels = { ...map };
}

export function loadModeLabels(): Promise<void> {
	if (loaded) return Promise.resolve();
	if (inFlight) return inFlight;
	inFlight = (async () => {
		try {
			const response = await api.getModeLabels();
			if (response.success && response.data?.labels) {
				labels = { ...response.data.labels };
				loaded = true;
			}
		} catch {
			return;
		} finally {
			inFlight = null;
		}
	})();
	return inFlight;
}

export function modeLabel(key: string | null | undefined, served?: string | null): string {
	if (served?.trim()) return served.trim();
	if (!key) return '';
	return labels[key.toLowerCase()] ?? (fallbackModeLabel(key) || key);
}
