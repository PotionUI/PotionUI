<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { ApiKey, ApiKeyLimits } from '$lib/services/api/apiKeys';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { parseApiError } from '$lib/utils/apiErrors';
	import ApiKeysCard from './ApiKeysCard.svelte';
	import CreateKeyDialog from './CreateKeyDialog.svelte';
	import EditKeyDialog from './EditKeyDialog.svelte';
	import McpToolsCard from './McpToolsCard.svelte';
	import { mcpKeysOf } from './apiKeyView';

	let {
		isAdmin,
		onCount
	}: {
		isAdmin: boolean;
		onCount?: (count: number) => void;
	} = $props();

	const DEFAULT_LIMITS: ApiKeyLimits = { keys_enabled: true, mcp_enabled: true, scopes_allowed: ['read', 'generate', 'content', 'mcp'], max_lifetime_days: null };

	let keys = $state<ApiKey[]>([]);
	let limits = $state<ApiKeyLimits>(DEFAULT_LIMITS);
	let loading = $state(true);
	let failed = $state(false);
	let creating = $state(false);
	let editing = $state<ApiKey | null>(null);
	let revokingId = $state<string | null>(null);
	let toolsKeyId = $state<string | null>(null);
	let toolsEl = $state<HTMLElement>();

	const ctx = $derived({ isAdmin, limits });
	const mcpKeys = $derived(mcpKeysOf(keys));

	function publish() {
		onCount?.(keys.filter((key) => key.status === 'active').length);
	}

	async function load() {
		loading = true;
		failed = false;
		try {
			const response = await api.listApiKeys();
			if (response.success && response.data) {
				keys = response.data.keys;
				limits = response.data.limits;
				publish();
			} else {
				failed = true;
			}
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	function replaceKey(next: ApiKey) {
		keys = keys.map((key) => (key.id === next.id ? next : key));
		publish();
	}

	async function revoke(key: ApiKey) {
		const ok = await confirmDialog({
			title: `Revoke “${key.name}”?`,
			message: 'Anything using this key stops working right away: scripts, MCP clients and open connections. This can’t be undone.',
			variant: 'danger',
			confirmLabel: 'Revoke key'
		});
		if (!ok) return;
		revokingId = key.id;
		editing = null;
		try {
			const response = await api.revokeApiKey(key.id);
			if (!response.success) throw new Error(response.message || 'Failed to revoke the key.');
			toasts.success('Key revoked');
			await load();
		} catch (err) {
			toasts.error(parseApiError(err).message || 'Failed to revoke the key.');
		} finally {
			revokingId = null;
		}
	}

	function chooseTools(key: ApiKey) {
		editing = null;
		toolsKeyId = key.id;
		toolsEl?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	onMount(load);
</script>

<ApiKeysCard
	{keys}
	{limits}
	{loading}
	{failed}
	{revokingId}
	onCreate={() => (creating = true)}
	onEdit={(key) => (editing = key)}
	onRevoke={revoke}
	onRetry={load}
/>

<div bind:this={toolsEl}>
	<McpToolsCard keys={mcpKeys} mcpEnabled={limits.mcp_enabled} bind:selectedKeyId={toolsKeyId} />
</div>

{#if creating}
	<CreateKeyDialog {ctx} onClose={() => (creating = false)} onCreated={() => void load()} />
{/if}

{#if editing}
	<EditKeyDialog apiKey={editing} {ctx} onClose={() => (editing = null)} onSaved={replaceKey} onRevoke={revoke} onChooseTools={chooseTools} />
{/if}
