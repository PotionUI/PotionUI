<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, Spinner } from '$lib/components/ui';
	import { getErrorMessage, logger } from '$lib/utils/logger';

	export let loader: () => Promise<{ default: any }>;
	export let componentProps: Record<string, unknown> = {};

	let Component: any = null;
	let detail: string | null = null;
	let retrying = false;

	async function load() {
		retrying = true;
		detail = null;
		try {
			Component = (await loader()).default;
		} catch (e) {
			logger.error('Admin tool failed to load', e);
			detail = getErrorMessage(e, 'The module could not be fetched.');
		} finally {
			retrying = false;
		}
	}

	onMount(load);
</script>

{#if Component}
	<svelte:component this={Component} {...componentProps} />
{:else if detail}
	<div class="flex min-h-48 flex-col items-center justify-center gap-3 px-4 text-center">
		<p class="text-sm text-danger">Unable to load this admin tool.</p>
		<p class="max-w-xl break-words font-mono text-xs text-fg-subtle">{detail}</p>
		<Button variant="secondary" size="sm" loading={retrying} onclick={load}>Retry</Button>
	</div>
{:else}
	<div class="flex min-h-48 items-center justify-center"><Spinner size="lg" /></div>
{/if}
