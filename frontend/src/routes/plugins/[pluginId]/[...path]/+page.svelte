<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { page } from '$app/stores';
	import { onMount, onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { authStore } from '$lib/stores/auth';
	import { goto, afterNavigate } from '$app/navigation';
	import { getRegistry } from '$lib/plugin-api/componentRegistry';
	import { PageHeader } from '$lib/components/ui';

	let pageInfo: any = null;
	let loading = true;
	let error: string | null = null;
	let containerEl: HTMLDivElement;
	let headerActionsEl: HTMLDivElement;
	let pluginInstance: any = null;
	let unmountFn: ((instance: any) => void) | null = null;
	let loadedPluginId: string | null = null;
	let loadVersion = 0;

	function createPluginApi(pluginId: string) {
		const baseUrl = api.getBaseURL();
		const headers = () => ({
			'Content-Type': 'application/json',
			...(api.getToken() ? { Authorization: `Bearer ${api.getToken()}` } : {})
		});

		return {
			// The auth token, so a plugin can authenticate a WebSocket it opens
			// itself (query-string token - a browser WebSocket cannot send an
			// Authorization header). Null when unauthenticated.
			getToken: () => api.getToken(),
			get: async (path: string) => {
				const res = await fetch(`${baseUrl}/api/plugins/${pluginId}${path}`, {
					headers: headers(),
					credentials: 'include'
				});
				return res.json();
			},
			post: async (path: string, data: any) => {
				const res = await fetch(`${baseUrl}/api/plugins/${pluginId}${path}`, {
					method: 'POST',
					headers: headers(),
					credentials: 'include',
					body: JSON.stringify(data)
				});
				return res.json();
			},
			put: async (path: string, data: any) => {
				const res = await fetch(`${baseUrl}/api/plugins/${pluginId}${path}`, {
					method: 'PUT',
					headers: headers(),
					credentials: 'include',
					body: JSON.stringify(data)
				});
				return res.json();
			},
			delete: async (path: string) => {
				const res = await fetch(`${baseUrl}/api/plugins/${pluginId}${path}`, {
					method: 'DELETE',
					headers: headers(),
					credentials: 'include'
				});
				return res.json();
			},
			uploadFile: async (path: string, formData: FormData) => {
				const res = await fetch(`${baseUrl}/api/plugins/${pluginId}${path}`, {
					method: 'POST',
					headers: {
						...(api.getToken() ? { Authorization: `Bearer ${api.getToken()}` } : {})
					},
					credentials: 'include',
					body: formData
				});
				return res.json();
			},
			fetchFromApp: async (path: string) => {
				const res = await fetch(`${baseUrl}${path}`, {
					headers: headers(),
					credentials: 'include'
				});
				return res.json();
			}
		};
	}

	function cleanupPlugin() {
		if (pluginInstance && unmountFn) {
			try {
				unmountFn(pluginInstance);
			} catch (e) {
				// Ignore unmount errors during navigation
			}
		}
		pluginInstance = null;
		unmountFn = null;
	}

	async function loadPlugin(id: string) {
		const thisVersion = ++loadVersion;

		cleanupPlugin();

		loading = true;
		error = null;
		pageInfo = null;

		try {
			const response = await fetch(`${api.getBaseURL()}/api/plugins/pages/${id}`, {
				credentials: 'include',
				headers: {
					'Content-Type': 'application/json',
					...(api.getToken() ? { Authorization: `Bearer ${api.getToken()}` } : {})
				}
			});

			if (thisVersion !== loadVersion) return;

			if (!response.ok) {
				throw new Error(`Plugin page not found: ${id}`);
			}

			const data = await response.json();
			if (!data.success || !data.data) {
				throw new Error('Invalid page data');
			}

			pageInfo = data.data;

			const componentUrl = `${api.getBaseURL()}/api/plugins/${pageInfo.plugin_id}/assets/${pageInfo.component_path}`;
			const mod = await import(/* @vite-ignore */ componentUrl);

			if (thisVersion !== loadVersion) return;

			loading = false;

			// Wait for the container to be in the DOM
			await new Promise(r => requestAnimationFrame(r));

			if (thisVersion !== loadVersion) return;

			if (mod.mountPlugin && containerEl) {
				pluginInstance = mod.mountPlugin(containerEl, {
					pluginId: id,
					route: pageInfo.route,
					api: createPluginApi(id),
					user: $authStore.user,
					navigate: (path: string) => goto(path),
					hostComponents: getRegistry(),
					headerActions: headerActionsEl
				});
				unmountFn = mod.unmountPlugin || null;
			} else if (containerEl) {
				// Fallback: try Svelte 5 mount directly
				const { mount } = await import('svelte');
				pluginInstance = mount(mod.default, {
					target: containerEl,
					props: {
						pluginId: id,
						route: pageInfo.route,
						api: createPluginApi(id),
						user: $authStore.user,
						navigate: (path: string) => goto(path),
						headerActions: headerActionsEl
					}
				});
			}

			loadedPluginId = id;
		} catch (err: any) {
			if (thisVersion === loadVersion) {
				error = err.message || 'Failed to load plugin page';
				logger.error('Plugin page load error:', err);
				loading = false;
			}
		}
	}

	onMount(() => {
		const currentId = $page.params.pluginId;
		if (currentId) {
			loadPlugin(currentId);
		}
	});

	afterNavigate(() => {
		const currentId = $page.params.pluginId;
		if (currentId && currentId !== loadedPluginId) {
			loadPlugin(currentId);
		}
	});

	onDestroy(() => {
		cleanupPlugin();
	});
</script>

<div class="flex h-[calc(100dvh-4rem)] flex-col overflow-hidden md:h-dvh">
	{#if loading}
		<div class="flex flex-1 items-center justify-center">
			<div class="text-fg-muted">Loading plugin...</div>
		</div>
	{:else if error}
		<div class="flex flex-1 items-center justify-center">
			<div class="text-danger">{error}</div>
		</div>
	{:else}
		<!-- Header -->
		<PageHeader sticky={false}>
			<div class="flex items-center gap-6 w-full">
				<div class="flex items-center gap-3">
					{#if pageInfo?.icon_svg}
						<svg class="w-5 h-5 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={pageInfo.icon_svg} />
						</svg>
					{:else}
						<svg class="w-5 h-5 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10l-2 1m0 0l-2-1m2 1v2.5M20 7l-2 1m2-1l-2-1m2 1v2.5M14 4l-2-1-2 1M4 7l2-1M4 7l2 1M4 7v2.5M12 21l-2-1m2 1l2-1m-2 1v-2.5M6 18l-2-1v-2.5M18 18l2-1v-2.5" />
						</svg>
					{/if}
					<span class="text-sm font-semibold text-fg">{pageInfo?.label || 'Plugin'}</span>
				</div>

				<!-- Plugin-provided header actions -->
				<div bind:this={headerActionsEl} class="flex items-center gap-3 flex-1"></div>
			</div>
		</PageHeader>

		<!-- Plugin Content -->
		<div bind:this={containerEl} class="plugin-container min-h-0 flex-1 overflow-auto"></div>
	{/if}
</div>
