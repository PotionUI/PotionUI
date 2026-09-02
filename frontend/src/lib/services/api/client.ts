import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';
import type { User } from '$lib/stores/auth';
import type { APIResponse } from '$lib/types/api';
import { storage } from '$lib/utils/storage';

export class APIClient {
	private client: AxiosInstance;
	private token: string | null = null;
	private onAuthExpiredCallback: (() => void) | null = null;

	constructor(baseURL: string = '') {
		this.client = axios.create({
			baseURL,
			timeout: 30000,
			headers: {
				'Content-Type': 'application/json'
			}
		});

		// Load token if in browser
		this.token = storage.get('auth_token');
		if (this.token) {
			this.setAuthHeader(this.token);
		}

		this.client.interceptors.request.use((config) => {
			if (this.token) {
				config.headers.Authorization = `Bearer ${this.token}`;
			}
			// Under the instance-wide JSON default, axios serialises a FormData
			// body to JSON instead of sending multipart - the server then sees
			// no file part at all. Declaring multipart here lets the browser set
			// the boundary itself.
			if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
				config.headers.setContentType('multipart/form-data');
			}
			return config;
		});

		// Handle 401 responses (expired/invalid tokens)
		this.client.interceptors.response.use(
			(response) => response,
			(error) => {
				if (
					error.response?.status === 401 &&
					!error.config?.url?.includes('/api/auth/login') &&
					!error.config?.url?.includes('/api/auth/register')
				) {
					this.onAuthExpiredCallback?.();
				}
				return Promise.reject(error);
			}
		);
	}

	setAuthHeader(token: string): void {
		this.token = token;
		this.client.defaults.headers.common['Authorization'] = `Bearer ${token}`;
		storage.set('auth_token', token);
	}

	getToken(): string | null {
		return this.token;
	}

	clearAuth(): void {
		this.token = null;
		delete this.client.defaults.headers.common['Authorization'];
		storage.remove('auth_token');
	}

	setOnAuthExpired(callback: () => void): void {
		this.onAuthExpiredCallback = callback;
	}

	// Lets callers built before setOnAuthExpired runs (e.g. domain modules
	// assembled at import time) still reach whichever callback is registered
	// by the time a 401 actually happens.
	triggerAuthExpired(): void {
		this.onAuthExpiredCallback?.();
	}

	getBaseURL(): string {
		return this.client.defaults.baseURL || '';
	}

	getClient(): AxiosInstance {
		return this.client;
	}

	// Auth API
	async login(credentials: {
		username: string;
		password: string;
		remember_me?: boolean;
	}): Promise<{ access_token: string; token_type: string }> {
		const formData = new FormData();
		formData.append('username', credentials.username);
		formData.append('password', credentials.password);
		if (credentials.remember_me) {
			formData.append('remember_me', 'true');
		}

		const response = await this.client.post('/api/auth/login', formData, {
			headers: {
				'Content-Type': 'application/x-www-form-urlencoded'
			}
		});

		const tokenData = response.data;
		this.setAuthHeader(tokenData.access_token);
		return tokenData;
	}

	async register(credentials: {
		username: string;
		email: string;
		password: string;
		// One-time setup token; only required to claim an unclaimed instance
		// from a non-loopback origin (see SetupStatus.claim_requires_token).
		claim_token?: string;
	}): Promise<{ success: boolean; data?: { user: User; access_token: string; token_type: string }; message?: string; error?: string }> {
		const response = await this.client.post('/api/auth/register', credentials);
		const data = response.data;

		if (data.success && data.data?.access_token) {
			this.setAuthHeader(data.data.access_token);
		}

		return data;
	}

	async getCurrentUser(): Promise<{ success: boolean; data?: User; message?: string; error?: string }> {
		const response = await this.client.get('/api/auth/me');
		return response.data;
	}

	async changePassword(currentPassword: string, newPassword: string): Promise<APIResponse> {
		const response = await this.client.post('/api/auth/change-password', {
			current_password: currentPassword,
			new_password: newPassword
		});
		return response.data;
	}

	async uploadAvatar(userId: string, file: File): Promise<APIResponse<{ avatar_url: string }>> {
		const formData = new FormData();
		formData.append('file', file);

		const response = await this.client.post(`/api/users/${userId}/avatar`, formData, {
			headers: {
				'Content-Type': 'multipart/form-data'
			}
		});
		return response.data;
	}

	async deleteAvatar(userId: string): Promise<APIResponse> {
		const response = await this.client.delete(`/api/users/${userId}/avatar`);
		return response.data;
	}
}
