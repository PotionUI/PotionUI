<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { SegmentedControl, Switch } from '$lib/components/ui';
	import EffectNote from './EffectNote.svelte';
	import {
		GRANTABLE_SCOPES,
		keysEnabledOf,
		keysSwitchNote,
		lifetimeNote,
		lifetimeOf,
		lifetimeOptions,
		mcpSwitchNote,
		scopeAllowed,
		scopeLoweredNote,
		scopeNote,
		scopeTitle,
		withScope,
		type GrantableScope
	} from './accessPanel';

	let {
		settings,
		savedSnapshot,
		onSettingChange
	}: {
		settings: Record<string, any>;
		savedSnapshot: string;
		onSettingChange: (key: string, value: unknown) => void;
	} = $props();

	const saved = $derived(JSON.parse(savedSnapshot || '{}') as Record<string, any>);
	const keysOn = $derived(keysEnabledOf(settings));
	const keysSaved = $derived(keysEnabledOf(saved));
	const mcpOn = $derived(Boolean(settings.mcp_enabled));
	const mcpSaved = $derived(Boolean(saved.mcp_enabled));
	const days = $derived(lifetimeOf(settings));
	const options = $derived(lifetimeOptions(days).map((option) => ({ id: String(option.days), label: option.label })));
</script>

<DetailSection label="API keys" padded={false}>
	<div class="divide-y divide-line px-4 sm:px-5" data-testid="api-keys-access">
		<div class="py-3">
			<div class="flex items-center justify-between gap-6">
				<label for="api-keys-enabled" class="text-sm font-medium text-fg">API keys</label>
				<Switch id="api-keys-enabled" testid="api-keys-enabled" label="API keys" checked={keysOn} onchange={(checked) => onSettingChange('api_keys_enabled', checked)} />
			</div>
			<EffectNote note={keysSwitchNote(keysSaved, keysOn)} testid="api-keys-enabled-note" />
		</div>

		<div class="py-3">
			<div class="flex items-center justify-between gap-6">
				<label for="mcp-enabled" class="text-sm font-medium text-fg">MCP</label>
				<Switch id="mcp-enabled" testid="mcp-enabled" label="MCP" checked={mcpOn} onchange={(checked) => onSettingChange('mcp_enabled', checked)} />
			</div>
			<EffectNote note={mcpSwitchNote(mcpSaved, mcpOn)} testid="mcp-enabled-note" />
		</div>

		<div class="py-3">
			<p class="text-sm font-medium text-fg">Keys may be allowed to</p>
			<div class="mt-2 divide-y divide-line rounded border border-line px-3">
				{#each GRANTABLE_SCOPES as scope (scope)}
					{@const on = scopeAllowed(settings, scope)}
					{@const note = scopeNote(scope)}
					<div class="py-2.5" data-scope-row={scope}>
						<div class="flex items-start justify-between gap-3">
							<div class="min-w-0">
								<label for="scope-{scope}" class="text-sm font-medium text-fg">{scopeTitle(scope)}</label>
								<code class="ml-1.5 font-mono text-sm text-fg-subtle">{scope}</code>
								{#if note}<p class="mt-0.5 text-sm text-warning">{note}</p>{/if}
							</div>
							<Switch
								id="scope-{scope}"
								testid="scope-{scope}"
								label={scopeTitle(scope)}
								checked={on}
								onchange={(checked) => onSettingChange('api_key_scopes_allowed', withScope(settings, scope as GrantableScope, checked))}
							/>
						</div>
						<EffectNote note={scopeLoweredNote(scopeAllowed(saved, scope), on)} testid="scope-{scope}-note" />
					</div>
				{/each}
			</div>
		</div>

		<div class="py-3">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<p class="text-sm font-medium text-fg">Longest key lifetime</p>
				<SegmentedControl
					variant="toggle"
					ariaLabel="Longest key lifetime"
					items={options}
					selected={String(days)}
					onSelect={(id) => onSettingChange('api_key_max_lifetime_days', Number(id))}
				/>
			</div>
			<p class="mt-1.5 text-sm text-fg-subtle" data-testid="lifetime-note">{lifetimeNote(days)}</p>
		</div>
	</div>
</DetailSection>
