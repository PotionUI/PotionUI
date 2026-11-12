import type { KitNotice } from './types';
import { skipLines } from './kitResolver';

export function kitNoticesOf(data: unknown): KitNotice[] {
	const kits = (data as { kits?: { notices?: unknown } } | null | undefined)?.kits;
	return Array.isArray(kits?.notices) ? (kits.notices as KitNotice[]) : [];
}

export function kitNoticeText(notices: readonly KitNotice[]): string {
	return skipLines(notices).join(' ');
}
