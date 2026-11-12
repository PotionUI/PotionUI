import type { KitNotice } from './types';
import { skipLines } from './kitResolver';
import { setSubmitKitNotices } from '$lib/stores/kitNotices';
import { toasts } from '$lib/stores/toast';

export function kitNoticesOf(data: unknown): KitNotice[] {
	const kits = (data as { kits?: { notices?: unknown } } | null | undefined)?.kits;
	return Array.isArray(kits?.notices) ? (kits.notices as KitNotice[]) : [];
}

export function kitNoticeText(notices: readonly KitNotice[]): string {
	return skipLines(notices).join(' ');
}

export function announceKitNotices(tabId: string, data: unknown): void {
	const notices = kitNoticesOf(data);
	setSubmitKitNotices(tabId, notices);
	if (notices.length > 0) toasts.warning(kitNoticeText(notices));
}
