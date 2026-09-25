export type DetailHeaderChipTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

export interface DetailHeaderChip {
	key: string;
	label: string;
	tone?: DetailHeaderChipTone;
}

export interface DetailHeaderChipSplit {
	visible: DetailHeaderChip[];
	overflow: DetailHeaderChip[];
}

export function splitDetailHeaderChips(
	chips: readonly DetailHeaderChip[],
	max = 3
): DetailHeaderChipSplit {
	if (chips.length <= max) return { visible: [...chips], overflow: [] };
	return { visible: chips.slice(0, max), overflow: chips.slice(max) };
}
