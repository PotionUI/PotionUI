import type { APIResponse } from '$lib/types/api';
import type { GenerationHistoryItem } from '$lib/types/history';
import {
	findGenerationTab,
	type GenerationState,
	type GenerationTab,
	type Tab,
	type TabOpenedFrom
} from '$lib/types/tabs';
import { buildHistoryReuseTabData, type HistoryReuseResult } from '$lib/utils/historyReuse';
import { mapGenerationFiles } from '$lib/utils/generationOrchestrator';
import { failureNotice } from '$lib/components/generation/failureNotice';
import { describeOrigin, offersOpenInTab } from '$lib/generation/origin';
import { findTabFollowing } from '$lib/generation/elsewhere/select';
import { buildActiveCompletionPatch, isConfirmedMissing } from '$lib/generation/restore/reconcile';
import { resetPreviewReplay } from '$lib/generation/messages/replayGuard';

export const MISSING_COPY = 'This generation is no longer available.';
export const UNREACHABLE_COPY = 'Could not open this generation. Try again.';
export const UNSUPPORTED_COPY = 'This generation cannot be opened in a tab.';

export interface OpenedTabPlan {
	name: string;
	tabData: Partial<GenerationTab>;
	reuse: HistoryReuseResult;
	live: boolean;
	settled: Partial<GenerationState> | null;
}

export function isLiveStatus(status: string | undefined): boolean {
	return status === 'pending' || status === 'running';
}

export function buildOpenedFrom(detail: Pick<GenerationHistoryItem, 'id' | 'origin'>): TabOpenedFrom {
	const info = describeOrigin(detail.origin);
	return { generationId: detail.id, kind: info?.kind ?? null, label: info?.label ?? null };
}

export function settledGenerationPatch(
	detail: GenerationHistoryItem,
	options: { isAdmin: boolean; hint: string | null }
): Partial<GenerationState> | null {
	if (detail.status === 'completed') {
		return buildActiveCompletionPatch(detail.id, mapGenerationFiles(detail.files ?? [], detail.id));
	}
	if (detail.status === 'failed') {
		const notice = failureNotice(detail, options.isAdmin);
		return {
			isGenerating: false,
			currentGeneration: {
				status: 'failed',
				id: detail.id,
				generation_id: detail.id,
				message: notice?.message ?? 'This generation failed.',
				hint: options.hint,
				errorId: notice?.errorId ?? null,
				errorCode: detail.error_code ?? null
			}
		};
	}
	if (detail.status === 'cancelled') return { isGenerating: false, currentGeneration: null };
	return null;
}

export function buildOpenedTab(
	detail: GenerationHistoryItem,
	options: { isAdmin: boolean; hint: string | null }
): OpenedTabPlan {
	const reuse = buildHistoryReuseTabData(detail);
	const live = isLiveStatus(detail.status);
	return {
		name: detail.preset_name || 'Generation',
		reuse,
		live,
		tabData: {
			...reuse.tabData,
			openedFrom: buildOpenedFrom(detail),
			...(live ? { activeGenerationId: detail.id } : {})
		},
		settled: live ? null : settledGenerationPatch(detail, options)
	};
}

export type OpenInTabResult =
	| { kind: 'focused'; tabId: string }
	| { kind: 'opened'; tabId: string; live: boolean }
	| { kind: 'missing' }
	| { kind: 'unsupported' }
	| { kind: 'unreachable' };

export interface OpenInTabDeps {
	getGeneration(id: string): Promise<APIResponse<GenerationHistoryItem>>;
	failureHint(id: string): Promise<string | null>;
	isAdmin(): boolean;
	tabs: {
		current(): { tabs: Tab[]; activeTabId: string };
		addTabWithData(name: string, data: Partial<GenerationTab>): string;
		setActiveTab(tabId: string): void;
		updateTab(tabId: string, updates: Partial<GenerationTab>): void;
	};
	attach(tabId: string, generationIds: string[]): Promise<boolean>;
	notifyError(message: string): void;
	announceReuse(reuse: HistoryReuseResult): void;
}

const inFlight = new Map<string, Promise<OpenInTabResult>>();

async function run(id: string, deps: OpenInTabDeps): Promise<OpenInTabResult> {
	const focusExisting = (): OpenInTabResult | null => {
		const existing = findTabFollowing(deps.tabs.current().tabs, id);
		if (!existing) return null;
		deps.tabs.setActiveTab(existing);
		return { kind: 'focused', tabId: existing };
	};
	const already = focusExisting();
	if (already) return already;

	let response: APIResponse<GenerationHistoryItem> | null = null;
	let thrown: unknown = null;
	try {
		response = await deps.getGeneration(id);
	} catch (error) {
		thrown = error;
	}
	if (!response?.success || !response.data) {
		const missing = isConfirmedMissing(thrown, response);
		deps.notifyError(missing ? MISSING_COPY : UNREACHABLE_COPY);
		return { kind: missing ? 'missing' : 'unreachable' };
	}
	const detail = response.data;
	if (!offersOpenInTab(detail) || (detail.mode ?? '').startsWith('editor:')) {
		deps.notifyError(UNSUPPORTED_COPY);
		return { kind: 'unsupported' };
	}

	const raced = focusExisting();
	if (raced) return raced;

	let hint: string | null = null;
	if (detail.status === 'failed' && deps.isAdmin()) {
		try {
			hint = await deps.failureHint(id);
		} catch {
			hint = null;
		}
	}

	const plan = buildOpenedTab(detail, { isAdmin: deps.isAdmin(), hint });
	resetPreviewReplay(id);
	const tabId = deps.tabs.addTabWithData(plan.name, plan.tabData);
	if (plan.settled) {
		const created = findGenerationTab(deps.tabs.current().tabs, tabId);
		if (created) deps.tabs.updateTab(tabId, { generation: { ...created.generation, ...plan.settled } });
	} else {
		await deps.attach(tabId, [id]);
	}
	deps.announceReuse(plan.reuse);
	return { kind: 'opened', tabId, live: plan.live };
}

export function openGenerationInTab(id: string, deps: OpenInTabDeps): Promise<OpenInTabResult> {
	const running = inFlight.get(id);
	if (running) return running;
	const next = run(id, deps).finally(() => inFlight.delete(id));
	inFlight.set(id, next);
	return next;
}
