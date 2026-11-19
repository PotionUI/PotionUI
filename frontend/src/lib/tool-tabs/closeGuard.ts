import type { AutosaveStatus } from './image/editAutosave';

export type CloseLossReason = 'offline' | 'failed' | 'conflict' | 'too-large' | 'unsynced' | 'layers';

export interface CloseLoss {
	edits: number;
	reason: CloseLossReason;
}

export type CloseGuard = () => Promise<CloseLoss | null>;

const guards = new Map<string, CloseGuard>();

export function setToolTabCloseGuard(tabId: string, guard: CloseGuard | null): void {
	if (guard) guards.set(tabId, guard);
	else guards.delete(tabId);
}

export function hasToolTabCloseGuard(tabId: string): boolean {
	return guards.has(tabId);
}

export async function runToolTabCloseGuard(tabId: string): Promise<CloseLoss | null> {
	const guard = guards.get(tabId);
	if (!guard) return null;
	try {
		return await guard();
	} catch {
		return { edits: 0, reason: 'unsynced' };
	}
}

export interface CloseFacts {
	edits: number;
	layersFlushed: boolean;
	status: AutosaveStatus;
}

export function editCloseLoss({ edits, layersFlushed, status }: CloseFacts): CloseLoss | null {
	if (!layersFlushed) return { edits, reason: 'layers' };
	if (edits <= 0) return null;
	if (status === 'saved') return null;
	if (status === 'offline') return { edits, reason: 'offline' };
	if (status === 'failed') return { edits, reason: 'failed' };
	if (status === 'conflict') return { edits, reason: 'conflict' };
	if (status === 'too-large') return { edits, reason: 'too-large' };
	return { edits, reason: 'unsynced' };
}

const REASON_COPY: Record<CloseLossReason, string> = {
	offline: 'You are offline, so they have not reached the server.',
	failed: 'Autosave failed, so they exist only in this tab.',
	conflict: 'A newer version of this edit exists on the server, so yours is not saved.',
	'too-large': 'They are too large to autosave, so they exist only in this tab.',
	unsynced: 'They have not been saved to the server.',
	layers: 'A paint layer could not be uploaded, so it exists only in this tab.'
};

export function closeLossMessage(loss: CloseLoss): string {
	return REASON_COPY[loss.reason];
}
