<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import { Alert, CopyButton, Input, SegmentedControl } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { ApiKey, ApiKeyCreated, ApiKeyExpiryDays, ApiKeyScope } from '$lib/services/api/apiKeys';
	import { confirmDialog } from '$lib/stores/confirm';
	import { parseApiError } from '$lib/utils/apiErrors';
	import KeyAccessFields from './KeyAccessFields.svelte';
	import {
		allowedDays,
		buildCreateRequest,
		canSubmit,
		expiryNote,
		expiryOptions,
		initialDraft,
		initialSecretState,
		secretReducer,
		setDays,
		setScope,
		type DialogContext,
		type SecretEvent
	} from './apiKeyDialog';
	import { curlSnippet, mcpClientConfig } from './apiKeySnippets';

	let {
		ctx,
		onClose,
		onCreated
	}: {
		ctx: DialogContext;
		onClose: () => void;
		onCreated: (key: ApiKey) => void;
	} = $props();

	let draft = $state(untrack(() => initialDraft(ctx)));
	let creating = $state(false);
	let error = $state<string | null>(null);
	let created = $state<ApiKeyCreated | null>(null);
	let secretState = $state(initialSecretState);

	const options = $derived(expiryOptions(draft, ctx));
	const days = $derived(allowedDays(draft, ctx));
	const note = $derived(expiryNote(draft, ctx));
	const origin = typeof window === 'undefined' ? '' : window.location.origin;
	const withMcp = $derived(created ? created.key.scopes.includes('mcp') : false);
	const curl = $derived(created ? curlSnippet(origin, created.secret) : '');
	const mcpConfig = $derived(created ? mcpClientConfig(origin, created.secret) : '');

	function markCopied() {
		secretState = secretReducer(secretState, 'copy');
	}

	function onScope(scope: ApiKeyScope, on: boolean) {
		draft = setScope(draft, scope, on, ctx);
	}

	function onDays(id: string) {
		draft = setDays(draft, id === 'never' ? null : (Number(id) as ApiKeyExpiryDays));
	}

	async function submit() {
		if (creating || !canSubmit(draft, ctx)) return;
		creating = true;
		error = null;
		try {
			const response = await api.createApiKey(buildCreateRequest(draft, ctx));
			if (response.success && response.data) {
				created = response.data;
				onCreated(response.data.key);
			} else {
				error = response.message || 'Could not create the key.';
			}
		} catch (err) {
			error = parseApiError(err).message || 'Could not create the key.';
		} finally {
			creating = false;
		}
	}

	async function dispatchSecret(event: SecretEvent) {
		secretState = secretReducer(secretState, event);
		if (secretState.stage === 'guarding') {
			const leave = await confirmDialog({
				title: 'Close without copying?',
				message: 'The key can’t be shown again. If you close now you will have to create a new one.',
				variant: 'warning',
				confirmLabel: 'Close anyway',
				cancelLabel: 'Keep open'
			});
			secretState = secretReducer(secretState, leave ? 'closeAnyway' : 'keepOpen');
		}
		if (secretState.stage === 'closed') onClose();
	}
</script>

<BaseModal
	isOpen
	title={created ? 'Key created' : 'Create API key'}
	sizeClass={created ? 'md:max-w-xl md:w-full' : 'md:max-w-lg md:w-full'}
	on:close={() => (created ? dispatchSecret('requestClose') : onClose())}
>
	{#if !created}
		<form
			class="space-y-5 p-4 md:p-5"
			onsubmit={(event) => {
				event.preventDefault();
				void submit();
			}}
		>
			<div>
				<label for="api-key-name" class="label">Name</label>
				<Input id="api-key-name" bind:value={draft.name} placeholder="e.g. Nightly backup" maxlength={80} autocomplete="off" disabled={creating} />
			</div>
			<KeyAccessFields {draft} {ctx} {onScope} />
			<div>
				<p class="label">Expires</p>
				<div>
					<SegmentedControl
						variant="toggle"
						ariaLabel="Expires after"
						items={options.map((option) => ({ id: option.days === null ? 'never' : String(option.days), label: option.label, disabled: option.disabled }))}
						selected={days === null ? 'never' : String(days)}
						onSelect={onDays}
					/>
				</div>
				{#if note}
					<p class="mt-2 text-sm {note.tone === 'warning' ? 'text-warning' : 'text-fg-subtle'}">{note.text}</p>
				{/if}
			</div>
			{#if error}
				<Alert variant="danger" density="compact" icon>{error}</Alert>
			{/if}
		</form>
	{:else}
		<div class="space-y-5 p-4 md:p-5">
			<div>
				<p class="label">Key</p>
				<code class="block select-all break-all rounded border border-line-strong bg-surface-2 p-3 font-mono text-sm text-fg">{created.secret}</code>
				<div class="mt-2">
					<CopyButton text={created.secret} label="Copy key" variant="secondary" size="sm" oncopy={markCopied} />
				</div>
			</div>
			<Alert variant="warning" density="compact" icon>Copy this key now. You won’t see it again.</Alert>
			<div>
				<div class="mb-1 flex items-center justify-between gap-2">
					<p class="label !mb-0">Script</p>
					<CopyButton text={curl} label="Copy" size="xs" oncopy={markCopied} />
				</div>
				<pre class="overflow-x-auto whitespace-pre-wrap break-all rounded border border-line bg-surface-2 p-3 font-mono text-sm text-fg-muted">{curl}</pre>
			</div>
			{#if withMcp}
				<div>
					<div class="mb-1 flex items-center justify-between gap-2">
						<p class="label !mb-0">MCP client</p>
						<CopyButton text={mcpConfig} label="Copy" size="xs" oncopy={markCopied} />
					</div>
					<pre class="overflow-x-auto whitespace-pre-wrap break-all rounded border border-line bg-surface-2 p-3 font-mono text-sm text-fg-muted">{mcpConfig}</pre>
				</div>
			{/if}
		</div>
	{/if}
	<svelte:fragment slot="footer">
		{#if !created}
			<ConfirmFooter confirmLabel="Create key" busy={creating} confirmDisabled={!canSubmit(draft, ctx)} onCancel={onClose} onConfirm={submit} />
		{:else}
			<ConfirmFooter hideCancel confirmLabel="I have saved it" onCancel={onClose} onConfirm={() => dispatchSecret('done')} />
		{/if}
	</svelte:fragment>
</BaseModal>
