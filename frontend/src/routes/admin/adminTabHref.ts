export function adminTabHref(pathname: string, tabId: string): string {
	return `${pathname}?${new URLSearchParams({ tab: tabId }).toString()}`;
}
