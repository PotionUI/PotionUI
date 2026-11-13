<script lang="ts">
	import { untrack } from 'svelte';
	import { Alert, Card, EmptyState, Spinner } from '$lib/components/ui';
	import LoadErrorState from '$lib/components/ui/LoadErrorState.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import type { ApiKey } from '$lib/services/api/apiKeys';
	import type { McpToolsListing, McpToolsPatch } from '$lib/services/api/mcpTools';
	import { parseApiError } from '$lib/utils/apiErrors';
	import { toasts } from '$lib/stores/toast';
	import McpKeyPicker from './McpKeyPicker.svelte';
	import McpToolsList from './McpToolsList.svelte';
	import { applyChange, isEmptyChange, keyPatchOf, type ToolsChange } from './mcpToolsView';

	let {
		keys,
		mcpEnabled,
		selectedKeyId = $bindable(null)
	}: {
		keys: ApiKey[];
		mcpEnabled: boolean;
		selectedKeyId?: string | null;
	} = $props();

	let listing = $state<McpToolsListing | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let unavailable = $state(false);
	let saved = $state(false);
	let seq = 0;
	let inflight = 0;
	let reloadAfter = false;
	let chain: Promise<void> = Promise.resolve();
	let savedTimer: ReturnType<typeof setTimeout> | undefined;

	const keyId = $derived(keys.some((key) => key.id === selectedKeyId) ? selectedKeyId : null);

	async function fetchListing(forKey: string | null): Promise<McpToolsListing | null> {
		const response = forKey ? await api.getMcpKeyTools(forKey) : await api.getMcpTools();
		return response.success && response.data ? response.data : null;
	}

	async function load(forKey: string | null) {
		const mine = ++seq;
		loading = true;
		failed = false;
		try {
			const next = await fetchListing(forKey);
			if (mine !== seq) return;
			if (next) {
				listing = next;
				unavailable = false;
			} else failed = true;
		} catch (err) {
			if (mine === seq) {
				if (!forKey && (err as { response?: { status?: number } })?.response?.status === 404) unavailable = true;
				else failed = true;
			}
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		const forKey = keyId;
		untrack(() => void load(forKey));
	});

	function flashSaved() {
		saved = true;
		clearTimeout(savedTimer);
		savedTimer = setTimeout(() => (saved = false), 2200);
	}

	function patchOf(change: ToolsChange): McpToolsPatch {
		const patch: McpToolsPatch = {};
		if (change.policy) patch.policy = change.policy;
		if (change.overrides) patch.overrides = change.overrides;
		return patch;
	}

	async function send(forKey: string | null, change: ToolsChange): Promise<McpToolsListing> {
		const response = forKey ? await api.patchMcpKeyTools(forKey, keyPatchOf(change)) : await api.patchMcpTools(patchOf(change));
		if (!response.success || !response.data) throw new Error(response.message || 'Could not save');
		return response.data;
	}

	function commit(change: ToolsChange) {
		if (!listing || isEmptyChange(change)) return;
		const forKey = keyId;
		const before = listing;
		listing = applyChange(listing, change);
		inflight += 1;
		chain = chain.then(async () => {
			try {
				const confirmed = await send(forKey, change);
				if (inflight === 1 && keyId === forKey) listing = confirmed;
				flashSaved();
			} catch (err) {
				const status = (err as { response?: { status?: number } })?.response?.status;
				toasts.error(parseApiError(err).message || 'Could not save the change.');
				if (keyId === forKey) {
					if (inflight === 1 && status !== 409) listing = before;
					else reloadAfter = true;
				}
			} finally {
				inflight -= 1;
				if (inflight === 0 && reloadAfter) {
					reloadAfter = false;
					void load(keyId);
				}
			}
		});
	}
</script>

<Card>
	<div class="mb-3 flex flex-wrap items-start justify-between gap-3">
		<h2 class="label !mb-0">MCP tools</h2>
		<span class="flex items-center gap-1 text-sm text-fg-subtle {saved ? '' : 'invisible'}" role="status">
			<Icon name="check" className="h-3.5 w-3.5 text-success" />
			Saved
		</span>
	</div>

	{#if !mcpEnabled}
		<Alert variant="info" density="compact" icon class="mb-4">MCP is turned off by your administrator.</Alert>
	{/if}
	{#if !keyId && !unavailable}
		<p class="mb-4 text-sm text-fg-subtle">The tools your keys can use. A key can narrow this list, never widen it.</p>
	{/if}

	{#if unavailable}
		<div role="status"><EmptyState compact icon="key" title="MCP tools are not available yet" /></div>
	{:else if loading && !listing}
		<div class="flex items-center justify-center py-8"><Spinner /></div>
	{:else if failed && !listing}
		<LoadErrorState message="The tools could not be loaded." onRetry={() => load(keyId)} retrying={loading} compact />
	{:else if listing}
		<div class="space-y-4" class:opacity-60={loading}>
			{#if keys.length}
				<McpKeyPicker {keys} selectedId={keyId} onSelect={(next) => (selectedKeyId = next)} />
			{/if}
			{#key listing.key_id ?? 'default'}
				<McpToolsList {listing} onChange={commit} onChangeDefault={() => (selectedKeyId = null)} />
			{/key}
		</div>
	{/if}
</Card>
