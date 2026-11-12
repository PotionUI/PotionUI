<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { UserApiAccess } from '$lib/services/api/apiKeys';
	import { Switch } from '$lib/components/ui';
	import { toasts } from '$lib/stores/toast';
	import { parseApiError } from '$lib/utils/apiErrors';
	import { accessChangeMessage, mcpUsable, nextAccess, type AccessField } from './userAccess';

	let {
		userId,
		name,
		stacked = false,
		onChanged
	}: {
		userId: string;
		name: string;
		stacked?: boolean;
		onChanged?: (access: UserApiAccess) => void;
	} = $props();

	let access = $state<UserApiAccess | null>(null);
	let failed = $state(false);
	let busy = $state<AccessField | null>(null);
	let version = $state(0);

	async function load() {
		failed = false;
		try {
			const response = await api.getUserApiAccess(userId);
			if (response.success && response.data) {
				access = response.data;
				onChanged?.(response.data);
			} else failed = true;
		} catch {
			failed = true;
		}
	}

	async function change(field: AccessField, value: boolean) {
		if (!access || busy) return;
		busy = field;
		try {
			const response = await api.setUserApiAccess(userId, nextAccess(access, field, value));
			if (!response.success || !response.data) throw new Error(response.message || 'Could not save the change.');
			access = { ...response.data, keys: response.data.keys ?? access.keys };
			onChanged?.(access);
			(value ? toasts.success : toasts.info)(accessChangeMessage(name, field, value));
		} catch (err) {
			toasts.error(parseApiError(err).message || 'Could not save the change.');
			await load();
			version += 1;
		} finally {
			busy = null;
		}
	}

	onMount(load);
</script>

{#if failed}
	<button type="button" class="text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline" onclick={load}>Could not load access. Retry</button>
{:else if stacked}
	{#key version}
		<div class="divide-y divide-line" data-access-for={userId}>
			<div class="flex items-start justify-between gap-6 pb-3">
				<div>
					<p class="mb-1 text-sm font-medium text-fg">Allow API keys</p>
					<p class="text-sm text-fg-muted">Lets this user create keys for scripts and MCP clients to act as them.</p>
				</div>
				<Switch
					testid="access-keys-{userId}"
					label="Allow API keys"
					checked={access?.keys_enabled ?? false}
					disabled={!access}
					busy={busy === 'keys_enabled'}
					onchange={(checked) => change('keys_enabled', checked)}
				/>
			</div>
			<div class="flex items-start justify-between gap-6 pt-3">
				<div>
					<p class="mb-1 text-sm font-medium text-fg">Allow MCP</p>
					<p class="text-sm text-fg-muted">Lets their keys connect MCP clients.</p>
				</div>
				<Switch
					testid="access-mcp-{userId}"
					label="Allow MCP"
					checked={access ? mcpUsable(access) : false}
					disabled={!access || !access.keys_enabled}
					busy={busy === 'mcp_enabled'}
					onchange={(checked) => change('mcp_enabled', checked)}
				/>
			</div>
		</div>
	{/key}
{:else}
	{#key version}
		<div class="flex items-center gap-4" data-access-for={userId}>
			<label class="flex items-center gap-2 text-sm text-fg-muted">
				<Switch
					testid="access-keys-{userId}"
					label="API keys for {name}"
					checked={access?.keys_enabled ?? false}
					disabled={!access}
					busy={busy === 'keys_enabled'}
					onchange={(checked) => change('keys_enabled', checked)}
				/>
				API keys
			</label>
			<label class="flex items-center gap-2 text-sm text-fg-muted">
				<Switch
					testid="access-mcp-{userId}"
					label="MCP for {name}"
					checked={access ? mcpUsable(access) : false}
					disabled={!access || !access.keys_enabled}
					busy={busy === 'mcp_enabled'}
					onchange={(checked) => change('mcp_enabled', checked)}
				/>
				MCP
			</label>
		</div>
	{/key}
{/if}
