const BACKEND_PARAM = 'backend';
const VIEW_PARAM = 'view';

export interface BackendsUrlState {
	backendId: string | null;
	view: string | null;
}

/** Read the selected backend and detail tab out of the Backends tab's own
 * query params. `view` is returned as-is (unvalidated) - whether it names a
 * real tab on the selected backend's driver is checked against
 * `isBackendDetailTab` by the caller, once the driver is known. */
export function readBackendsUrlState(searchParams: URLSearchParams): BackendsUrlState {
	return {
		backendId: searchParams.get(BACKEND_PARAM),
		view: searchParams.get(VIEW_PARAM)
	};
}

/** Write `state` onto `url`'s query params, returning a new URL. A null
 * `backendId` (nothing selected) or a `view` of 'overview' (the default) is
 * removed rather than written, so an idle Backends tab keeps a clean URL. */
export function writeBackendsUrlState(url: URL, state: BackendsUrlState): URL {
	const next = new URL(url);
	if (state.backendId) {
		next.searchParams.set(BACKEND_PARAM, state.backendId);
	} else {
		next.searchParams.delete(BACKEND_PARAM);
	}
	if (state.view && state.view !== 'overview') {
		next.searchParams.set(VIEW_PARAM, state.view);
	} else {
		next.searchParams.delete(VIEW_PARAM);
	}
	return next;
}
