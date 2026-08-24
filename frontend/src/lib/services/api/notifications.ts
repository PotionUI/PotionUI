import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export type NotificationLevel = 'success' | 'error' | 'info' | 'warning';

export interface AppNotification {
	id: string;
	user_id: string;
	category: string;
	type: string;
	level: NotificationLevel;
	title: string;
	message: string;
	metadata: Record<string, unknown> | null;
	source: string;
	read: boolean;
	created_at: string;
}

export interface NotificationTypePref {
	key: string;
	label: string;
	description: string;
	category: string;
	default_enabled: boolean;
	enabled: boolean;
}

export interface NotificationPreferences {
	types: NotificationTypePref[];
	sound: boolean;
}

export interface UpdateNotificationPreferencesInput {
	types?: Record<string, boolean>;
	sound?: boolean;
}

export interface NotificationListResult {
	notifications: AppNotification[];
	unread_count: number;
}

export interface CreateNotificationInput {
	level: NotificationLevel;
	title: string;
	message?: string;
	category?: string;
	type?: string;
	transient?: boolean;
	show_toast?: boolean;
	metadata?: Record<string, unknown> | null;
}

export interface ListNotificationsParams {
	limit?: number;
	before?: string;
	unread_only?: boolean;
}

export function createNotificationsApi(client: AxiosInstance) {
	return {
		async listNotifications(
			params: ListNotificationsParams = {}
		): Promise<APIResponse<NotificationListResult>> {
			const response = await client.get('/api/notifications/', { params });
			return response.data;
		},

		async createNotification(
			input: CreateNotificationInput
		): Promise<APIResponse<{ notifications: AppNotification[] }>> {
			const response = await client.post('/api/notifications/', input);
			return response.data;
		},

		async markNotificationRead(id: string): Promise<APIResponse<unknown>> {
			const response = await client.post(`/api/notifications/${id}/read`);
			return response.data;
		},

		async markAllNotificationsRead(): Promise<APIResponse<{ updated: number }>> {
			const response = await client.post('/api/notifications/read-all');
			return response.data;
		},

		async deleteNotification(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/notifications/${id}`);
			return response.data;
		},

		async clearNotifications(): Promise<APIResponse<{ deleted: number }>> {
			const response = await client.delete('/api/notifications/');
			return response.data;
		},

		async getNotificationTypes(): Promise<APIResponse<NotificationPreferences>> {
			const response = await client.get('/api/notifications/types');
			return response.data;
		},

		async updateNotificationPreferences(
			input: UpdateNotificationPreferencesInput
		): Promise<APIResponse<NotificationPreferences>> {
			const response = await client.put('/api/notifications/preferences', input);
			return response.data;
		}
	};
}
