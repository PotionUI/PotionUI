export type FactStyle = 'tag' | 'text';

export interface Fact<Row> {
	key: string;
	label: string;
	value: (row: Row) => string | null | undefined;
	as: FactStyle;
	base?: boolean;
}

export type Lead =
	| { type: 'cover'; id: string; name: string; src: string | null; category?: string | null }
	| { type: 'thumb'; src: string | null; icon?: string }
	| { type: 'icon'; name: string }
	| { type: 'avatar'; text: string; src?: string | null };

export type BadgeTone = 'neutral' | 'signal' | 'success' | 'warning' | 'danger' | 'info';

export interface PickerBadge {
	label: string;
	tone: BadgeTone;
}

export interface PickerColumn<Row> {
	key: string;
	label: string;
	width: string;
	priority?: 0 | 1 | 2;
	align?: 'left' | 'right' | 'center';
	mono?: boolean;
	sortable?: boolean;
	value: (row: Row) => string;
}

export interface FilterOption {
	value: string;
	label: string;
}

export interface FilterContext {
	collision: number;
}

export interface FilterSpec<Row> {
	key: string;
	label: string;
	options: (rows: readonly Row[]) => FilterOption[];
	match: (row: Row, value: string, ctx: FilterContext) => boolean;
}

export interface SortSpec<Row> {
	value: string;
	label: string;
	compare: (a: Row, b: Row) => number;
}

export interface EntityKind<Row> {
	id: string;
	singular: string;
	plural: string;
	icon: string;
	getId: (row: Row) => string;
	getName: (row: Row) => string;
	searchText: (row: Row) => string;
	lead: (row: Row) => Lead;
	badges?: (row: Row) => PickerBadge[];
	facts: readonly Fact<Row>[];
	columns: readonly PickerColumn<Row>[];
	filters: readonly FilterSpec<Row>[];
	sorts: readonly SortSpec<Row>[];
	defaultSort: string;
	searchPlaceholder: string;
	empty: { title: string; description: string };
	rowHeight?: number;
}

export type PickerView = 'unassigned' | 'assigned' | 'all';

export interface DisplayFact {
	key: string;
	label: string;
	value: string;
	as: FactStyle;
	emphasis: boolean;
}

export interface CollisionInfo {
	size: number;
	differing: ReadonlySet<string>;
	needsIdTail: boolean;
}

export interface PickerDiff {
	add: string[];
	remove: string[];
}

export interface ApplyFailure {
	id: string;
	message: string;
}

export interface ApplyResult {
	ok: string[];
	failed: ApplyFailure[];
}

export interface RemoteQuery {
	q: string;
	filters: Record<string, string>;
	sort: string;
	view: PickerView;
	offset: number;
	limit: number;
}

export interface RemoteSource<R> {
	fetch: (query: RemoteQuery) => Promise<{ rows: R[]; total: number }>;
	filterOptions?: Record<string, FilterOption[]>;
	pageSize?: number;
}
