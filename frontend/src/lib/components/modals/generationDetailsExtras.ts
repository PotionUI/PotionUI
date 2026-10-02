export interface DetailsExtraRow {
	id: string;
	label: string;
	checked: boolean;
	disabled?: boolean;
	onToggle: (checked: boolean) => void;
}

export interface DetailsExtraAction {
	id: string;
	label: string;
	icon?: string;
	onClick: () => void;
}

export interface DetailsExtraSection {
	id: string;
	title: string;
	icon?: string;
	rows?: DetailsExtraRow[];
	actions?: DetailsExtraAction[];
}

export interface DetailsFileChange {
	generationId: string;
	fileIndex: number;
	fileId: number | null;
}
