<script lang="ts">
	import JsonTreeView from '../../JsonTreeView.svelte';
	import { CopyButton } from '$lib/components/ui';

	export let artifact: {
		artifact_data: {
			node_count?: number;
			workflow_file?: string;
			workflow?: any;
		};
	};

	function downloadWorkflow() {
		const blob = new Blob([JSON.stringify(artifact.artifact_data.workflow, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'comfyui_workflow.json';
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="space-y-3">
	<!-- Workflow header -->
	<div class="flex items-center justify-between gap-2 p-3 bg-warning/10 border border-warning/25">
		<div class="flex items-center gap-2">
			<div class="p-1 bg-warning-solid">
				<svg class="w-3.5 h-3.5 text-fg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z" />
				</svg>
			</div>
			<span class="text-sm font-semibold text-fg-muted">ComfyUI Workflow</span>
			{#if artifact.artifact_data.node_count}
				<span class="px-2 py-0.5 bg-warning/10 text-warning text-xs font-mono tabular-nums font-medium rounded">
					{artifact.artifact_data.node_count} nodes
				</span>
			{/if}
		</div>
		<div class="flex items-center gap-2">
			<!-- Copy to clipboard button -->
			<CopyButton
				text={() => JSON.stringify(artifact.artifact_data.workflow, null, 2)}
				ariaLabel="Copy workflow JSON to clipboard"
				class="text-warning hover:bg-warning/10"
			/>
			<!-- Download button -->
			<button
				class="p-1.5 hover:bg-warning/10 transition-colors rounded"
				on:click={downloadWorkflow}
				title="Download workflow as JSON file"
			>
				<svg class="w-4 h-4 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Source file info -->
	{#if artifact.artifact_data.workflow_file}
		<div class="text-xs text-fg-subtle px-2">
			Source: <span class="font-mono">{artifact.artifact_data.workflow_file}</span>
		</div>
	{/if}

	<!-- Workflow JSON tree -->
	{#if artifact.artifact_data.workflow}
		<JsonTreeView data={artifact.artifact_data.workflow} initialExpandLevel={1} maxHeight="400px" />
	{:else}
		<div class="p-4 text-center text-fg-subtle">
			<p class="text-sm">Workflow data not available</p>
		</div>
	{/if}
</div>
