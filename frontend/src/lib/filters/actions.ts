import type { PaintSession } from '$lib/components/imageEditor/session';
import { loadCube } from './catalog';
import { toActiveFilter, type FilterItem } from './types';

export async function selectFilterItem(session: PaintSession, item: FilterItem | null): Promise<void> {
	session.closeFineTune();
	if (!item) {
		session.clearFilter();
		return;
	}
	const cube = item.has_lut ? await loadCube(item) : null;
	session.setFilter(toActiveFilter(item, cube));
}

export function suggestedName(name: string, owned: boolean): string {
	const base = owned ? `${name} copy` : `My ${name}`;
	return base.slice(0, 24);
}
