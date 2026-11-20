import { writable } from 'svelte/store';
import {
	loadMediaToolCatalog,
	isMediaToolCatalogLoaded,
	mediaToolCatalog
} from '$lib/media-tools/catalog';
import { toasts } from '$lib/stores/toast';
import { syncCatalogTools } from './catalogTools';

export const CATALOG_LOAD_FAILED_MESSAGE = "Editing tools couldn't load";

export const mediaToolRegistrations = writable(0);

let mirroring = false;
let failed = false;
let retried = false;

function mirrorCatalog(): void {
	if (mirroring) return;
	mirroring = true;
	mediaToolCatalog.subscribe((tools) => {
		syncCatalogTools(tools);
		mediaToolRegistrations.update((tick) => tick + 1);
	});
}

function catalogLoaded(): boolean {
	return isMediaToolCatalogLoaded();
}

export async function loadPluginMediaTools(): Promise<void> {
	mirrorCatalog();
	if (failed) return;
	await loadMediaToolCatalog();
	if (catalogLoaded()) {
		failed = false;
		retried = false;
		return;
	}
	const first = !failed;
	failed = true;
	retried = false;
	if (first) toasts.show('warning', CATALOG_LOAD_FAILED_MESSAGE);
}

export async function retryMediaToolCatalog(): Promise<void> {
	if (!failed || retried) return;
	retried = true;
	await loadMediaToolCatalog();
	if (catalogLoaded()) failed = false;
}
