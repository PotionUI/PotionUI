const BOX_BASE = 'rounded-lg border border-line bg-surface-1 shadow-raised';

export function toggleOpen(open: boolean): boolean {
	return !open;
}

export function sectionBoxClass(padded: boolean): string {
	return padded ? BOX_BASE : `${BOX_BASE} overflow-hidden`;
}

export function sectionBodyClass(padded: boolean): string {
	return padded ? 'px-4 sm:px-5 py-4' : '';
}
