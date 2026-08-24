<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { SvelteFlowProvider } from '@xyflow/svelte';
	import { Spinner } from '$lib/components/ui';
	import { automationEditor } from '$lib/stores/automationEditor';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { automationRuns } from '$lib/stores/automationRuns';
	import { automationRunsWebSocket } from '$lib/services/automationRunsWebsocket';
	import Toolbar from './components/Toolbar.svelte';
	import NodePalette from './components/NodePalette.svelte';
	import Canvas from './components/Canvas.svelte';
	import Inspector from './components/Inspector.svelte';
	import RunHistoryPanel from './components/RunHistoryPanel.svelte';
	import ValidationErrors from './components/ValidationErrors.svelte';

	let automationId = $derived($page.params.id ?? '');
	let editorState = $derived($automationEditor);

	// Admin-only: the automation editor lives under the Admin section.
	$effect(() => {
		const auth = $authStore;
		if (auth.loading) return;
		if (!auth.isAuthenticated) {
			goto('/login');
		} else if (auth.user && auth.user.account_type !== 'ADMIN') {
			goto('/generate');
		}
	});

	onMount(async () => {
		if (!automationId) return;
		await Promise.all([
			automationNodeTypes.load(),
			automationEditor.load(automationId),
			automationRuns.loadRuns(automationId)
		]);
		automationRunsWebSocket.connect();
	});

	onDestroy(() => {
		automationRunsWebSocket.disconnect();
		automationEditor.reset();
		automationRuns.reset();
	});
</script>

<svelte:head>
	<title>{editorState.automation?.name ?? 'Automation'} · PotionUI</title>
</svelte:head>

{#if !editorState.loaded}
	<div class="flex items-center justify-center h-full py-16">
		<Spinner />
	</div>
{:else if editorState.error}
	<div class="flex items-center justify-center h-full py-16">
		<p class="text-sm text-danger">{editorState.error}</p>
	</div>
{:else}
	<SvelteFlowProvider>
		<div class="h-screen flex flex-col bg-canvas overflow-hidden">
			<Toolbar />
			<div class="flex flex-1 min-h-0">
				<NodePalette />
				<Canvas />
				<Inspector />
				<RunHistoryPanel {automationId} />
			</div>
			<ValidationErrors issues={editorState.validationIssues} />
		</div>
	</SvelteFlowProvider>
{/if}
