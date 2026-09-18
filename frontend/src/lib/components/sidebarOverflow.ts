export interface SidebarPartition<T> {
	visible: T[];
	overflow: T[];
}

export interface SidebarPartitionOptions {
	rowHeight: number;
	reservedHeight: number;
}

export function partitionNavItems<T>(
	items: T[],
	availableHeight: number,
	{ rowHeight, reservedHeight }: SidebarPartitionOptions
): SidebarPartition<T> {
	const usable = availableHeight - reservedHeight;
	if (rowHeight <= 0 || usable <= 0) {
		return { visible: [], overflow: [...items] };
	}
	if (items.length * rowHeight <= usable) {
		return { visible: [...items], overflow: [] };
	}
	const usableWithMoreRow = usable - rowHeight;
	const visibleCount = usableWithMoreRow > 0 ? Math.floor(usableWithMoreRow / rowHeight) : 0;
	return {
		visible: items.slice(0, visibleCount),
		overflow: items.slice(visibleCount)
	};
}
