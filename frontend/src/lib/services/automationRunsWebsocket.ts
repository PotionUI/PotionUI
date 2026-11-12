/**
 * Automations run-status WebSocket service.
 *
 * Token-authenticated connection to `/ws/automations` (pattern copied from
 * `notificationsWebsocket.ts`). Unlike the notifications socket this is
 * page-scoped: the automation editor page connects it in `onMount` and
 * disconnects in `onDestroy`, rather than keeping one global singleton alive
 * for the whole authenticated session.
 */
import { logger } from '$lib/utils/logger';
import { BaseWebSocket, type BaseWebSocketMessage } from './BaseWebSocket';
import { automationRuns } from '$lib/stores/automationRuns';
import type { AutomationRunUpdateMessage } from '$lib/types/automations';

class AutomationRunsWebSocketService extends BaseWebSocket {
	private intentionalDisconnect = false;

	constructor() {
		super('/ws/automations');
	}

	connect(): void {
		this.intentionalDisconnect = false;
		super.connect();
	}

	disconnect(): void {
		this.intentionalDisconnect = true;
		super.disconnect();
	}

	protected override onOpen(): void {
		logger.debug('Automations WebSocket connected');
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		switch (message.type) {
			case 'heartbeat':
			case 'pong':
			case 'connection_established':
				break;

			case 'automation_run_update':
				automationRuns.applyWsMessage(message as unknown as AutomationRunUpdateMessage);
				break;

			default:
				logger.debug('Unknown automations message type:', message.type);
		}
	}

	protected override onClose(_event: CloseEvent): void {
		if (!this.intentionalDisconnect) {
			this.scheduleReconnect();
		}
	}
}

export const automationRunsWebSocket = new AutomationRunsWebSocketService();
