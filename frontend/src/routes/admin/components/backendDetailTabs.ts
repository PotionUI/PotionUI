// The two built-in `native` drivers (see backend_config.py) - both are core,
// not plugin-contributed, so naming them here isn't the kind of plugin-name
// hardcoding CLAUDE.md forbids.
const NATIVE_LOCAL_DRIVER = 'native.local';
const NATIVE_REMOTE_DRIVER = 'native.remote';

export type BackendDetailTabId = 'overview' | 'infrastructure' | 'models' | 'optimizations' | 'stats';

export interface BackendDetailTabDescriptor {
	id: BackendDetailTabId;
	label: string;
	icon: string;
}

/**
 * Detail-pane tabs for a backend, keyed by its driver.
 *  - `native.remote` (a headless worker this installation dispatches to) gets
 *    its own Infrastructure tab (provision/stop/terminate compute) and Models
 *    tab (sync the worker's model depot) - both are meaningless for a driver
 *    that owns no compute of its own to provision or depot to sync.
 *  - `native.local` (the in-process, auto-provisioned driver) keeps its
 *    Optimizations tab.
 *  - Every other engine (comfyui, ...) gets just Overview and Stats -
 *    provisioning is restricted server-side to `native.remote`
 *    (`src/features/provisioning/operations.py`), so `BackendInfrastructureSection`
 *    never renders anything for another driver.
 */
export function backendDetailTabsFor(driver: string): BackendDetailTabDescriptor[] {
	if (driver === NATIVE_REMOTE_DRIVER) {
		return [
			{ id: 'overview', label: 'Overview', icon: 'info' },
			{ id: 'infrastructure', label: 'Infrastructure', icon: 'server' },
			{ id: 'models', label: 'Models', icon: 'cube' },
			{ id: 'stats', label: 'Stats', icon: 'gauge' }
		];
	}
	if (driver === NATIVE_LOCAL_DRIVER) {
		return [
			{ id: 'overview', label: 'Overview', icon: 'info' },
			{ id: 'optimizations', label: 'Optimizations', icon: 'sliders' },
			{ id: 'stats', label: 'Stats', icon: 'gauge' }
		];
	}
	return [
		{ id: 'overview', label: 'Overview', icon: 'info' },
		{ id: 'stats', label: 'Stats', icon: 'gauge' }
	];
}

/** True when `tab` is one of `driver`'s own tabs - used to fall back to
 * Overview when the previously-selected backend's tab doesn't exist on the
 * newly-selected one (e.g. leaving a native.remote's Infrastructure tab for
 * a comfyui backend). */
export function isBackendDetailTab(driver: string, tab: string): boolean {
	return backendDetailTabsFor(driver).some((t) => t.id === tab);
}
