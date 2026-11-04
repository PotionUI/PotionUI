<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import NodeIconButton from './NodeIconButton.svelte';
	import type { NodeKind } from '$lib/elixirs/types';

	interface Props {
		data: any;
		kind: NodeKind;
		phone?: boolean;
	}

	let { data, kind, phone = false }: Props = $props();
</script>

<header class="node-head" class:phone>
	{#if kind === 'llm'}
		<span class="ai-icon" aria-hidden="true"><Icon name="sparkles" className="h-3 w-3" /></span>
	{:else}
		<span class="kind-icon" aria-hidden="true">
			<Icon name={kind === 'media' ? 'image' : 'flask'} className="h-4 w-4" />
		</span>
	{/if}
	<h3 class="min-w-0 flex-1 truncate font-semibold text-fg">{data.label}</h3>
	<div class="flex shrink-0 items-center">
		{#if kind !== 'media' && !data.readOnly}
			<NodeIconButton
				icon="refresh"
				large={phone}
				label={data.run ? `Run ${data.label} again` : `Run ${data.label}`}
				tip={data.runAgainBlocked ?? 'Run this step again. Steps after it will need a rerun.'}
				disabled={!!data.runAgainBlocked}
				onclick={() => data.onrunagain(data.node.id)}
			/>
		{:else if kind === 'media'}
			<NodeIconButton icon="image" large={phone} label="Change files" onclick={() => data.onsettings(data.node.id)} />
		{/if}
		<NodeIconButton icon="sliders" large={phone} label="{data.label} settings" tip="Step settings" onclick={() => data.onsettings(data.node.id)} />
		{#if !data.readOnly}
			<NodeIconButton icon="more" large={phone} label="More for {data.label}" tip="More" haspopup onclick={(event) => data.onmore(event.currentTarget, data.node.id)} />
		{/if}
	</div>
</header>

<style>
	.node-head {
		display: flex;
		height: 56px;
		align-items: center;
		gap: 10px;
		padding: 0 12px 0 16px;
		border-bottom: 1px solid rgb(var(--line));
		font-size: 13px;
	}
	.node-head.phone {
		height: 52px;
		padding-right: 6px;
	}
	.kind-icon {
		display: inline-flex;
		color: rgb(var(--fg-subtle));
	}
	.ai-icon {
		display: inline-grid;
		place-items: center;
		width: 20px;
		height: 20px;
		flex: none;
		border-radius: 4px;
		color: rgb(var(--on-scrim));
		background: linear-gradient(145deg, rgb(var(--ai-1)), rgb(var(--ai-2)) 55%, rgb(var(--ai-3)));
	}
</style>
