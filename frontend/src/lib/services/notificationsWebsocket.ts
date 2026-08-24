/**
 * Notifications WebSocket Service
 *
 * Token-authenticated connection to `/ws/notifications` (like the generation
 * WS). Dispatches pushed notifications and sync events into the notifications
 * store and surfaces toasts. Separate lifecycle from the generation/admin
 * sockets so it can be connected once per authenticated session.
 */

import { get } from 'svelte/store';
import { getWsUrl } from './wsUrl';
import { logger } from '$lib/utils/logger';
import { BaseWebSocket, type BaseWebSocketMessage } from './BaseWebSocket';
import { api } from '$lib/services/api';
import { notifications } from '$lib/stores/notifications';
import { toasts, type ToastType } from '$lib/stores/toast';
import { playNotificationChime } from '$lib/utils/notificationChime';
import type { AppNotification } from '$lib/services/api/notifications';

class NotificationsWebSocketService extends BaseWebSocket {
	private intentionalDisconnect = false;

	constructor() {
		// URL + token are resolved dynamically in buildWsUrl (token can change on login).
		super('', null);
	}

	protected override buildWsUrl(): string {
		return getWsUrl('/ws/notifications', api.getToken());
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
		logger.debug('Notifications WebSocket connected');
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		switch (message.type) {
			case 'heartbeat':
			case 'pong':
				break;

			case 'connection_established':
				notifications.dispatch({
					type: 'connection_established',
					unread_count: message.unread_count as number | undefined
				});
				break;

			case 'notification': {
				const notification = message.notification as AppNotification;
				const showToast = message.show_toast !== false;
				notifications.add(notification);
				if (showToast && notification) {
					toasts.show(this.toToastType(notification.level), notification.message || '', {
						title: notification.title
					});
				}
				this.maybeChime();
				break;
			}

			case 'toast':
				toasts.show(this.toToastType(message.level as string), (message.message as string) || '', {
					title: message.title as string | undefined
				});
				this.maybeChime();
				break;

			case 'notification_read':
				notifications.dispatch({ type: 'notification_read', id: message.id as string });
				break;

			case 'all_read':
				notifications.dispatch({ type: 'all_read' });
				break;

			case 'notification_deleted':
				notifications.dispatch({ type: 'notification_deleted', id: message.id as string });
				break;

			case 'notifications_cleared':
				notifications.dispatch({ type: 'notifications_cleared' });
				break;

			default:
				logger.debug('Unknown notification message type:', message.type);
		}
	}

	protected override onClose(_event: CloseEvent): void {
		if (!this.intentionalDisconnect) {
			this.scheduleReconnect();
		}
	}

	/** Play the chime iff the user's sound preference is enabled. */
	private maybeChime(): void {
		if (get(notifications).sound) {
			playNotificationChime();
		}
	}

	private toToastType(level: string): ToastType {
		switch (level) {
			case 'success':
			case 'error':
			case 'warning':
			case 'info':
				return level;
			default:
				return 'info';
		}
	}
}

export const notificationsWebSocket = new NotificationsWebSocketService();
