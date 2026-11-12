import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export type ApiKeyScope = 'read' | 'generate' | 'content' | 'admin' | 'mcp';
export type ApiKeyStatus = 'active' | 'expired' | 'revoked';
export type ApiKeyExpiryDays = 30 | 90 | 365 | null;

export interface ApiKeyRevoker {
	id: string;
	username: string;
}

export interface ApiKeyMcpTools {
	exposed: number;
	total: number;
}

export interface ApiKeyEventPerson {
	id: string;
	username: string | null;
}

export interface ApiKeyEventChanges {
	name?: { before: string | null; after: string | null };
	scopes?: { added: ApiKeyScope[]; removed: ApiKeyScope[] };
}

export interface ApiKeyEvent {
	action: string;
	actor: ApiKeyEventPerson | null;
	detail: Record<string, unknown>;
	changes: ApiKeyEventChanges | null;
	created_at: string;
}

export interface ApiKeyEventsPage {
	items: ApiKeyEvent[];
	total: number;
	page: number;
	page_size: number;
}

export interface ApiKey {
	id: string;
	name: string;
	display_prefix: string;
	last_four: string | null;
	scopes: ApiKeyScope[];
	status: ApiKeyStatus;
	created_at: string;
	expires_at: string | null;
	last_used_at: string | null;
	last_used_via: string | null;
	revoked_at: string | null;
	revoked_by: ApiKeyRevoker | null;
	mcp_tools: ApiKeyMcpTools | null;
}

export interface ApiKeyLimits {
	keys_enabled: boolean;
	mcp_enabled: boolean;
	scopes_allowed: ApiKeyScope[];
	max_lifetime_days: number | null;
}

export interface ApiKeyListing {
	keys: ApiKey[];
	limits: ApiKeyLimits;
}

export interface ApiKeyCreateRequest {
	name: string;
	scopes: ApiKeyScope[];
	expires_in_days: ApiKeyExpiryDays;
}

export interface ApiKeyCreated {
	key: ApiKey;
	secret: string;
}

export interface ApiKeyUpdateRequest {
	name?: string;
	scopes?: ApiKeyScope[];
}

export interface ApiKeyOwner {
	id: string;
	username: string | null;
}

export interface AdminApiKey extends ApiKey {
	owner: ApiKeyOwner;
}

export type AdminKeyStatusFilter = 'all' | ApiKeyStatus;

export interface AdminApiKeysQuery {
	status?: AdminKeyStatusFilter;
	userId?: string | null;
	query?: string;
	page?: number;
	pageSize?: number;
}

export interface AdminKeyCounts {
	all: number;
	active: number;
	expired: number;
	revoked: number;
}

export interface AdminApiKeysPage {
	items: AdminApiKey[];
	total: number;
	counts?: AdminKeyCounts;
	page: number;
	page_size: number;
}

export interface UserKeyCounts {
	active: number;
	total: number;
}

export interface UserApiAccess {
	keys_enabled: boolean;
	mcp_enabled: boolean;
	keys?: UserKeyCounts;
}

export interface ApiKeyRevoked {
	id: string;
	revoked: boolean;
}

export function createApiKeysApi(client: AxiosInstance) {
	return {
		async listApiKeys(): Promise<APIResponse<ApiKeyListing>> {
			const response = await client.get('/api/keys');
			return response.data;
		},

		async createApiKey(request: ApiKeyCreateRequest): Promise<APIResponse<ApiKeyCreated>> {
			const response = await client.post('/api/keys', request);
			return response.data;
		},

		async updateApiKey(keyId: string, request: ApiKeyUpdateRequest): Promise<APIResponse<ApiKey>> {
			const response = await client.patch(`/api/keys/${encodeURIComponent(keyId)}`, request);
			return response.data;
		},

		async listApiKeyEvents(keyId: string, page = 1, pageSize = 20): Promise<APIResponse<ApiKeyEventsPage>> {
			const response = await client.get(`/api/keys/${encodeURIComponent(keyId)}/events`, {
				params: { page, page_size: pageSize }
			});
			return response.data;
		},

		async revokeApiKey(keyId: string): Promise<APIResponse<ApiKeyRevoked>> {
			const response = await client.delete(`/api/keys/${encodeURIComponent(keyId)}`);
			return response.data;
		},

		async listAdminApiKeys(query: AdminApiKeysQuery = {}): Promise<APIResponse<AdminApiKeysPage>> {
			const params: Record<string, string | number> = {
				status: query.status ?? 'all',
				page: query.page ?? 1,
				page_size: query.pageSize ?? 50
			};
			if (query.userId) params.user_id = query.userId;
			if (query.query?.trim()) params.q = query.query.trim();
			const response = await client.get('/api/admin/keys', { params });
			return response.data;
		},

		async revokeAdminApiKey(keyId: string): Promise<APIResponse<ApiKeyRevoked>> {
			const response = await client.delete(`/api/admin/keys/${encodeURIComponent(keyId)}`);
			return response.data;
		},

		async listAdminApiKeyEvents(keyId: string): Promise<APIResponse<ApiKeyEvent[]>> {
			const response = await client.get(`/api/admin/keys/${encodeURIComponent(keyId)}/events`);
			return response.data;
		},

		async getUserApiAccess(userId: string): Promise<APIResponse<UserApiAccess>> {
			const response = await client.get(`/api/admin/users/${encodeURIComponent(userId)}/api-access`);
			return response.data;
		},

		async setUserApiAccess(userId: string, access: UserApiAccess): Promise<APIResponse<UserApiAccess>> {
			const response = await client.put(`/api/admin/users/${encodeURIComponent(userId)}/api-access`, {
				keys_enabled: access.keys_enabled,
				mcp_enabled: access.mcp_enabled
			});
			return response.data;
		}
	};
}
