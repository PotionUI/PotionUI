<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { Badge, Spinner, EmptyState, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import type { AdminToolsetEntry } from '$lib/types/llm';

	// Tool governance for one LLM configuration - the same tool can be enabled
	// here and disabled on another config, so this panel always operates on
	// `configId`, never globally (see src.features.llm.tools.governance).
	export let configId: string;

	let tools: AdminToolsetEntry[] = [];
	let loading = true;
	// Per-tool in-flight guard so a slow PUT can't be raced by a second click
	// on the same toggle.
	let saving: Record<string, boolean> = {};

	$: groupedTools = Object.entries(
		tools.reduce<Record<string, AdminToolsetEntry[]>>((acc, tool) => {
			(acc[tool.group] ??= []).push(tool);
			return acc;
		}, {})
	).sort(([a], [b]) => a.localeCompare(b));

	// Re-loads whenever the panel is mounted for a different config - the
	// {#key configId} wrapper at the call site remounts this component, so
	// onMount alone is enough (no need to watch for prop changes).
	onMount(load);

	async function load() {
		try {
			loading = true;
			const response = await adminApi.getAdminToolset(configId);
			if (response.success && response.data) {
				tools = response.data;
			}
		} catch (error) {
			logger.error('Failed to load toolset:', error);
			toasts.error('Failed to load toolset');
		} finally {
			loading = false;
		}
	}

	async function setGovernance(tool: AdminToolsetEntry, changes: { enabled?: boolean; locked?: boolean }) {
		saving = { ...saving, [tool.name]: true };
		try {
			const response = await adminApi.updateToolGovernance(configId, tool.name, changes);
			if (response.success && response.data) {
				tools = tools.map((t) =>
					t.name === tool.name ? { ...t, enabled: response.data!.enabled, locked: response.data!.locked } : t
				);
			}
		} catch (error) {
			logger.error(`Failed to update tool governance for ${tool.name}:`, error);
			toasts.error(`Failed to update "${tool.label}"`);
		} finally {
			saving = { ...saving, [tool.name]: false };
		}
	}
</script>

{#if loading}
	<div class="flex flex-col items-center justify-center py-16">
		<Spinner size="lg" />
		<p class="text-sm text-fg-muted mt-4">Loading toolset…</p>
	</div>
{:else if tools.length === 0}
	<EmptyState icon="shield" title="No tools registered" description="No chat tools are registered yet." compact />
{:else}
	<div class="space-y-5">
		<p class="text-xs text-fg-muted max-w-2xl">
			Tools this configuration can use in chat. Off removes a tool from every session using this
			configuration; Locked keeps it on for everyone using this configuration — users can't opt out.
		</p>
		{#each groupedTools as [group, groupTools] (group)}
			<DetailSection label={group} padded={false}>
				<div class="divide-y divide-line">
					{#each groupTools as tool (tool.name)}
						<div class="flex items-center gap-3 px-4 py-3" data-testid="toolset-row" data-tool={tool.name}>
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2">
									<span class="text-sm font-medium text-fg truncate">{tool.label}</span>
									{#if !tool.enabled}
										<span data-testid="tool-status-badge"><Badge variant="neutral" size="sm">Off</Badge></span>
									{:else if tool.locked}
										<span data-testid="tool-status-badge"><Badge variant="info" size="sm">Locked</Badge></span>
									{/if}
								</div>
								{#if tool.user_description}
									<p class="text-xs text-fg-muted mt-0.5">{tool.user_description}</p>
								{/if}
							</div>
							<div class="flex items-center gap-4 flex-shrink-0">
								<label class="flex items-center gap-1.5" for={`toolset-enabled-${tool.name}`}>
									<span class="text-2xs text-fg-subtle">Enabled</span>
									<Switch
										id={`toolset-enabled-${tool.name}`}
										label="Enabled"
										checked={tool.enabled}
										busy={!!saving[tool.name]}
										onchange={(checked) => setGovernance(tool, { enabled: checked })}
									/>
								</label>
								<label class="flex items-center gap-1.5" for={`toolset-locked-${tool.name}`}>
									<span class="text-2xs text-fg-subtle">Locked</span>
									<Switch
										id={`toolset-locked-${tool.name}`}
										label="Locked"
										checked={tool.locked}
										disabled={!tool.enabled}
										busy={!!saving[tool.name]}
										onchange={(checked) => setGovernance(tool, { locked: checked })}
									/>
								</label>
							</div>
						</div>
					{/each}
				</div>
			</DetailSection>
		{/each}
	</div>
{/if}
