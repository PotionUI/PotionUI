import type { MenuItem } from '$lib/components/ui/Menu.svelte';

interface ViewMenuState {
	size?: string;
	grouped?: boolean;
	refresh?: boolean;
}

interface ViewMenuHandlers {
	onsize?: (size: string) => void;
	ongroup?: () => void;
	onrefresh?: () => void;
}

const SIZES: Array<[string, string]> = [
	['small', 'Small tiles'],
	['medium', 'Medium tiles'],
	['large', 'Large tiles']
];

export function viewMenuItems(state: ViewMenuState): MenuItem[] {
	const items: MenuItem[] = [];
	if (state.refresh) items.push({ id: 'view-refresh', label: 'Refresh', icon: 'refresh' });
	if (state.size !== undefined) {
		for (const [value, label] of SIZES) {
			items.push({ id: `view-size-${value}`, label, icon: state.size === value ? 'check' : undefined });
		}
	}
	if (state.grouped !== undefined) {
		items.push({
			id: 'view-group',
			label: state.grouped ? 'One continuous wall' : 'Group by date',
			icon: 'calendar'
		});
	}
	if (items.length > 0) items.push({ id: 'view-separator', label: '', separator: true });
	return items;
}

export function handleViewMenu(id: string, handlers: ViewMenuHandlers): boolean {
	if (!id.startsWith('view-')) return false;
	if (id === 'view-refresh') handlers.onrefresh?.();
	else if (id === 'view-group') handlers.ongroup?.();
	else if (id.startsWith('view-size-')) handlers.onsize?.(id.slice('view-size-'.length));
	return true;
}
