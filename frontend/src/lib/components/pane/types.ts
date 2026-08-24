export interface TreeItem {
	id: string;
	parent_id?: string | null;
}

export interface PaneTreeNode<T> {
	item: T;
	children: PaneTreeNode<T>[];
	depth: number;
}

export interface RowContext<T> {
	item: T;
	node: PaneTreeNode<T>;
	depth: number;
	hasChildren: boolean;
	expanded: boolean;
	toggle: () => void;
}
