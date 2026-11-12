import { goto } from '$app/navigation';

export function replaceUrl(url: string): void {
	void goto(url, { replaceState: true, keepFocus: true, noScroll: true }).catch(() => undefined);
}
