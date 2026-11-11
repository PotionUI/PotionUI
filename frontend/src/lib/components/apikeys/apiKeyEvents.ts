import type { ApiKeyEvent, ApiKeyScope } from '$lib/services/api/apiKeys';
import { SCOPE_DEFS } from './apiKeyDialog';
import { formatKeyDate } from './apiKeyView';

export const ACTIVITY_LIMIT = 4;

export interface ActivityLine {
	when: string;
	what: string;
	detail: string;
}

function scopeName(scope: ApiKeyScope): string {
	return SCOPE_DEFS[scope]?.label ?? scope;
}

function list(scopes: readonly ApiKeyScope[], verb: string): string {
	return scopes.length ? `${scopes.map(scopeName).join(', ')} ${verb}` : '';
}

export function describeEvent(event: ApiKeyEvent, now: Date = new Date()): ActivityLine {
	const when = formatKeyDate(event.created_at, now);
	switch (event.action) {
		case 'created':
			return { when, what: 'Created', detail: '' };
		case 'renamed': {
			const name = event.changes?.name;
			return { when, what: 'Renamed', detail: name ? `From “${name.before ?? ''}” to “${name.after ?? ''}”` : '' };
		}
		case 'scopes_changed': {
			const scopes = event.changes?.scopes;
			const detail = [list(scopes?.added ?? [], 'added'), list(scopes?.removed ?? [], 'removed')].filter(Boolean).join(', ');
			return { when, what: 'Access changed', detail };
		}
		case 'revoked':
			return { when, what: 'Revoked', detail: event.detail?.by_owner === false ? 'By an administrator' : 'By you' };
		case 'expired':
			return { when, what: 'Expired', detail: '' };
		default:
			return { when, what: event.action.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), detail: '' };
	}
}

export function describeEvents(events: readonly ApiKeyEvent[], now: Date = new Date()): ActivityLine[] {
	return events.slice(0, ACTIVITY_LIMIT).map((event) => describeEvent(event, now));
}
