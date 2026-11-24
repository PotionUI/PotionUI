import { goto } from '$app/navigation';

export type ListParamPatch = Record<string, string | number | null | undefined>;

export function pageParam(params: URLSearchParams, key = 'page'): number {
	const value = Number.parseInt(params.get(key) ?? '', 10);
	return Number.isFinite(value) && value > 1 ? value : 1;
}

export function choiceParam<T extends string>(
	params: URLSearchParams,
	key: string,
	allowed: readonly T[],
	fallback: T
): T {
	const value = params.get(key);
	return allowed.find((candidate) => candidate === value) ?? fallback;
}

export function hrefWithParams(url: URL, patch: ListParamPatch): string {
	const params = new URLSearchParams(url.searchParams);
	for (const [key, value] of Object.entries(patch)) {
		if (value === null || value === undefined || value === '') params.delete(key);
		else params.set(key, String(value));
	}
	const query = params.toString();
	return `${url.pathname}${query ? `?${query}` : ''}${url.hash}`;
}

export function replaceListParams(url: URL, patch: ListParamPatch): Promise<void> {
	const href = hrefWithParams(url, patch);
	if (href === `${url.pathname}${url.search}${url.hash}`) return Promise.resolve();
	return goto(href, { replaceState: true, keepFocus: true, noScroll: true });
}
