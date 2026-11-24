export const KITS_VIEW_PARAM = 'view';
export const KITS_VIEW_VALUE = 'kits';
export const KIT_PARAM = 'kit';
export const KITS_LIST_PARAMS = ['page', 'sort', 'dir', 'q', 'label'] as const;

const UNSAFE_ID = /[/\\?#]|\.\./;

export interface KitsViewState {
	active: boolean;
	kitId: string | null;
}

export function kitsViewFromUrl(url: URL): KitsViewState {
	if (url.searchParams.get(KITS_VIEW_PARAM) !== KITS_VIEW_VALUE) return { active: false, kitId: null };
	const id = url.searchParams.get(KIT_PARAM)?.trim();
	return { active: true, kitId: id && !UNSAFE_ID.test(id) ? id : null };
}

export function hrefWithKitsView(url: URL, state: KitsViewState): string {
	const params = new URLSearchParams(url.searchParams);
	if (state.active) {
		params.set(KITS_VIEW_PARAM, KITS_VIEW_VALUE);
		if (state.kitId) params.set(KIT_PARAM, state.kitId);
		else params.delete(KIT_PARAM);
	} else {
		params.delete(KITS_VIEW_PARAM);
		params.delete(KIT_PARAM);
		for (const key of KITS_LIST_PARAMS) params.delete(key);
	}
	const query = params.toString();
	return `${url.pathname}${query ? `?${query}` : ''}${url.hash}`;
}
