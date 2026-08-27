<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Badge } from '$lib/components/ui';
	import { automationEditor, buildVariableScope, selectedNode } from '$lib/stores/automationEditor';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { getSourceHandles } from '$lib/automations/nodeHandles';
	import { outputRef } from '$lib/automations/nodeData';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';
	import NodeConfigForm from './NodeConfigForm.svelte';

	/** The reference most recently copied, to flash "copied" on that row. */
	let copied = $state<string | null>(null);
	let copiedTimer: ReturnType<typeof setTimeout> | undefined;

	async function copyReference(reference: string) {
		const ok = await copyText(reference);
		if (ok) {
			copied = reference;
			clearTimeout(copiedTimer);
			copiedTimer = setTimeout(() => (copied = null), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}

	let node = $derived($selectedNode);
	let nodeTypeDef = $derived(
		node ? $automationNodeTypes.find((t) => t.key === node!.data.nodeType) : undefined
	);

	/** Flow-edge handles, shared with the canvas node via `nodeHandles.ts` so the
	 *  two can't disagree as a switch node's `cases` config is edited. */
	let sourceHandles = $derived(
		nodeTypeDef ? getSourceHandles(nodeTypeDef, node?.data.config) : []
	);

	/** What this node can reference — its ancestors' declared outputs. */
	let scope = $derived(
		node ? buildVariableScope($automationEditor.nodes, $automationEditor.edges, node.id, $automationNodeTypes) : undefined
	);

	function handleConfigChange(config: Record<string, any>) {
		if (node) automationEditor.updateNodeConfig(node.id, config);
	}

	function handleDelete() {
		if (node) automationEditor.removeNode(node.id);
	}
</script>

<aside class="w-80 flex-shrink-0 bg-surface-1 border-l border-line flex flex-col overflow-hidden">
	{#if !node}
		<div class="flex-1 flex items-center justify-center p-6 text-center">
			<p class="text-sm text-fg-subtle">Select a node to configure it.</p>
		</div>
	{:else}
		<div class="h-header flex items-center justify-between gap-2 px-4 border-b border-line flex-shrink-0">
			<div class="min-w-0">
				<p class="text-2xs font-mono uppercase tracking-wide text-fg-subtle">{node.data.kind}</p>
				<h2 class="text-sm font-semibold text-fg truncate">
					{nodeTypeDef?.title ?? node.data.nodeType}
				</h2>
			</div>
			<IconButton icon="trash" label="Delete node" onclick={handleDelete} />
		</div>

		<div class="flex-1 overflow-y-auto p-4 space-y-4">
			{#if nodeTypeDef?.description}
				<p class="text-xs text-fg-muted">{nodeTypeDef.description}</p>
			{/if}

			<div class="flex flex-wrap items-center gap-2">
				<Badge variant="neutral">
					<span class="font-mono">{node.data.nodeType}</span>
				</Badge>
				{#each sourceHandles as handle (handle.id)}
					<Badge variant="signal">
						<span class="font-mono uppercase">{handle.label}</span>
					</Badge>
				{/each}
			</div>

			{#if nodeTypeDef}
				<NodeConfigForm
					schema={nodeTypeDef.config_schema}
					value={node.data.config}
					onChange={handleConfigChange}
					{scope}
				/>

				<!-- The data contract: what downstream nodes may read from this one. -->
				<section class="pt-2 border-t border-line">
					<h3 class="text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle mb-2">
						Outputs
					</h3>

					{#if nodeTypeDef.dynamic_outputs}
						<p class="text-2xs text-fg-subtle">
							Defined at runtime — this node's payload depends on
							{node.data.kind === 'trigger' ? 'the event it fires' : 'its input'}.
						</p>
					{:else if (nodeTypeDef.outputs ?? []).length === 0}
						<p class="text-2xs text-fg-subtle">This node emits no data.</p>
					{:else}
						<div class="space-y-1">
							{#each nodeTypeDef.outputs ?? [] as output (output.key)}
								{@const reference = outputRef(node.data.kind, node.id, output.key)}
								<button
									type="button"
									class="w-full text-left rounded px-2 py-1 -mx-2 hover:bg-surface-2 transition-colors"
									title="Copy “{reference}”"
									onclick={() => copyReference(reference)}
								>
									<span class="flex items-baseline justify-between gap-2">
										<span class="font-mono text-xs text-fg truncate">{output.key}</span>
										<span class="font-mono text-2xs text-fg-subtle tabular-nums flex-shrink-0">
											{copied === reference ? 'copied' : output.type}
										</span>
									</span>
									{#if output.description}
										<span class="block text-2xs text-fg-subtle">{output.description}</span>
									{/if}
									<code class="block text-2xs font-mono text-fg-subtle/80 truncate mt-0.5">
										{reference}
									</code>
								</button>
							{/each}
						</div>
						<p class="mt-2 text-2xs text-fg-subtle">
							Click a field to copy its reference. Downstream, wrap it in
							<code class="font-mono text-fg-muted">&lbrace;&lbrace; &rbrace;&rbrace;</code>
							inside an action field; paste it bare into a condition's field.
						</p>
					{/if}
				</section>
			{:else}
				<div class="flex items-center gap-2 text-xs text-warning">
					<Icon name="warning" className="w-4 h-4 flex-shrink-0" />
					<span>Unknown node type — its catalog entry is unavailable.</span>
				</div>
			{/if}
		</div>
	{/if}
</aside>
