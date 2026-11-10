import { api } from '$lib/services/api/index';
import { getApiErrorMessage } from '$lib/utils/logger';

export type PluginCatalogStatus =
	| 'available'
	| 'update'
	| 'installed'
	| 'older'
	| 'unreadable'
	| 'bundled'
	| 'conflict'
	| 'incompatible'
	| 'unreleased';

export interface PluginCatalogEntry {
	id: string;
	name: string;
	version: string;
	description: string;
	license: string;
	requires_core: string;
	archive: string | null;
	sha256: string | null;
	status: PluginCatalogStatus;
	reason: string;
	installed_version: string | null;
}

export interface PluginCatalogProblem {
	index: number;
	plugin_id: string | null;
	reason: string;
}

export interface PluginCatalog {
	url: string;
	entries: PluginCatalogEntry[];
	problems: PluginCatalogProblem[];
}

export interface PluginCatalogInstallResult {
	plugin_id: string;
	version: string;
	previous_version: string | null;
	enabled: boolean;
	dependencies_python: string[];
}

const CATALOG_URL_KEY = 'potionui.admin.pluginCatalogUrl';

const STATUS_LABELS: Record<PluginCatalogStatus, string> = {
	available: 'Available',
	update: 'Update available',
	installed: 'Installed',
	older: 'Older than installed',
	unreadable: 'Versions not comparable',
	bundled: 'Ships with PotionUI',
	conflict: 'Installed elsewhere',
	incompatible: 'Incompatible',
	unreleased: 'Not released'
};

export function catalogStatusLabel(status: PluginCatalogStatus): string {
	return STATUS_LABELS[status] ?? status;
}

export function catalogStatusTone(status: PluginCatalogStatus): 'success' | 'info' | 'warning' | 'neutral' {
	if (status === 'available') return 'info';
	if (status === 'update') return 'warning';
	if (status === 'installed') return 'success';
	return 'neutral';
}

export function catalogActionLabel(entry: PluginCatalogEntry): string | null {
	if (entry.status === 'available') return 'Install';
	if (entry.status === 'update') return `Update to ${entry.version}`;
	return null;
}

export function rememberedCatalogUrl(): string {
	try {
		return localStorage.getItem(CATALOG_URL_KEY) ?? '';
	} catch {
		return '';
	}
}

export function rememberCatalogUrl(url: string): void {
	try {
		localStorage.setItem(CATALOG_URL_KEY, url);
	} catch {
		return;
	}
}

async function post<T>(path: string, body: unknown, fallback: string): Promise<T> {
	try {
		const response = await api.getClient().post(path, body, { timeout: 0 });
		if (!response.data?.success) throw new Error(response.data?.message || fallback);
		return response.data.data as T;
	} catch (error) {
		throw new Error(getApiErrorMessage(error, fallback));
	}
}

export function readPluginCatalog(url: string): Promise<PluginCatalog> {
	return post<PluginCatalog>('/api/plugins/catalog', { url }, 'Failed to read the plugin catalog');
}

export function installFromCatalog(
	url: string,
	entry: Pick<PluginCatalogEntry, 'id' | 'version' | 'sha256'>
): Promise<PluginCatalogInstallResult> {
	return post<PluginCatalogInstallResult>(
		'/api/plugins/catalog/install',
		{ url, plugin_id: entry.id, version: entry.version, sha256: entry.sha256 },
		'Failed to install the plugin'
	);
}
