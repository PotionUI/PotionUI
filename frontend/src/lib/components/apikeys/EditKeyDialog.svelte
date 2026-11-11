<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import { Alert, Button, Input } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { ApiKey, ApiKeyScope } from '$lib/services/api/apiKeys';
	import { parseApiError } from '$lib/utils/apiErrors';
	import KeyAccessFields from './KeyAccessFields.svelte';
	import { adminBlockedByExpiry, buildUpdateRequest, draftFromKey, hasChanges, setScope, type DialogContext } from './apiKeyDialog';
	import { describeEvents, type ActivityLine } from './apiKeyEvents';
	import { formatKeyDate, isMigratedKey, keyFingerprint } from './apiKeyView';

	let {
		apiKey,
		ctx,
		onClose,
		onSaved,
		onRevoke,
		onChooseTools
	}: {
		apiKey: ApiKey;
		ctx: DialogContext;
		onClose: () => void;
		onSaved: (key: ApiKey) => void;
		onRevoke: (key: ApiKey) => void;
		onChooseTools: (key: ApiKey) => void;
	} = $props();

	let draft = $state(untrack(() => draftFromKey(apiKey)));
	let saving = $state(false);
	let error = $state<string | null>(null);
	let activity = $state<ActivityLine[]>([]);

	const request = $derived(buildUpdateRequest(apiKey, draft, ctx));
	const canSave = $derived(draft.name.trim().length > 0 && hasChanges(request));
	const mcpOn = $derived(apiKey.scopes.includes('mcp') && draft.scopes.mcp);
	const tools = $derived(apiKey.mcp_tools);
	const details = $derived([
		{ label: 'Key', value: keyFingerprint(apiKey) },
		{ label: 'Created', value: formatKeyDate(apiKey.created_at) },
		{ label: 'Expires', value: formatKeyDate(apiKey.expires_at) }
	]);

	onMount(async () => {
		try {
			const response = await api.listApiKeyEvents(apiKey.id, 1, 10);
			if (response.success && response.data) activity = describeEvents(response.data.items);
		} catch {
			activity = [];
		}
	});

	function onScope(scope: ApiKeyScope, on: boolean) {
		draft = setScope(draft, scope, on, ctx);
	}

	async function save() {
		if (saving || !canSave) return;
		saving = true;
		error = null;
		try {
			const response = await api.updateApiKey(apiKey.id, request);
			if (response.success && response.data) {
				onSaved(response.data);
				onClose();
			} else {
				error = response.message || 'Could not save the key.';
			}
		} catch (err) {
			error = parseApiError(err).message || 'Could not save the key.';
		} finally {
			saving = false;
		}
	}
</script>

<BaseModal isOpen title="Edit key" sizeClass="md:max-w-xl md:w-full" on:close={onClose}>
	<form
		class="space-y-5 p-4 md:p-5"
		onsubmit={(event) => {
			event.preventDefault();
			void save();
		}}
	>
		<div>
			<label for="api-key-edit-name" class="label">Name</label>
			<Input id="api-key-edit-name" bind:value={draft.name} maxlength={80} autocomplete="off" disabled={saving} />
		</div>
		<KeyAccessFields {draft} {ctx} adminBlockedByExpiry={adminBlockedByExpiry(apiKey.expires_at)} {onScope}>
			{#snippet mcpExtra()}
				{#if mcpOn && tools}
					<div class="mt-2 flex items-center justify-between gap-2">
						<p class="font-mono text-sm tabular-nums text-fg-muted">{tools.exposed} of {tools.total} tools on</p>
						<Button size="xs" onclick={() => onChooseTools(apiKey)}>Choose tools</Button>
					</div>
				{:else if mcpOn}
					<div class="mt-2 flex justify-end">
						<Button size="xs" onclick={() => onChooseTools(apiKey)}>Choose tools</Button>
					</div>
				{/if}
			{/snippet}
		</KeyAccessFields>
		<dl class="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
			{#each details as item (item.label)}
				<div class="min-w-0">
					<dt class="text-sm text-fg-subtle">{item.label}</dt>
					<dd class="font-mono text-sm tabular-nums text-fg-muted [overflow-wrap:anywhere]">{item.value}</dd>
				</div>
			{/each}
		</dl>
		{#if isMigratedKey(apiKey)}
			<p class="text-sm text-fg-subtle">Migrated from an MCP token.</p>
		{/if}
		{#if activity.length}
			<div>
				<p class="label">Recent activity</p>
				<ol class="divide-y divide-line rounded border border-line">
					{#each activity as line, i (i)}
						<li class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2">
							<span class="w-28 flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{line.when}</span>
							<span class="text-sm text-fg">{line.what}</span>
							{#if line.detail}<span class="min-w-0 text-sm text-fg-muted">{line.detail}</span>{/if}
						</li>
					{/each}
				</ol>
			</div>
		{/if}
		{#if error}
			<Alert variant="danger" density="compact" icon>{error}</Alert>
		{/if}
	</form>
	<svelte:fragment slot="footer">
		<ConfirmFooter confirmLabel="Save changes" busy={saving} confirmDisabled={!canSave} onCancel={onClose} onConfirm={save}>
			{#snippet leftActions()}
				<Button variant="ghost" size="sm" icon="ban" class="text-danger hover:text-danger" onclick={() => onRevoke(apiKey)}>Revoke key</Button>
			{/snippet}
		</ConfirmFooter>
	</svelte:fragment>
</BaseModal>
