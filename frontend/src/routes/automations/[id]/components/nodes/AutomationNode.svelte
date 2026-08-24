<script lang="ts">
	/**
	 * Unified node card for all three kinds (trigger/condition/action). Kind is
	 * differentiated by icon + mono uppercase micro-label only (never color);
	 * the only signal-blue on the card comes from selection/template values.
	 *
	 * Handle logic is fully delegated to `nodeHandles.ts` (`hasTargetHandle`,
	 * `getSourceHandles`) — this component never re-derives port lists. Output
	 * FIELDS (the data contract, `def.outputs`) are display-only rows and never
	 * get a `<Handle>`: a source handle keyed on an output would let the user
	 * draw a data edge the backend's single-flow-edge engine can't execute.
	 *
	 * The two data sections answer the two questions the canvas has to answer,
	 * and neither is a re-render of the config form (that lives in the Inspector):
	 *
	 * - PROVIDES: what a downstream node can collect from this one, shown under
	 *   the namespace it's actually reachable through (`event.*` for a trigger,
	 *   `upstream.<node_id>.*` otherwise). The key alone is useless; the
	 *   reference is the thing you type.
	 * - ACCEPTS: only the fields that read run data (`templatable` / `input_ref`),
	 *   with their current binding. A node with none consumes nothing upstream,
	 *   and the section is omitted.
	 */
	import { Handle, Position } from '@xyflow/svelte';
	import Icon from '$lib/components/Icon.svelte';
	import RunStatusBadge from '$lib/components/automation/RunStatusBadge.svelte';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { nodeStatuses } from '$lib/stores/automationRuns';
	import { hasTargetHandle, getSourceHandles } from '$lib/automations/nodeHandles';
	import { nodeDetail } from '$lib/automations/nodeDetail';
	import { getDataInputs, outputPrefix, outputRef } from '$lib/automations/nodeData';
	import type { FlowNodeData } from '$lib/stores/automationEditor';
	import type { NodeTypeDef } from '$lib/types/automations';

	let { id, data, selected }: { id: string; data: FlowNodeData; selected?: boolean } = $props();

	let nodeTypeDef = $derived($automationNodeTypes.find((t) => t.key === data.nodeType));
	let status = $derived($nodeStatuses[id]);
	let kindLabel = $derived(data.kind ? data.kind.toUpperCase() : '');

	/**
	 * A saved graph can reference a node type the catalog no longer has (a plugin
	 * was disabled). Such a node must still render its handles, or its edges
	 * visually detach. `kind` is recoverable from the node itself, and that's all
	 * the handle helpers need.
	 */
	let handleDef = $derived(
		nodeTypeDef ?? ({ kind: data.kind, output_ports: [] } as unknown as NodeTypeDef)
	);

	let showTarget = $derived(hasTargetHandle(handleDef));
	let sourceHandles = $derived(getSourceHandles(handleDef, data.config));

	/** Only the fields that read run data — not the whole config form. */
	let dataInputs = $derived(getDataInputs(nodeTypeDef, data.config));

	/** The namespace downstream nodes reach this node's outputs through. */
	let prefix = $derived(outputPrefix(data.kind, id));

	let outputs = $derived(nodeTypeDef?.outputs ?? []);
	let hasOutputs = $derived(outputs.length > 0 || nodeTypeDef?.dynamic_outputs === true);
</script>

<div
	class="{$nodeDetail === 'compact' ? 'min-w-[140px]' : 'min-w-[190px]'} rounded-lg border bg-surface-1 shadow-raised px-3 py-2.5 transition-shadow
		{status === 'failed' ? 'border-danger ring-2 ring-danger/60' : selected ? 'border-signal ring-2 ring-signal/60' : 'border-line'}"
>
	<div class="flex items-center justify-between gap-2 mb-1">
		<span
			class="inline-flex items-center gap-1.5 text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle"
		>
			<Icon name={nodeTypeDef?.icon || 'cube'} className="w-3.5 h-3.5" />
			{kindLabel}
		</span>
		<RunStatusBadge {status} animated />
	</div>
	<p class="text-sm font-medium text-fg truncate">{nodeTypeDef?.title ?? data.nodeType}</p>
	{#if nodeTypeDef?.category}
		<p class="text-2xs text-fg-subtle truncate">{nodeTypeDef.category}</p>
	{/if}

	{#if $nodeDetail === 'full'}
		<!-- What this node consumes from upstream. Omitted entirely when it
		     consumes nothing (e.g. Wait For GPU) — that's the useful signal. -->
		{#if dataInputs.length > 0}
			<div class="mt-2 pt-2 border-t border-line">
				<p class="text-2xs font-mono uppercase tracking-wide text-fg-subtle mb-1">Accepts</p>
				<div class="flex flex-col gap-0.5">
					{#each dataInputs as input (input.name)}
						<div class="flex items-baseline justify-between gap-2 text-2xs">
							<span class="text-fg-muted truncate flex-shrink-0">{input.name}</span>
							{#if input.value}
								<span
									class="font-mono truncate text-right max-w-[130px] {input.bound
										? 'text-signal'
										: 'text-fg-subtle'}"
									title={input.value}
								>
									{input.value}
								</span>
							{:else}
								<span class="font-mono text-fg-subtle/70 italic flex-shrink-0">{input.hint}</span>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/if}

		<!-- What downstream nodes can collect, under the namespace they'd
		     actually reference it through. -->
		{#if hasOutputs}
			<div class="mt-2 pt-2 border-t border-line">
				<div class="flex items-baseline justify-between gap-2 mb-1">
					<p class="text-2xs font-mono uppercase tracking-wide text-fg-subtle">Provides</p>
					<code
						class="text-2xs font-mono text-fg-subtle truncate max-w-[120px]"
						title="Reference these downstream as {prefix}.<field>"
					>
						{prefix}.*
					</code>
				</div>
				<div class="flex flex-col gap-0.5">
					{#if nodeTypeDef?.dynamic_outputs}
						<p class="text-2xs text-fg-subtle italic">
							runtime-defined — depends on the event
						</p>
					{:else}
						{#each outputs as output (output.key)}
							<div
								class="flex items-baseline justify-between gap-2 text-2xs"
								title={outputRef(data.kind, id, output.key)}
							>
								<span class="font-mono text-fg truncate">{output.key}</span>
								<span class="font-mono text-fg-subtle tabular-nums flex-shrink-0">{output.type}</span>
							</div>
						{/each}
					{/if}
				</div>
			</div>
		{/if}
	{/if}

	{#if sourceHandles.length > 1}
		<div class="mt-2 flex flex-col gap-1 text-2xs font-mono uppercase tracking-wide text-fg-subtle">
			{#each sourceHandles as handle (handle.id)}
				<div class="relative flex items-center justify-end gap-1 h-4 pr-2">
					<span class="truncate max-w-[130px]">{handle.label}</span>
					<Handle
						type="source"
						position={Position.Right}
						id={handle.id}
						style="position: absolute; right: -13px; top: 50%; transform: translateY(-50%);"
					/>
				</div>
			{/each}
		</div>
	{/if}

	{#if showTarget}
		<Handle type="target" position={Position.Left} id="in" />
	{/if}
	{#if sourceHandles.length === 1}
		<Handle type="source" position={Position.Right} id={sourceHandles[0].id} />
	{/if}
</div>
